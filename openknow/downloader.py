"""File download functionality for OpenKnow agent.

Handles downloading files from OneDrive and SharePoint to local storage,
with support for resumable downloads and progress reporting.
"""

import os
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable, List, Optional

import requests

from .config import get_download_dir
from .graph_client import GraphClient
from .workspace import list_links, record_file_sync


class DownloadError(Exception):
    """Raised when a file download fails."""


def download_file(
    url: str,
    dest_path: Path,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Download a file from a URL to a local destination path.

    Args:
        url: The URL to download from.
        dest_path: The local path to save the file.
        progress_callback: Optional callback receiving (bytes_downloaded, total_bytes).

    Returns:
        The path where the file was saved.

    Raises:
        DownloadError: If the download fails.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise DownloadError(f"Failed to download '{url}': {exc}") from exc

    total_size = int(response.headers.get("Content-Length", 0))
    downloaded = 0

    try:
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total_size)
    except OSError as exc:
        raise DownloadError(f"Failed to write file '{dest_path}': {exc}") from exc

    return dest_path


def sync_workspace(
    workspace_name: str,
    client: GraphClient,
    download_dir: Optional[Path] = None,
    file_filter: Optional[str] = None,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
    db_path: Optional[Path] = None,
) -> List[dict]:
    """Sync all files from a workspace's OneDrive/SharePoint links to local storage.

    Args:
        workspace_name: Name of the workspace to sync.
        client: Authenticated GraphClient instance.
        download_dir: Base directory for downloads (uses default if None).
        file_filter: Optional glob-style filter (e.g. '*.pdf'). Filters by filename.
        progress_callback: Optional callback receiving (filename, bytes_done, total_bytes).
        db_path: Path to the SQLite database (uses default if None).

    Returns:
        List of result dicts with keys: file, local_path, status, error.
    """
    base_dir = download_dir or get_download_dir()
    workspace_dir = base_dir / workspace_name
    workspace_dir.mkdir(parents=True, exist_ok=True)

    links = list_links(workspace_name, db_path)
    results = []

    for link in links:
        link_id = link["id"]
        url = link["url"]
        link_label = link.get("label") or f"link_{link_id}"
        link_dir = workspace_dir / _safe_dirname(link_label or str(link_id))

        try:
            files = client.scan_share_url(url)
        except Exception as exc:
            results.append(
                {
                    "file": url,
                    "local_path": None,
                    "status": "error",
                    "error": f"Failed to scan '{url}': {exc}",
                }
            )
            continue

        for file_info in files:
            remote_path = file_info["path"]
            filename = file_info["name"]

            # Apply file filter if specified
            if file_filter and not _match_filter(filename, file_filter):
                continue

            local_path = link_dir / remote_path.replace("/", os.sep if _is_windows() else "/")

            # Use pre-authenticated download URL if available, otherwise fetch it
            download_url = file_info.get("download_url")
            if not download_url:
                try:
                    download_url = client.get_download_url(
                        file_info["drive_id"], file_info["item_id"]
                    )
                except Exception as exc:
                    results.append(
                        {
                            "file": remote_path,
                            "local_path": None,
                            "status": "error",
                            "error": str(exc),
                        }
                    )
                    continue

            cb = _make_progress_cb(filename, progress_callback)

            try:
                download_file(download_url, local_path, cb)
                record_file_sync(
                    workspace_name=workspace_name,
                    link_id=link_id,
                    remote_path=remote_path,
                    local_path=str(local_path),
                    file_size=file_info.get("size", 0),
                    last_modified=file_info.get("last_modified"),
                    db_path=db_path,
                )
                results.append(
                    {
                        "file": remote_path,
                        "local_path": str(local_path),
                        "status": "ok",
                        "error": None,
                    }
                )
            except DownloadError as exc:
                results.append(
                    {
                        "file": remote_path,
                        "local_path": None,
                        "status": "error",
                        "error": str(exc),
                    }
                )

    return results


def _make_progress_cb(
    filename: str,
    progress_callback: Optional[Callable[[str, int, int], None]],
) -> Optional[Callable[[int, int], None]]:
    """Create a progress callback bound to a specific filename.

    Returns None if no progress_callback is provided, so callers can pass
    the result directly to download_file without further branching.
    """
    if progress_callback is None:
        return None
    captured = filename

    def cb(done: int, total: int) -> None:
        progress_callback(captured, done, total)

    return cb


def _safe_dirname(name: str) -> str:
    """Convert a string to a safe directory name."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return safe[:64] or "default"


def _match_filter(filename: str, pattern: str) -> bool:
    """Check if filename matches a glob-style pattern."""
    return fnmatch(filename.lower(), pattern.lower())


def _is_windows() -> bool:
    """Return True if running on Windows."""
    return sys.platform == "win32"
