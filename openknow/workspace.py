"""Workspace management with local SQLite memory for OpenKnow agent.

Each workspace stores 1-5 OneDrive or SharePoint share links, providing
isolated knowledge spaces for different projects or contexts.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, List, Optional
from urllib.parse import urlparse

from .config import get_db_path

# Maximum number of links allowed per workspace
MAX_LINKS_PER_WORKSPACE = 5


class WorkspaceError(Exception):
    """Raised when a workspace operation fails."""


@contextmanager
def _get_connection(db_path: Optional[Path] = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections."""
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_workspace_links_if_needed(conn: sqlite3.Connection) -> None:
    """Migrate the workspace_links table to support new link types if needed.

    Versions of OpenKnow prior to the plugin system only allowed
    ``('onedrive', 'sharepoint', 'unknown')`` as link_type values.  This
    helper detects that old constraint and recreates the table with an
    extended set that also includes ``'folder'`` and ``'url'``.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='workspace_links'"
    ).fetchone()

    if row is None:
        # Table doesn't exist yet — will be created fresh below.
        return

    create_sql = row[0] or ""
    if "'folder'" in create_sql and "'url'" in create_sql:
        # Already up-to-date — nothing to do.
        return

    # Recreate table with an extended CHECK constraint.
    conn.executescript(
        """
        CREATE TABLE workspace_links_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            label TEXT DEFAULT '',
            link_type TEXT NOT NULL CHECK(link_type IN ('onedrive', 'sharepoint', 'folder', 'url', 'unknown')),
            added_at TEXT NOT NULL,
            UNIQUE(workspace_id, url)
        );
        INSERT INTO workspace_links_new SELECT * FROM workspace_links;
        DROP TABLE workspace_links;
        ALTER TABLE workspace_links_new RENAME TO workspace_links;
        """
    )


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize the database schema, running any necessary migrations."""
    with _get_connection(db_path) as conn:
        # Run migration before creating tables so the correct schema is used
        # when upgrading an existing database.
        _migrate_workspace_links_if_needed(conn)

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workspace_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                url TEXT NOT NULL,
                label TEXT DEFAULT '',
                link_type TEXT NOT NULL CHECK(link_type IN ('onedrive', 'sharepoint', 'folder', 'url', 'unknown')),
                added_at TEXT NOT NULL,
                UNIQUE(workspace_id, url)
            );

            CREATE TABLE IF NOT EXISTS file_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                link_id INTEGER NOT NULL REFERENCES workspace_links(id) ON DELETE CASCADE,
                remote_path TEXT NOT NULL,
                local_path TEXT NOT NULL,
                file_size INTEGER DEFAULT 0,
                last_modified TEXT,
                synced_at TEXT NOT NULL,
                UNIQUE(link_id, remote_path)
            );
            """
        )


def _detect_link_type(url: str) -> str:
    """Detect whether a URL is a local folder, OneDrive, SharePoint, generic URL, or unknown.

    Detection rules (first match wins):

    1. Paths that start with ``/``, ``~``, ``./``, ``../`` are local folders.
    2. Windows drive-letter paths (``C:\\`` / ``C:/``) are local folders.
    3. ``file://`` URLs are local folders.
    4. ``*.sharepoint.com`` hosts are SharePoint links.
    5. ``1drv.ms``, ``onedrive.live.com``, ``onedrive.com`` hosts are OneDrive links.
    6. Any other ``http://`` or ``https://`` URL is a generic ``url`` link.
    7. Everything else is ``unknown``.
    """
    # --- Local path heuristics ---
    if url.startswith(("/", "~", "./", "../")):
        return "folder"
    # Windows drive letter e.g. C:\ or C:/.
    if len(url) >= 3 and url[1] == ":" and url[2] in ("/", "\\"):
        return "folder"

    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()

        if scheme == "file":
            return "folder"

        host = (parsed.hostname or "").lower()

        if host == "sharepoint.com" or host.endswith(".sharepoint.com"):
            return "sharepoint"
        if (
            host == "onedrive.live.com"
            or host.endswith(".onedrive.live.com")
            or host == "1drv.ms"
            or host.endswith(".1drv.ms")
            or host == "onedrive.com"
            or host.endswith(".onedrive.com")
        ):
            return "onedrive"
        if scheme in ("http", "https"):
            return "url"
    except Exception:
        pass
    return "unknown"


def create_workspace(name: str, description: str = "", db_path: Optional[Path] = None) -> int:
    """Create a new workspace and return its ID.

    Args:
        name: Unique workspace name.
        description: Optional description.
        db_path: Path to the SQLite database (uses default if None).

    Returns:
        The ID of the newly created workspace.

    Raises:
        WorkspaceError: If a workspace with that name already exists.
    """
    now = datetime.now(timezone.utc).isoformat()
    with _get_connection(db_path) as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO workspaces (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (name, description, now, now),
            )
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            raise WorkspaceError(f"Workspace '{name}' already exists.")


def delete_workspace(name: str, db_path: Optional[Path] = None) -> None:
    """Delete a workspace and all its associated data.

    Args:
        name: Workspace name.
        db_path: Path to the SQLite database (uses default if None).

    Raises:
        WorkspaceError: If the workspace does not exist.
    """
    with _get_connection(db_path) as conn:
        cursor = conn.execute("DELETE FROM workspaces WHERE name = ?", (name,))
        if cursor.rowcount == 0:
            raise WorkspaceError(f"Workspace '{name}' does not exist.")


def list_workspaces(db_path: Optional[Path] = None) -> List[dict]:
    """Return all workspaces with their link counts.

    Args:
        db_path: Path to the SQLite database (uses default if None).

    Returns:
        List of workspace dicts with keys: id, name, description, created_at, updated_at, link_count.
    """
    with _get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT w.id, w.name, w.description, w.created_at, w.updated_at,
                   COUNT(wl.id) as link_count
            FROM workspaces w
            LEFT JOIN workspace_links wl ON wl.workspace_id = w.id
            GROUP BY w.id
            ORDER BY w.name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_workspace(name: str, db_path: Optional[Path] = None) -> dict:
    """Return workspace details by name.

    Args:
        name: Workspace name.
        db_path: Path to the SQLite database (uses default if None).

    Raises:
        WorkspaceError: If the workspace does not exist.
    """
    with _get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT w.id, w.name, w.description, w.created_at, w.updated_at,
                   COUNT(wl.id) as link_count
            FROM workspaces w
            LEFT JOIN workspace_links wl ON wl.workspace_id = w.id
            WHERE w.name = ?
            GROUP BY w.id
            """,
            (name,),
        ).fetchone()
    if row is None:
        raise WorkspaceError(f"Workspace '{name}' does not exist.")
    return dict(row)


def add_link(
    workspace_name: str,
    url: str,
    label: str = "",
    db_path: Optional[Path] = None,
) -> int:
    """Add a OneDrive or SharePoint link to a workspace.

    Args:
        workspace_name: Name of the workspace.
        url: OneDrive or SharePoint share URL.
        label: Optional human-readable label for the link.
        db_path: Path to the SQLite database (uses default if None).

    Returns:
        The ID of the newly added link.

    Raises:
        WorkspaceError: If workspace doesn't exist, link limit exceeded, or URL duplicate.
    """
    workspace = get_workspace(workspace_name, db_path)
    workspace_id = workspace["id"]
    link_count = workspace["link_count"]

    if link_count >= MAX_LINKS_PER_WORKSPACE:
        raise WorkspaceError(
            f"Workspace '{workspace_name}' already has {MAX_LINKS_PER_WORKSPACE} links "
            f"(maximum allowed). Remove a link before adding a new one."
        )

    link_type = _detect_link_type(url)
    now = datetime.now(timezone.utc).isoformat()

    with _get_connection(db_path) as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO workspace_links (workspace_id, url, label, link_type, added_at) VALUES (?, ?, ?, ?, ?)",
                (workspace_id, url, label, link_type, now),
            )
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            raise WorkspaceError(f"URL '{url}' is already added to workspace '{workspace_name}'.")


def remove_link(workspace_name: str, link_id: int, db_path: Optional[Path] = None) -> None:
    """Remove a link from a workspace.

    Args:
        workspace_name: Name of the workspace.
        link_id: ID of the link to remove.
        db_path: Path to the SQLite database (uses default if None).

    Raises:
        WorkspaceError: If workspace or link does not exist.
    """
    workspace = get_workspace(workspace_name, db_path)
    with _get_connection(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM workspace_links WHERE id = ? AND workspace_id = ?",
            (link_id, workspace["id"]),
        )
        if cursor.rowcount == 0:
            raise WorkspaceError(
                f"Link ID {link_id} not found in workspace '{workspace_name}'."
            )


def list_links(workspace_name: str, db_path: Optional[Path] = None) -> List[dict]:
    """Return all links for a workspace.

    Args:
        workspace_name: Name of the workspace.
        db_path: Path to the SQLite database (uses default if None).

    Returns:
        List of link dicts with keys: id, url, label, link_type, added_at.
    """
    workspace = get_workspace(workspace_name, db_path)
    with _get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, url, label, link_type, added_at FROM workspace_links WHERE workspace_id = ? ORDER BY added_at",
            (workspace["id"],),
        ).fetchall()
    return [dict(row) for row in rows]


def record_file_sync(
    workspace_name: str,
    link_id: int,
    remote_path: str,
    local_path: str,
    file_size: int = 0,
    last_modified: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> None:
    """Record a synced file in the local cache.

    Args:
        workspace_name: Name of the workspace.
        link_id: ID of the link this file came from.
        remote_path: Remote path of the file.
        local_path: Local path where the file was saved.
        file_size: Size of the file in bytes.
        last_modified: Last modified timestamp from the remote.
        db_path: Path to the SQLite database (uses default if None).
    """
    workspace = get_workspace(workspace_name, db_path)
    now = datetime.now(timezone.utc).isoformat()
    with _get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO file_cache (workspace_id, link_id, remote_path, local_path, file_size, last_modified, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(link_id, remote_path) DO UPDATE SET
                local_path = excluded.local_path,
                file_size = excluded.file_size,
                last_modified = excluded.last_modified,
                synced_at = excluded.synced_at
            """,
            (workspace["id"], link_id, remote_path, local_path, file_size, last_modified, now),
        )


def list_cached_files(workspace_name: str, db_path: Optional[Path] = None) -> List[dict]:
    """Return all cached (downloaded) files for a workspace.

    Args:
        workspace_name: Name of the workspace.
        db_path: Path to the SQLite database (uses default if None).

    Returns:
        List of file cache dicts.
    """
    workspace = get_workspace(workspace_name, db_path)
    with _get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT fc.id, fc.remote_path, fc.local_path, fc.file_size, fc.last_modified, fc.synced_at,
                   wl.url as source_url, wl.label as source_label
            FROM file_cache fc
            JOIN workspace_links wl ON wl.id = fc.link_id
            WHERE fc.workspace_id = ?
            ORDER BY fc.remote_path
            """,
            (workspace["id"],),
        ).fetchall()
    return [dict(row) for row in rows]
