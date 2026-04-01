"""File download functionality for OpenKnow agent.

Handles downloading files from OneDrive and SharePoint to local storage
with progress reporting, file filtering, and opencode CLI indexing.
"""

import os
import shutil
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Callable, List, Optional
from urllib.parse import urlparse

import requests

from .config import get_download_dir
from .graph_client import AuthError, GraphError, OneDriveClient, SharePointClient
from .workspace import list_links, record_file_sync


class DownloadError(Exception):
    """Raised when a file download fails."""


def _sanitize_remote_path(remote_path: str, base_dir: Path) -> Path:
    """Resolve a remote path to a safe local path under base_dir.

    Prevents path traversal attacks (e.g. remote paths containing ``..``
    or absolute path components). All path segments are resolved relative
    to *base_dir* and the result is validated to remain within it.

    Args:
        remote_path: Remote file path (may use forward slashes).
        base_dir: The directory all downloads must be constrained to.

    Returns:
        Absolute local Path safely inside base_dir.

    Raises:
        DownloadError: If the resolved path would escape base_dir.
    """
    try:
        pure = PurePosixPath(remote_path)
        # Reject absolute paths and any traversal segments
        if pure.is_absolute():
            raise DownloadError(f"Rejected absolute remote path: {remote_path!r}")

        # Filter out any '..' segments and empty parts
        safe_parts = []
        for part in pure.parts:
            if part in ("..", ".", "") or part.startswith("/"):
                continue
            safe_parts.append(part)

        if not safe_parts:
            raise DownloadError(f"Remote path resolved to empty after sanitization: {remote_path!r}")

        local_path = base_dir.joinpath(*safe_parts).resolve()
        # Ensure the resolved path is inside base_dir
        base_resolved = base_dir.resolve()
        if not str(local_path).startswith(str(base_resolved)):
            raise DownloadError(
                f"Remote path {remote_path!r} attempts to escape the download directory."
            )
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(f"Failed to sanitize remote path {remote_path!r}: {exc}") from exc

    return local_path


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


def index_with_opencode(file_paths: List[Path], workspace_name: str) -> dict:
    """Index downloaded files into opencode as knowledge.

    Runs ``opencode`` as a subprocess to add the downloaded files to the
    local AI knowledge base so they can be queried via the chat UI.

    Args:
        file_paths: List of local file paths to index.
        workspace_name: Name of the workspace (used as opencode context label).

    Returns:
        Dict with keys: indexed (list of paths), errors (list of error strings).
    """
    indexed = []
    errors = []

    # Check opencode is available
    opencode_bin = _find_opencode()
    if not opencode_bin:
        errors.append(
            "opencode is not installed or not on PATH. "
            "Files were downloaded but not indexed. "
            "Install opencode from https://opencode.ai and re-run 'openknow sync'."
        )
        return {"indexed": indexed, "errors": errors}

    for path in file_paths:
        if not path.exists():
            errors.append(f"File not found for indexing: {path}")
            continue
        try:
            # opencode add <file> --context <workspace>
            result = subprocess.run(
                [opencode_bin, "add", str(path), "--context", workspace_name],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                indexed.append(str(path))
            else:
                errors.append(
                    f"opencode failed to index {path.name}: {result.stderr.strip() or result.stdout.strip()}"
                )
        except subprocess.TimeoutExpired:
            errors.append(f"opencode timed out indexing {path.name}")
        except OSError as exc:
            errors.append(f"Failed to run opencode for {path.name}: {exc}")

    return {"indexed": indexed, "errors": errors}


def _find_opencode() -> Optional[str]:
    """Return the path to the opencode binary, or None if not found."""
    return shutil.which("opencode")


def sync_workspace(
    workspace_name: str,
    download_dir: Optional[Path] = None,
    file_filter: Optional[str] = None,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
    db_path: Optional[Path] = None,
    opencode_index: bool = True,
) -> List[dict]:
    """Sync all files from a workspace's OneDrive/SharePoint links to local storage.

    After downloading, optionally indexes files with opencode for knowledge retrieval.

    Args:
        workspace_name: Name of the workspace to sync.
        download_dir: Base directory for downloads (uses default if None).
        file_filter: Optional glob-style filter (e.g. '*.pdf'). Filters by filename.
        progress_callback: Optional callback receiving (filename, bytes_done, total_bytes).
        db_path: Path to the SQLite database (uses default if None).
        opencode_index: Whether to index downloaded files with opencode (default True).

    Returns:
        List of result dicts with keys: file, local_path, status, error.
    """
    base_dir = download_dir or get_download_dir()
    workspace_dir = base_dir / workspace_name
    workspace_dir.mkdir(parents=True, exist_ok=True)

    links = list_links(workspace_name, db_path)
    results = []
    downloaded_paths = []

    # Instantiate clients once (credentials loaded from config)
    onedrive_client: Optional[OneDriveClient] = None
    sharepoint_client: Optional[SharePointClient] = None

    for link in links:
        link_id = link["id"]
        url = link["url"]
        link_label = link.get("label") or f"link_{link_id}"
        link_type = link.get("link_type", "unknown")
        link_dir = workspace_dir / _safe_dirname(link_label)

        if link_type == "sharepoint":
            results += _sync_sharepoint_link(
                url=url,
                link_id=link_id,
                link_dir=link_dir,
                workspace_name=workspace_name,
                file_filter=file_filter,
                progress_callback=progress_callback,
                downloaded_paths=downloaded_paths,
                db_path=db_path,
            )
        else:
            # onedrive or unknown — try OneDrive client
            results += _sync_onedrive_link(
                url=url,
                link_id=link_id,
                link_dir=link_dir,
                workspace_name=workspace_name,
                file_filter=file_filter,
                progress_callback=progress_callback,
                downloaded_paths=downloaded_paths,
                db_path=db_path,
            )

    # Index newly downloaded files with opencode
    if opencode_index and downloaded_paths:
        index_result = index_with_opencode(downloaded_paths, workspace_name)
        for err in index_result["errors"]:
            results.append({"file": "(opencode)", "local_path": None, "status": "warning", "error": err})

    return results


def _sync_onedrive_link(
    url: str,
    link_id: int,
    link_dir: Path,
    workspace_name: str,
    file_filter: Optional[str],
    progress_callback: Optional[Callable],
    downloaded_paths: list,
    db_path: Optional[Path],
) -> List[dict]:
    """Sync a single OneDrive share link. Returns list of result dicts."""
    results = []
    try:
        client = OneDriveClient()
        file_infos = client.list_folder_items(url)
    except GraphError:
        # Might be a direct file link rather than a folder — try downloading directly
        file_infos = [
            {
                "name": url.rstrip("/").split("/")[-1].split("?")[0] or "download",
                "path": url.rstrip("/").split("/")[-1].split("?")[0] or "download",
                "download_url": url,
                "size": 0,
                "last_modified": None,
                "mime_type": "",
            }
        ]
    except Exception as exc:
        return [
            {
                "file": url,
                "local_path": None,
                "status": "error",
                "error": f"Failed to list OneDrive link: {exc}",
            }
        ]

    for file_info in file_infos:
        filename = file_info["name"]
        if file_filter and not _match_filter(filename, file_filter):
            continue

        remote_path = file_info.get("path", filename)
        try:
            local_path = _sanitize_remote_path(remote_path, link_dir)
        except DownloadError as exc:
            results.append({"file": remote_path, "local_path": None, "status": "error", "error": str(exc)})
            continue

        download_url = file_info.get("download_url", "")
        if not download_url:
            results.append(
                {"file": remote_path, "local_path": None, "status": "error", "error": "No download URL"}
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
            downloaded_paths.append(local_path)
            results.append({"file": remote_path, "local_path": str(local_path), "status": "ok", "error": None})
        except (DownloadError, AuthError) as exc:
            results.append({"file": remote_path, "local_path": None, "status": "error", "error": str(exc)})

    return results


def _sync_sharepoint_link(
    url: str,
    link_id: int,
    link_dir: Path,
    workspace_name: str,
    file_filter: Optional[str],
    progress_callback: Optional[Callable],
    downloaded_paths: list,
    db_path: Optional[Path],
) -> List[dict]:
    """Sync a single SharePoint link. Returns list of result dicts."""
    results = []
    try:
        client = SharePointClient()
        # Derive site URL and folder from the full URL
        site_url, folder_relative_url = _parse_sharepoint_url(url)
        file_infos = client.list_folder_files(site_url, folder_relative_url)
    except Exception as exc:
        return [
            {
                "file": url,
                "local_path": None,
                "status": "error",
                "error": f"Failed to list SharePoint folder: {exc}",
            }
        ]

    for file_info in file_infos:
        filename = file_info["name"]
        if file_filter and not _match_filter(filename, file_filter):
            continue

        remote_path = file_info.get("path", filename)
        try:
            local_path = _sanitize_remote_path(remote_path, link_dir)
        except DownloadError as exc:
            results.append({"file": remote_path, "local_path": None, "status": "error", "error": str(exc)})
            continue

        server_relative_url = file_info.get("server_relative_url", "")
        site_url_for_dl, _ = _parse_sharepoint_url(url)

        if progress_callback:
            progress_callback(filename, 0, file_info.get("size", 0))

        try:
            client.download_file(site_url_for_dl, server_relative_url, local_path)
            record_file_sync(
                workspace_name=workspace_name,
                link_id=link_id,
                remote_path=remote_path,
                local_path=str(local_path),
                file_size=file_info.get("size", 0),
                last_modified=file_info.get("last_modified"),
                db_path=db_path,
            )
            downloaded_paths.append(local_path)
            results.append({"file": remote_path, "local_path": str(local_path), "status": "ok", "error": None})
        except Exception as exc:
            results.append({"file": remote_path, "local_path": None, "status": "error", "error": str(exc)})

    return results


def _parse_sharepoint_url(url: str):
    """Split a SharePoint URL into (site_url, server_relative_folder_path).

    Handles URLs like:
      https://tenant.sharepoint.com/sites/mysite/Shared Documents/folder
    """
    parsed = urlparse(url)
    path_parts = parsed.path.split("/")

    # Detect /sites/xxx or /teams/xxx patterns
    site_end = 0
    for i, part in enumerate(path_parts):
        if part in ("sites", "teams") and i + 1 < len(path_parts):
            site_end = i + 2
            break

    if site_end:
        site_path = "/".join(path_parts[:site_end])
        folder_path = "/".join(path_parts[site_end:])
    else:
        site_path = parsed.path
        folder_path = ""

    site_url = f"{parsed.scheme}://{parsed.netloc}{site_path}"
    folder_relative = f"{site_path}/{folder_path}".rstrip("/") if folder_path else ""
    return site_url, folder_relative


def _make_progress_cb(
    filename: str,
    progress_callback: Optional[Callable[[str, int, int], None]],
) -> Optional[Callable[[int, int], None]]:
    """Create a progress callback bound to a specific filename."""
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
