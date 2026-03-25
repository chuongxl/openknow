"""Tests for the file downloader module."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import responses as responses_lib

from openknow.downloader import (
    DownloadError,
    _match_filter,
    _safe_dirname,
    download_file,
    sync_workspace,
)
from openknow.workspace import add_link, create_workspace, init_db


@pytest.fixture
def db(tmp_path):
    """Provide a fresh database for each test."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


class TestDownloadFile:
    @responses_lib.activate
    def test_downloads_file_successfully(self, tmp_path):
        url = "https://download.example.com/doc.pdf"
        content = b"PDF content here"
        responses_lib.add(responses_lib.GET, url, body=content, status=200)

        dest = tmp_path / "doc.pdf"
        result = download_file(url, dest)

        assert result == dest
        assert dest.exists()
        assert dest.read_bytes() == content

    @responses_lib.activate
    def test_creates_parent_directories(self, tmp_path):
        url = "https://download.example.com/nested/doc.pdf"
        responses_lib.add(responses_lib.GET, url, body=b"content", status=200)

        dest = tmp_path / "deep" / "nested" / "doc.pdf"
        download_file(url, dest)

        assert dest.exists()

    @responses_lib.activate
    def test_raises_download_error_on_http_failure(self, tmp_path):
        url = "https://download.example.com/notfound.pdf"
        responses_lib.add(responses_lib.GET, url, status=404)

        with pytest.raises(DownloadError, match="Failed to download"):
            download_file(url, tmp_path / "notfound.pdf")

    @responses_lib.activate
    def test_calls_progress_callback(self, tmp_path):
        url = "https://download.example.com/doc.pdf"
        content = b"A" * 16384  # 16 KB
        responses_lib.add(
            responses_lib.GET,
            url,
            body=content,
            status=200,
            headers={"Content-Length": str(len(content))},
        )

        progress_calls = []
        download_file(url, tmp_path / "doc.pdf", progress_callback=lambda d, t: progress_calls.append((d, t)))

        assert len(progress_calls) > 0
        assert progress_calls[-1][0] == len(content)


class TestSafeDirname:
    def test_alphanumeric_unchanged(self):
        assert _safe_dirname("MyProject123") == "MyProject123"

    def test_special_chars_replaced(self):
        result = _safe_dirname("My Project/Link!")
        assert "/" not in result
        assert " " not in result
        assert "!" not in result

    def test_allowed_special_chars(self):
        result = _safe_dirname("my-project_2.0")
        assert result == "my-project_2.0"

    def test_truncated_to_64_chars(self):
        long_name = "a" * 100
        assert len(_safe_dirname(long_name)) <= 64

    def test_empty_string_returns_default(self):
        assert _safe_dirname("") == "default"


class TestMatchFilter:
    def test_matches_pdf_pattern(self):
        assert _match_filter("document.pdf", "*.pdf") is True

    def test_does_not_match_wrong_extension(self):
        assert _match_filter("document.docx", "*.pdf") is False

    def test_case_insensitive(self):
        assert _match_filter("DOCUMENT.PDF", "*.pdf") is True

    def test_matches_exact_name(self):
        assert _match_filter("report.xlsx", "report.xlsx") is True

    def test_wildcard_prefix_and_suffix(self):
        assert _match_filter("my_report_final.docx", "*report*") is True


class TestSyncWorkspace:
    def test_syncs_files_from_workspace(self, db, tmp_path):
        create_workspace("proj", db_path=db)
        link_id = add_link("proj", "https://1drv.ms/f/abc", db_path=db)

        mock_client = MagicMock()
        mock_client.scan_share_url.return_value = [
            {
                "name": "doc.pdf",
                "path": "doc.pdf",
                "drive_id": "drive1",
                "item_id": "item1",
                "size": 1024,
                "last_modified": "2024-01-01T00:00:00Z",
                "download_url": "https://download.example.com/doc.pdf",
                "mime_type": "application/pdf",
            }
        ]

        with patch("openknow.downloader.download_file") as mock_download:
            mock_download.return_value = tmp_path / "proj" / "abc" / "doc.pdf"
            results = sync_workspace(
                workspace_name="proj",
                client=mock_client,
                download_dir=tmp_path,
                db_path=db,
            )

        assert len(results) == 1
        assert results[0]["status"] == "ok"
        assert results[0]["file"] == "doc.pdf"

    def test_handles_scan_error_gracefully(self, db, tmp_path):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://1drv.ms/f/abc", db_path=db)

        mock_client = MagicMock()
        mock_client.scan_share_url.side_effect = Exception("Network error")

        results = sync_workspace(
            workspace_name="proj",
            client=mock_client,
            download_dir=tmp_path,
            db_path=db,
        )

        assert len(results) == 1
        assert results[0]["status"] == "error"
        assert "Network error" in results[0]["error"]

    def test_applies_file_filter(self, db, tmp_path):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://1drv.ms/f/abc", db_path=db)

        mock_client = MagicMock()
        mock_client.scan_share_url.return_value = [
            {
                "name": "doc.pdf",
                "path": "doc.pdf",
                "drive_id": "drive1",
                "item_id": "item1",
                "size": 512,
                "last_modified": None,
                "download_url": "https://download.example.com/doc.pdf",
                "mime_type": "application/pdf",
            },
            {
                "name": "image.png",
                "path": "image.png",
                "drive_id": "drive1",
                "item_id": "item2",
                "size": 256,
                "last_modified": None,
                "download_url": "https://download.example.com/image.png",
                "mime_type": "image/png",
            },
        ]

        with patch("openknow.downloader.download_file") as mock_download:
            mock_download.return_value = tmp_path / "doc.pdf"
            results = sync_workspace(
                workspace_name="proj",
                client=mock_client,
                download_dir=tmp_path,
                file_filter="*.pdf",
                db_path=db,
            )

        # Only pdf should be downloaded
        assert len(results) == 1
        assert results[0]["file"] == "doc.pdf"

    def test_handles_empty_workspace(self, db, tmp_path):
        create_workspace("proj", db_path=db)
        mock_client = MagicMock()
        results = sync_workspace(
            workspace_name="proj",
            client=mock_client,
            download_dir=tmp_path,
            db_path=db,
        )
        assert results == []
        mock_client.scan_share_url.assert_not_called()
