"""Tests for Microsoft Graph API client module."""

import base64
from unittest.mock import MagicMock, patch

import pytest
import requests

from openknow.graph_client import AuthError, GraphClient, GraphError


@pytest.fixture
def client(tmp_path):
    """Provide a GraphClient with a test client_id and temp cache path."""
    return GraphClient(
        client_id="test-client-id-1234",
        tenant_id="common",
        token_cache_path=tmp_path / "token_cache.json",
    )


class TestDetectLinkType:
    """Test link type detection via add_link (via workspace module)."""

    def test_sharepoint_url_detected(self, client):
        # The _detect_link_type function is imported indirectly; test the public API
        from openknow.workspace import _detect_link_type
        assert _detect_link_type("https://company.sharepoint.com/sites/test") == "sharepoint"

    def test_onedrive_short_url_detected(self):
        from openknow.workspace import _detect_link_type
        assert _detect_link_type("https://1drv.ms/f/abc") == "onedrive"

    def test_onedrive_live_url_detected(self):
        from openknow.workspace import _detect_link_type
        assert _detect_link_type("https://onedrive.live.com/redir?...") == "onedrive"

    def test_unknown_url(self):
        from openknow.workspace import _detect_link_type
        assert _detect_link_type("https://example.com/file") == "unknown"


class TestGraphClientInit:
    def test_loads_client_id(self, client):
        assert client.client_id == "test-client-id-1234"

    def test_loads_tenant_id(self, client):
        assert client.tenant_id == "common"


class TestResolveShareUrl:
    def test_encodes_url_as_share_id(self, client):
        """The share ID must be URL-safe base64 of the URL, prefixed with 'u!'."""
        share_url = "https://1drv.ms/f/abc123"
        expected_encoded = base64.urlsafe_b64encode(share_url.encode()).rstrip(b"=").decode()
        expected_share_id = f"u!{expected_encoded}"

        with patch.object(client, "_graph_get") as mock_get:
            mock_get.return_value = {"id": "item123", "name": "TestFolder"}
            client.resolve_share_url(share_url)
            called_url = mock_get.call_args[0][0]
            assert expected_share_id in called_url

    def test_returns_drive_item(self, client):
        with patch.object(client, "_graph_get") as mock_get:
            mock_get.return_value = {"id": "item123", "name": "doc.pdf", "file": {}}
            result = client.resolve_share_url("https://1drv.ms/f/abc")
            assert result["id"] == "item123"

    def test_raises_graph_error_on_failure(self, client):
        with patch.object(client, "_graph_get") as mock_get:
            mock_get.side_effect = GraphError("Not found")
            with pytest.raises(GraphError):
                client.resolve_share_url("https://1drv.ms/f/abc")


class TestListFolderItems:
    def test_yields_items_from_single_page(self, client):
        mock_data = {
            "value": [
                {"id": "f1", "name": "file1.pdf", "file": {}},
                {"id": "f2", "name": "file2.docx", "file": {}},
            ]
        }
        with patch.object(client, "_graph_get", return_value=mock_data):
            items = list(client.list_folder_items("drive1", "folder1"))
        assert len(items) == 2
        assert items[0]["name"] == "file1.pdf"

    def test_paginates_using_next_link(self, client):
        page1 = {
            "value": [{"id": "f1", "name": "file1.pdf", "file": {}}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
        }
        page2 = {
            "value": [{"id": "f2", "name": "file2.pdf", "file": {}}],
        }
        call_count = 0

        def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            return page1 if call_count == 1 else page2

        with patch.object(client, "_graph_get", side_effect=mock_get):
            items = list(client.list_folder_items("drive1", "folder1"))

        assert len(items) == 2
        assert call_count == 2


class TestGetDownloadUrl:
    def test_returns_download_url(self, client):
        mock_item = {
            "id": "item1",
            "name": "doc.pdf",
            "@microsoft.graph.downloadUrl": "https://download.example.com/doc.pdf",
        }
        with patch.object(client, "_graph_get", return_value=mock_item):
            url = client.get_download_url("drive1", "item1")
        assert url == "https://download.example.com/doc.pdf"

    def test_raises_for_folder(self, client):
        mock_item = {"id": "folder1", "name": "MyFolder", "folder": {}}
        with patch.object(client, "_graph_get", return_value=mock_item):
            with pytest.raises(GraphError, match="No download URL"):
                client.get_download_url("drive1", "folder1")


class TestScanShareUrl:
    def test_returns_single_file(self, client):
        drive_item = {
            "id": "file1",
            "name": "doc.pdf",
            "file": {"mimeType": "application/pdf"},
            "size": 1024,
            "lastModifiedDateTime": "2024-01-01T00:00:00Z",
            "@microsoft.graph.downloadUrl": "https://download.example.com/doc.pdf",
            "parentReference": {"driveId": "drive1"},
        }
        with patch.object(client, "resolve_share_url", return_value=drive_item):
            files = client.scan_share_url("https://1drv.ms/f/abc")

        assert len(files) == 1
        assert files[0]["name"] == "doc.pdf"
        assert files[0]["size"] == 1024
        assert files[0]["drive_id"] == "drive1"

    def test_returns_files_from_folder(self, client):
        folder_item = {
            "id": "folder1",
            "name": "MyFolder",
            "folder": {"childCount": 2},
            "parentReference": {"driveId": "drive1"},
        }
        child_files = [
            {
                "id": f"file{i}",
                "name": f"doc{i}.pdf",
                "file": {"mimeType": "application/pdf"},
                "size": 512 * i,
                "parentReference": {"driveId": "drive1"},
                "@microsoft.graph.downloadUrl": f"https://download.example.com/doc{i}.pdf",
            }
            for i in range(1, 3)
        ]

        with (
            patch.object(client, "resolve_share_url", return_value=folder_item),
            patch.object(client, "list_folder_items", return_value=iter(child_files)),
        ):
            files = client.scan_share_url("https://1drv.ms/f/folder")

        assert len(files) == 2
        assert files[0]["name"] == "doc1.pdf"
        assert files[1]["name"] == "doc2.pdf"
