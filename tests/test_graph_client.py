"""Tests for the updated graph_client module (username/password auth)."""

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from openknow.graph_client import AuthError, GraphError, OneDriveClient, SharePointClient
from openknow.workspace import _detect_link_type


# ---------------------------------------------------------------------------
# Link type detection
# ---------------------------------------------------------------------------

class TestDetectLinkType:
    def test_sharepoint_subdomain(self):
        assert _detect_link_type("https://company.sharepoint.com/sites/test") == "sharepoint"

    def test_sharepoint_root_domain(self):
        assert _detect_link_type("https://sharepoint.com/...") == "sharepoint"

    def test_onedrive_short_url(self):
        assert _detect_link_type("https://1drv.ms/f/abc") == "onedrive"

    def test_onedrive_live_url(self):
        assert _detect_link_type("https://onedrive.live.com/redir?...") == "onedrive"

    def test_onedrive_com_url(self):
        assert _detect_link_type("https://onedrive.com/file") == "onedrive"

    def test_unknown_url(self):
        assert _detect_link_type("https://example.com/file") == "url"

    def test_spoofed_sharepoint_rejected(self):
        # 'evilsharepoint.com' must NOT be detected as sharepoint
        assert _detect_link_type("https://evilsharepoint.com/file") == "url"

    def test_spoofed_onedrive_rejected(self):
        assert _detect_link_type("https://evil1drv.ms/file") == "url"


# ---------------------------------------------------------------------------
# OneDriveClient
# ---------------------------------------------------------------------------

class TestOneDriveClientInit:
    def test_works_without_credentials(self, tmp_path, monkeypatch):
        # OneDrive share links may be public (no auth required), so no error on init
        monkeypatch.setenv("OPENKNOW_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("OPENKNOW_USERNAME", raising=False)
        monkeypatch.delenv("OPENKNOW_PASSWORD", raising=False)
        client = OneDriveClient()  # should NOT raise
        assert client.username == ""

    def test_accepts_explicit_credentials(self):
        client = OneDriveClient(username="user@example.com", password="secret")
        assert client.username == "user@example.com"
        assert client.password == "secret"

    def test_loads_from_environment(self, monkeypatch):
        monkeypatch.setenv("OPENKNOW_USERNAME", "env_user@example.com")
        monkeypatch.setenv("OPENKNOW_PASSWORD", "env_pass")
        client = OneDriveClient()
        assert client.username == "env_user@example.com"


class TestMakeDirectDownloadUrl:
    def setup_method(self):
        self.client = OneDriveClient(username="u", password="p")

    def test_appends_download_param(self):
        url = self.client._make_direct_download_url("https://1drv.ms/f/abc")
        assert "download=1" in url

    def test_uses_ampersand_when_query_exists(self):
        url = self.client._make_direct_download_url("https://1drv.ms/f/abc?foo=bar")
        assert "?foo=bar&download=1" in url

    def test_does_not_double_add_download(self):
        url = self.client._make_direct_download_url("https://1drv.ms/f/abc?download=1")
        assert url.count("download") == 1


class TestOneDriveListFolderItems:
    def setup_method(self):
        self.client = OneDriveClient(username="u", password="p")

    def test_returns_files_for_public_share(self):
        folder_item = {
            "id": "folder1",
            "name": "Folder",
            "folder": {"childCount": 1},
            "parentReference": {"driveId": "drive1"},
        }
        child_file = {
            "id": "file1",
            "name": "doc.pdf",
            "file": {"mimeType": "application/pdf"},
            "size": 1024,
            "parentReference": {"driveId": "drive1"},
            "@microsoft.graph.downloadUrl": "https://download.example.com/doc.pdf",
        }
        children_page = {"value": [child_file]}

        def mock_get(url, timeout=30):
            resp = MagicMock()
            resp.ok = True
            if "shares" in url:
                resp.json.return_value = folder_item
            else:
                resp.json.return_value = children_page
            return resp

        with patch.object(self.client._get_session(), "get", side_effect=mock_get):
            files = self.client.list_folder_items("https://1drv.ms/f/abc")

        assert len(files) == 1
        assert files[0]["name"] == "doc.pdf"
        assert files[0]["size"] == 1024

    def test_raises_graph_error_on_api_failure(self):
        def mock_get(url, timeout=30):
            resp = MagicMock()
            resp.ok = False
            resp.status_code = 401
            return resp

        with patch.object(self.client._get_session(), "get", side_effect=mock_get):
            with pytest.raises(GraphError, match="Cannot list OneDrive share folder"):
                self.client.list_folder_items("https://1drv.ms/f/abc")


# ---------------------------------------------------------------------------
# SharePointClient
# ---------------------------------------------------------------------------

class TestSharePointClientInit:
    def test_raises_if_no_credentials(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENKNOW_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("OPENKNOW_USERNAME", raising=False)
        monkeypatch.delenv("OPENKNOW_PASSWORD", raising=False)
        with pytest.raises(AuthError, match="credentials are not configured"):
            SharePointClient()

    def test_accepts_explicit_credentials(self):
        client = SharePointClient(username="user@company.com", password="secret")
        assert client.username == "user@company.com"
