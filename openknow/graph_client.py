"""Microsoft Graph API client for OneDrive and SharePoint access.

Uses MSAL device code flow authentication, which is ideal for local/CLI tools
where a browser is available but no redirect URI is configured.
"""

import json
import os
from pathlib import Path
from typing import Iterator, List, Optional
from urllib.parse import quote

import msal
import requests

from .config import GRAPH_BASE_URL, GRAPH_SCOPES, get_config_dir, load_auth_config


class AuthError(Exception):
    """Raised when authentication fails."""


class GraphError(Exception):
    """Raised when a Microsoft Graph API request fails."""


class GraphClient:
    """Client for Microsoft Graph API supporting OneDrive and SharePoint.

    Authenticates via MSAL device code flow and caches tokens locally to
    avoid re-authentication on every run.
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        token_cache_path: Optional[Path] = None,
    ):
        auth_config = load_auth_config()
        self.client_id = client_id or auth_config.get("client_id", "")
        self.tenant_id = tenant_id or auth_config.get("tenant_id", "common")
        self.token_cache_path = token_cache_path or (get_config_dir() / "token_cache.json")
        self._app: Optional[msal.PublicClientApplication] = None

    def _get_token_cache(self) -> msal.SerializableTokenCache:
        """Load or create a serializable token cache."""
        cache = msal.SerializableTokenCache()
        if self.token_cache_path.exists():
            try:
                cache.deserialize(self.token_cache_path.read_text())
            except Exception:
                pass
        return cache

    def _save_token_cache(self, cache: msal.SerializableTokenCache) -> None:
        """Persist token cache to disk if it has changed."""
        if cache.has_state_changed:
            self.token_cache_path.write_text(cache.serialize())
            # Restrict permissions so only the owner can read the token cache
            os.chmod(self.token_cache_path, 0o600)

    def _get_app(self) -> msal.PublicClientApplication:
        """Get or create the MSAL application instance."""
        if not self.client_id:
            raise AuthError(
                "Azure AD client_id is not configured. "
                "Run 'openknow configure' to set up authentication."
            )
        if self._app is None:
            cache = self._get_token_cache()
            self._app = msal.PublicClientApplication(
                self.client_id,
                authority=f"https://login.microsoftonline.com/{self.tenant_id}",
                token_cache=cache,
            )
        return self._app

    def get_access_token(self) -> str:
        """Obtain a valid access token, prompting for device code login if needed.

        Returns:
            A valid access token string.

        Raises:
            AuthError: If authentication fails.
        """
        app = self._get_app()
        cache = app.token_cache

        # Try to get token silently from cache first
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
            if result and "access_token" in result:
                self._save_token_cache(cache)
                return result["access_token"]

        # Fall back to device code flow
        flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
        if "user_code" not in flow:
            raise AuthError(f"Failed to initiate device flow: {flow.get('error_description', 'Unknown error')}")

        print("\n" + flow["message"])
        print("Waiting for authentication...")

        result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise AuthError(
                f"Authentication failed: {result.get('error_description', result.get('error', 'Unknown error'))}"
            )

        self._save_token_cache(cache)
        return result["access_token"]

    def _get_headers(self) -> dict:
        """Return request headers with a valid bearer token."""
        return {
            "Authorization": f"Bearer {self.get_access_token()}",
            "Accept": "application/json",
        }

    def _graph_get(self, url: str, params: Optional[dict] = None) -> dict:
        """Make an authenticated GET request to the Graph API.

        Args:
            url: Full URL or path relative to Graph base URL.
            params: Optional query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            GraphError: If the request fails.
        """
        if not url.startswith("http"):
            url = f"{GRAPH_BASE_URL}{url}"

        response = requests.get(url, headers=self._get_headers(), params=params, timeout=30)
        if not response.ok:
            raise GraphError(
                f"Graph API request failed [{response.status_code}]: {response.text}"
            )
        return response.json()

    def resolve_share_url(self, share_url: str) -> dict:
        """Resolve a OneDrive or SharePoint share URL to a Drive item.

        Uses the Graph API shares endpoint to look up the shared resource.

        Args:
            share_url: A OneDrive or SharePoint share URL.

        Returns:
            Drive item metadata dict.

        Raises:
            GraphError: If the URL cannot be resolved.
        """
        # Encode the share URL as per Graph API requirements
        import base64
        encoded = base64.urlsafe_b64encode(share_url.encode()).rstrip(b"=").decode()
        share_id = f"u!{encoded}"

        return self._graph_get(f"/shares/{share_id}/driveItem")

    def list_folder_items(self, drive_id: str, item_id: str) -> Iterator[dict]:
        """Yield all items in a folder (with pagination).

        Args:
            drive_id: The drive ID.
            item_id: The folder item ID.

        Yields:
            Drive item metadata dicts.
        """
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/children"
        while url:
            data = self._graph_get(url)
            for item in data.get("value", []):
                yield item
            url = data.get("@odata.nextLink")

    def list_sharepoint_folder(self, site_url: str, folder_path: str = "") -> Iterator[dict]:
        """Yield all items in a SharePoint document library folder.

        Args:
            site_url: SharePoint site URL (e.g. https://tenant.sharepoint.com/sites/mysite).
            folder_path: Relative path within the document library.

        Yields:
            Drive item metadata dicts.
        """
        # Get site ID from URL
        parsed = site_url.rstrip("/").split("/")
        site_hostname = parsed[2]
        site_path = "/" + "/".join(parsed[3:]) if len(parsed) > 3 else ""

        site_data = self._graph_get(f"/sites/{site_hostname}:{site_path}")
        site_id = site_data["id"]

        # Get default document library drive
        drives = self._graph_get(f"/sites/{site_id}/drives")
        if not drives.get("value"):
            raise GraphError(f"No document libraries found for site: {site_url}")
        drive_id = drives["value"][0]["id"]

        if folder_path:
            encoded_path = quote(folder_path)
            url = f"{GRAPH_BASE_URL}/drives/{drive_id}/root:/{encoded_path}:/children"
        else:
            url = f"{GRAPH_BASE_URL}/drives/{drive_id}/root/children"

        while url:
            data = self._graph_get(url)
            for item in data.get("value", []):
                yield item
            url = data.get("@odata.nextLink")

    def get_download_url(self, drive_id: str, item_id: str) -> str:
        """Get a temporary download URL for a file.

        Args:
            drive_id: The drive ID.
            item_id: The file item ID.

        Returns:
            A pre-authenticated download URL.

        Raises:
            GraphError: If the file is not downloadable.
        """
        item = self._graph_get(f"/drives/{drive_id}/items/{item_id}")
        download_url = item.get("@microsoft.graph.downloadUrl")
        if not download_url:
            raise GraphError(f"No download URL available for item '{item_id}'. It may be a folder.")
        return download_url

    def scan_share_url(self, share_url: str) -> List[dict]:
        """Scan a OneDrive or SharePoint share URL and return all file items.

        Recursively lists all files under the shared folder (or returns the
        single file if the share URL points directly to a file).

        Args:
            share_url: A OneDrive or SharePoint share URL.

        Returns:
            List of file item dicts with keys: name, path, drive_id, item_id,
            size, last_modified, mime_type.
        """
        drive_item = self.resolve_share_url(share_url)
        files = []
        self._collect_files(drive_item, "", files)
        return files

    def _collect_files(self, item: dict, parent_path: str, files: list) -> None:
        """Recursively collect file items from a drive item tree."""
        name = item.get("name", "")
        path = f"{parent_path}/{name}".lstrip("/")
        drive_id = item.get("parentReference", {}).get("driveId", "")
        item_id = item.get("id", "")

        if "folder" in item:
            # Recurse into folder
            for child in self.list_folder_items(drive_id, item_id):
                self._collect_files(child, path, files)
        elif "file" in item:
            files.append(
                {
                    "name": name,
                    "path": path,
                    "drive_id": drive_id,
                    "item_id": item_id,
                    "size": item.get("size", 0),
                    "last_modified": item.get("lastModifiedDateTime"),
                    "mime_type": item.get("file", {}).get("mimeType", ""),
                    "download_url": item.get("@microsoft.graph.downloadUrl"),
                }
            )
