"""Microsoft Graph API client for OneDrive and SharePoint access.

Authenticates using Microsoft 365 username and password credentials via the
Office365-REST-Python-Client library. No Azure AD app registration or device
code flow is required — users just provide their existing Microsoft 365
account credentials.
"""

import base64
from pathlib import Path
from pathlib import PurePosixPath
from typing import Iterator, List, Optional
from urllib.parse import quote, urlparse

import requests

from .config import GRAPH_BASE_URL, get_config_dir, load_credentials


class AuthError(Exception):
    """Raised when authentication fails."""


class GraphError(Exception):
    """Raised when a Microsoft Graph API request fails."""


class SharePointClient:
    """Client for SharePoint Online using username/password credentials.

    Uses the Office365-REST-Python-Client library which authenticates through
    Microsoft 365 SAML-based authentication (no Azure AD app registration needed).
    """

    def __init__(self, username: Optional[str] = None, password: Optional[str] = None):
        creds = load_credentials()
        self.username = username or creds.get("username", "")
        self.password = password or creds.get("password", "")

        if not self.username or not self.password:
            raise AuthError(
                "Microsoft 365 credentials are not configured. "
                "Run 'openknow configure' to store your username and password."
            )

    def _get_context(self, site_url: str):
        """Return an authenticated SharePoint ClientContext for the given site URL."""
        try:
            from office365.sharepoint.client_context import ClientContext
        except ImportError as exc:
            raise AuthError(
                "Office365-REST-Python-Client is not installed. "
                "Run: pip install Office365-REST-Python-Client"
            ) from exc

        ctx = ClientContext(site_url).with_user_credentials(self.username, self.password)
        return ctx

    def list_folder_files(self, site_url: str, folder_relative_url: str = "") -> List[dict]:
        """List all files recursively within a SharePoint folder.

        Args:
            site_url: Full SharePoint site URL (e.g. https://tenant.sharepoint.com/sites/mysite).
            folder_relative_url: Server-relative URL of the folder (e.g. /sites/mysite/Shared Documents).
                                  Defaults to the root Shared Documents library.

        Returns:
            List of file dicts with keys: name, path, server_relative_url, size, last_modified.
        """
        ctx = self._get_context(site_url)

        if not folder_relative_url:
            # Default to the root Shared Documents library
            folder_relative_url = "/Shared Documents"
            parsed = urlparse(site_url)
            site_path = parsed.path.rstrip("/")
            folder_relative_url = f"{site_path}/Shared Documents"

        folder = ctx.web.get_folder_by_server_relative_url(folder_relative_url)
        ctx.load(folder)
        ctx.execute_query()

        files = []
        self._collect_sp_files(ctx, folder, folder_relative_url, files)
        return files

    def _collect_sp_files(self, ctx, folder, base_path: str, files: list) -> None:
        """Recursively collect files from a SharePoint folder."""
        items = folder.files
        ctx.load(items)
        ctx.execute_query()

        for f in items:
            server_url = f.properties.get("ServerRelativeUrl", "")
            relative = server_url[len(base_path):].lstrip("/")
            files.append(
                {
                    "name": f.properties.get("Name", ""),
                    "path": relative or f.properties.get("Name", ""),
                    "server_relative_url": server_url,
                    "size": f.properties.get("Length", 0),
                    "last_modified": f.properties.get("TimeLastModified"),
                }
            )

        subfolders = folder.folders
        ctx.load(subfolders)
        ctx.execute_query()

        for sf in subfolders:
            sf_url = sf.properties.get("ServerRelativeUrl", "")
            sf_name = sf.properties.get("Name", "")
            if sf_name in ("_t", "_w", "Forms"):
                continue  # skip SharePoint system folders
            self._collect_sp_files(ctx, sf, base_path, files)

    def download_file(self, site_url: str, server_relative_url: str, dest_path: Path) -> Path:
        """Download a SharePoint file to a local path.

        Args:
            site_url: Full SharePoint site URL.
            server_relative_url: Server-relative URL of the file.
            dest_path: Local destination path.

        Returns:
            The destination path.
        """
        ctx = self._get_context(site_url)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        with open(dest_path, "wb") as f:
            file = ctx.web.get_file_by_server_relative_url(server_relative_url)
            file.download(f)
            ctx.execute_query()

        return dest_path


class OneDriveClient:
    """Client for downloading files from OneDrive share links.

    Share links that are publicly accessible (shared with "anyone with the link")
    can be downloaded directly using requests without authentication.

    For links that require Microsoft account credentials, the client opens the
    download in the system browser as a fallback.
    """

    def __init__(self, username: Optional[str] = None, password: Optional[str] = None):
        creds = load_credentials()
        self.username = username or creds.get("username", "")
        self.password = password or creds.get("password", "")
        self._session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        """Return a requests Session (reused across calls)."""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": "openknow/0.2.0"})
        return self._session

    def _resolve_share_url(self, share_url: str) -> str:
        """Follow redirects to get the final download URL for a share link."""
        session = self._get_session()
        resp = session.head(share_url, allow_redirects=True, timeout=15)
        return resp.url

    def download_share_url(self, share_url: str, dest_path: Path) -> Path:
        """Download a file from a OneDrive share link.

        Tries direct streaming download first. If the file requires sign-in,
        raises AuthError with instructions to open the URL in a browser.

        Args:
            share_url: A OneDrive share link URL.
            dest_path: Local destination path.

        Returns:
            The local destination path.

        Raises:
            AuthError: If the link requires authentication that cannot be satisfied.
        """
        session = self._get_session()
        # OneDrive share links ending in :/download redirect to direct download
        download_url = self._make_direct_download_url(share_url)

        resp = session.get(download_url, stream=True, timeout=60)
        if resp.status_code in (401, 403):
            raise AuthError(
                f"The OneDrive link requires Microsoft account sign-in.\n"
                f"Please open the link manually in your browser and download the file:\n"
                f"  {share_url}\n"
                f"Then run 'openknow link add <workspace> <url>' with a direct download link."
            )
        if not resp.ok:
            raise GraphError(
                f"Failed to download OneDrive share link [{resp.status_code}]: {share_url}"
            )

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        return dest_path

    def _make_direct_download_url(self, share_url: str) -> str:
        """Convert a OneDrive share URL to a direct download URL where possible.

        OneDrive short URLs (1drv.ms) and share links support a ``download=1``
        query parameter that forces a direct file download.
        """
        if "download" in share_url:
            return share_url
        separator = "&" if "?" in share_url else "?"
        return f"{share_url}{separator}download=1"

    def list_folder_items(self, share_url: str) -> List[dict]:
        """List files in a shared OneDrive folder.

        Uses the Graph API shares endpoint with anonymous access (works for
        "anyone with the link" shares). Returns a flat list of all files.

        Args:
            share_url: OneDrive shared folder URL.

        Returns:
            List of file dicts with keys: name, path, download_url, size, last_modified.
        """
        encoded = base64.urlsafe_b64encode(share_url.encode()).rstrip(b"=").decode()
        share_id = f"u!{encoded}"

        session = self._get_session()
        url = f"{GRAPH_BASE_URL}/shares/{share_id}/driveItem"
        resp = session.get(url, timeout=30)

        if not resp.ok:
            raise GraphError(
                f"Cannot list OneDrive share folder [{resp.status_code}]: {share_url}\n"
                "Ensure the link is a shared folder accessible to anyone."
            )

        drive_item = resp.json()
        files: List[dict] = []
        self._collect_drive_files(session, drive_item, "", files)
        return files

    def _collect_drive_files(
        self, session: requests.Session, item: dict, parent_path: str, files: list
    ) -> None:
        """Recursively collect downloadable file entries."""
        name = item.get("name", "")
        path = f"{parent_path}/{name}".lstrip("/")
        drive_id = item.get("parentReference", {}).get("driveId", "")
        item_id = item.get("id", "")

        if "folder" in item:
            children_url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/children"
            while children_url:
                resp = session.get(children_url, timeout=30)
                if not resp.ok:
                    break
                data = resp.json()
                for child in data.get("value", []):
                    self._collect_drive_files(session, child, path, files)
                children_url = data.get("@odata.nextLink")
        elif "file" in item:
            download_url = item.get("@microsoft.graph.downloadUrl", "")
            if not download_url and drive_id and item_id:
                download_url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/content"
            files.append(
                {
                    "name": name,
                    "path": path,
                    "drive_id": drive_id,
                    "item_id": item_id,
                    "download_url": download_url,
                    "size": item.get("size", 0),
                    "last_modified": item.get("lastModifiedDateTime"),
                    "mime_type": item.get("file", {}).get("mimeType", ""),
                }
            )
