"""Tests for the updated downloader module."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import responses as responses_lib

from openknow.downloader import (
    DownloadError,
    _match_filter,
    _safe_dirname,
    _sanitize_remote_path,
    _sync_folder_link,
    _sync_url_link,
    download_file,
    index_with_opencode,
    sync_workspace,
)
from openknow.workspace import add_link, create_workspace, init_db


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# _sanitize_remote_path
# ---------------------------------------------------------------------------

class TestSanitizeRemotePath:
    def test_normal_path_allowed(self, tmp_path):
        result = _sanitize_remote_path("folder/doc.pdf", tmp_path)
        assert str(result).startswith(str(tmp_path.resolve()))
        assert result.name == "doc.pdf"

    def test_traversal_stripped_and_made_safe(self, tmp_path):
        # "../secret.txt" strips ".." and becomes "secret.txt" inside base_dir (safe)
        result = _sanitize_remote_path("../secret.txt", tmp_path)
        assert str(result).startswith(str(tmp_path.resolve()))
        assert result.name == "secret.txt"

    def test_absolute_path_rejected(self, tmp_path):
        with pytest.raises(DownloadError, match="absolute"):
            _sanitize_remote_path("/etc/passwd", tmp_path)

    def test_deeply_nested_traversal_stripped(self, tmp_path):
        # "a/../../etc/passwd" strips ".." → safe path inside base_dir
        result = _sanitize_remote_path("a/../../etc/passwd", tmp_path)
        assert str(result).startswith(str(tmp_path.resolve()))

    def test_double_dots_in_middle_filtered(self, tmp_path):
        result = _sanitize_remote_path("docs/../readme.md", tmp_path)
        assert str(result).startswith(str(tmp_path.resolve()))
        assert result.name == "readme.md"

    def test_empty_after_sanitize_raises(self, tmp_path):
        with pytest.raises(DownloadError):
            _sanitize_remote_path("../", tmp_path)


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------

class TestDownloadFile:
    @responses_lib.activate
    def test_downloads_file_successfully(self, tmp_path):
        url = "https://download.example.com/doc.pdf"
        content = b"PDF content here"
        responses_lib.add(responses_lib.GET, url, body=content, status=200)

        dest = tmp_path / "doc.pdf"
        result = download_file(url, dest)

        assert result == dest
        assert dest.read_bytes() == content

    @responses_lib.activate
    def test_creates_parent_directories(self, tmp_path):
        url = "https://download.example.com/deep/doc.pdf"
        responses_lib.add(responses_lib.GET, url, body=b"content", status=200)

        dest = tmp_path / "deep" / "nested" / "doc.pdf"
        download_file(url, dest)
        assert dest.exists()

    @responses_lib.activate
    def test_raises_on_http_failure(self, tmp_path):
        url = "https://download.example.com/notfound.pdf"
        responses_lib.add(responses_lib.GET, url, status=404)

        with pytest.raises(DownloadError, match="Failed to download"):
            download_file(url, tmp_path / "notfound.pdf")

    @responses_lib.activate
    def test_calls_progress_callback(self, tmp_path):
        url = "https://download.example.com/doc.pdf"
        content = b"A" * 16384
        responses_lib.add(
            responses_lib.GET, url, body=content, status=200,
            headers={"Content-Length": str(len(content))},
        )

        calls = []
        download_file(url, tmp_path / "doc.pdf", progress_callback=lambda d, t: calls.append((d, t)))
        assert len(calls) > 0
        assert calls[-1][0] == len(content)


# ---------------------------------------------------------------------------
# _safe_dirname
# ---------------------------------------------------------------------------

class TestSafeDirname:
    def test_alphanumeric_unchanged(self):
        assert _safe_dirname("MyProject123") == "MyProject123"

    def test_special_chars_replaced(self):
        result = _safe_dirname("My Project/Link!")
        assert "/" not in result and " " not in result

    def test_truncated_to_64_chars(self):
        assert len(_safe_dirname("a" * 100)) <= 64

    def test_empty_returns_default(self):
        assert _safe_dirname("") == "default"


# ---------------------------------------------------------------------------
# _match_filter
# ---------------------------------------------------------------------------

class TestMatchFilter:
    def test_pdf_pattern(self):
        assert _match_filter("doc.pdf", "*.pdf")

    def test_wrong_extension(self):
        assert not _match_filter("doc.docx", "*.pdf")

    def test_case_insensitive(self):
        assert _match_filter("DOC.PDF", "*.pdf")

    def test_exact_match(self):
        assert _match_filter("report.xlsx", "report.xlsx")


# ---------------------------------------------------------------------------
# index_with_opencode
# ---------------------------------------------------------------------------

class TestIndexWithOpencode:
    def test_warns_if_opencode_not_installed(self, tmp_path):
        path = tmp_path / "doc.txt"
        path.write_text("content")
        with patch("openknow.downloader._find_opencode", return_value=None):
            result = index_with_opencode([path], "proj")
        assert result["indexed"] == []
        assert any("not installed" in e for e in result["errors"])

    def test_indexes_file_successfully(self, tmp_path):
        path = tmp_path / "doc.txt"
        path.write_text("content")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Indexed."
        with (
            patch("openknow.downloader._find_opencode", return_value="/usr/bin/opencode"),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = index_with_opencode([path], "proj")
        assert str(path) in result["indexed"]
        assert result["errors"] == []

    def test_records_error_on_opencode_failure(self, tmp_path):
        path = tmp_path / "doc.txt"
        path.write_text("content")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "opencode error"
        mock_result.stdout = ""
        with (
            patch("openknow.downloader._find_opencode", return_value="/usr/bin/opencode"),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = index_with_opencode([path], "proj")
        assert result["indexed"] == []
        assert any("opencode error" in e for e in result["errors"])

    def test_skips_missing_file(self, tmp_path):
        missing = tmp_path / "ghost.txt"
        with patch("openknow.downloader._find_opencode", return_value="/usr/bin/opencode"):
            result = index_with_opencode([missing], "proj")
        assert missing not in result["indexed"]


# ---------------------------------------------------------------------------
# sync_workspace
# ---------------------------------------------------------------------------

class TestSyncWorkspace:
    def test_handles_empty_workspace(self, db, tmp_path):
        create_workspace("proj", db_path=db)
        mock_client = MagicMock()
        results = sync_workspace(
            workspace_name="proj",
            download_dir=tmp_path,
            db_path=db,
            opencode_index=False,
        )
        assert results == []

    def test_downloads_onedrive_files(self, db, tmp_path):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://1drv.ms/f/abc", db_path=db)

        file_info = {
            "name": "doc.pdf",
            "path": "doc.pdf",
            "download_url": "https://dl.example.com/doc.pdf",
            "size": 1024,
            "last_modified": None,
            "mime_type": "application/pdf",
        }

        with (
            patch("openknow.downloader.OneDriveClient") as MockClient,
            patch("openknow.downloader.download_file") as mock_dl,
            patch("openknow.downloader.is_plugin_installed", return_value=True),
        ):
            MockClient.return_value.list_folder_items.return_value = [file_info]
            mock_dl.return_value = tmp_path / "proj" / "doc.pdf"
            results = sync_workspace(
                workspace_name="proj",
                download_dir=tmp_path,
                db_path=db,
                opencode_index=False,
            )

        assert len(results) == 1
        assert results[0]["status"] == "ok"

    def test_applies_file_filter(self, db, tmp_path):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://1drv.ms/f/abc", db_path=db)

        files = [
            {"name": "doc.pdf", "path": "doc.pdf", "download_url": "https://dl.example.com/doc.pdf", "size": 512, "last_modified": None},
            {"name": "image.png", "path": "image.png", "download_url": "https://dl.example.com/image.png", "size": 256, "last_modified": None},
        ]

        with (
            patch("openknow.downloader.OneDriveClient") as MockClient,
            patch("openknow.downloader.download_file") as mock_dl,
            patch("openknow.downloader.is_plugin_installed", return_value=True),
        ):
            MockClient.return_value.list_folder_items.return_value = files
            mock_dl.return_value = tmp_path / "doc.pdf"
            results = sync_workspace(
                workspace_name="proj",
                download_dir=tmp_path,
                file_filter="*.pdf",
                db_path=db,
                opencode_index=False,
            )

        assert len(results) == 1
        assert results[0]["file"] == "doc.pdf"

    def test_handles_scan_error_gracefully(self, db, tmp_path):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://1drv.ms/f/abc", db_path=db)

        with patch("openknow.downloader.OneDriveClient") as MockClient, \
             patch("openknow.downloader.is_plugin_installed", return_value=True):
            MockClient.return_value.list_folder_items.side_effect = Exception("Network error")
            # Falls back to treating the URL as a direct file link
            with patch("openknow.downloader.download_file") as mock_dl:
                mock_dl.side_effect = Exception("404")
                results = sync_workspace(
                    workspace_name="proj",
                    download_dir=tmp_path,
                    db_path=db,
                    opencode_index=False,
                )
        assert any(r["status"] == "error" for r in results)

    def test_plugin_not_installed_returns_error(self, db, tmp_path):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://1drv.ms/f/abc", db_path=db)

        with patch("openknow.downloader.is_plugin_installed", return_value=False):
            results = sync_workspace(
                workspace_name="proj",
                download_dir=tmp_path,
                db_path=db,
                opencode_index=False,
            )
        assert len(results) == 1
        assert results[0]["status"] == "error"
        assert "plugin" in results[0]["error"].lower()


# ---------------------------------------------------------------------------
# _sync_folder_link
# ---------------------------------------------------------------------------

class TestSyncFolderLink:
    def test_copies_files_from_local_folder(self, db, tmp_path):
        # Create a source folder with some files
        src = tmp_path / "source"
        src.mkdir()
        (src / "report.pdf").write_bytes(b"PDF")
        (src / "notes.txt").write_bytes(b"notes")

        create_workspace("proj", db_path=db)
        link_id = add_link("proj", str(src), db_path=db)
        link_dir = tmp_path / "dest"

        results = _sync_folder_link(
            url=str(src),
            link_id=link_id,
            link_dir=link_dir,
            workspace_name="proj",
            file_filter=None,
            progress_callback=None,
            downloaded_paths=[],
            db_path=db,
        )
        assert len(results) == 2
        assert all(r["status"] == "ok" for r in results)
        names = {r["file"] for r in results}
        assert "report.pdf" in names
        assert "notes.txt" in names

    def test_applies_file_filter(self, db, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "doc.pdf").write_bytes(b"PDF")
        (src / "image.png").write_bytes(b"PNG")

        create_workspace("proj", db_path=db)
        link_id = add_link("proj", str(src), db_path=db)
        link_dir = tmp_path / "dest"

        results = _sync_folder_link(
            url=str(src),
            link_id=link_id,
            link_dir=link_dir,
            workspace_name="proj",
            file_filter="*.pdf",
            progress_callback=None,
            downloaded_paths=[],
            db_path=db,
        )
        assert len(results) == 1
        assert results[0]["file"] == "doc.pdf"

    def test_recurses_into_subdirectories(self, db, tmp_path):
        src = tmp_path / "source"
        (src / "sub").mkdir(parents=True)
        (src / "sub" / "nested.txt").write_bytes(b"nested")

        create_workspace("proj", db_path=db)
        link_id = add_link("proj", str(src), db_path=db)
        link_dir = tmp_path / "dest"

        results = _sync_folder_link(
            url=str(src),
            link_id=link_id,
            link_dir=link_dir,
            workspace_name="proj",
            file_filter=None,
            progress_callback=None,
            downloaded_paths=[],
            db_path=db,
        )
        assert len(results) == 1
        assert "nested.txt" in results[0]["file"]

    def test_returns_error_for_nonexistent_folder(self, db, tmp_path):
        create_workspace("proj", db_path=db)
        link_id = add_link("proj", str(tmp_path), db_path=db)

        results = _sync_folder_link(
            url="/nonexistent/path/that/does/not/exist",
            link_id=link_id,
            link_dir=tmp_path / "dest",
            workspace_name="proj",
            file_filter=None,
            progress_callback=None,
            downloaded_paths=[],
            db_path=db,
        )
        assert len(results) == 1
        assert results[0]["status"] == "error"

    def test_sync_workspace_routes_folder_link(self, db, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "file.txt").write_bytes(b"hello")

        create_workspace("proj", db_path=db)
        add_link("proj", str(src), db_path=db)

        results = sync_workspace(
            workspace_name="proj",
            download_dir=tmp_path / "downloads",
            db_path=db,
            opencode_index=False,
        )
        assert len(results) == 1
        assert results[0]["status"] == "ok"


# ---------------------------------------------------------------------------
# _sync_url_link
# ---------------------------------------------------------------------------

class TestSyncUrlLink:
    @responses_lib.activate
    def test_downloads_file_from_url(self, db, tmp_path):
        url = "https://files.example.com/report.pdf"
        responses_lib.add(responses_lib.GET, url, body=b"PDF content", status=200)

        create_workspace("proj", db_path=db)
        link_id = add_link("proj", url, db_path=db)
        link_dir = tmp_path / "dest"
        downloaded = []

        results = _sync_url_link(
            url=url,
            link_id=link_id,
            link_dir=link_dir,
            workspace_name="proj",
            file_filter=None,
            progress_callback=None,
            downloaded_paths=downloaded,
            db_path=db,
        )
        assert len(results) == 1
        assert results[0]["status"] == "ok"
        assert results[0]["file"] == "report.pdf"
        assert len(downloaded) == 1

    @responses_lib.activate
    def test_applies_file_filter(self, db, tmp_path):
        url = "https://files.example.com/image.png"
        responses_lib.add(responses_lib.GET, url, body=b"PNG", status=200)

        create_workspace("proj", db_path=db)
        link_id = add_link("proj", url, db_path=db)
        link_dir = tmp_path / "dest"

        results = _sync_url_link(
            url=url,
            link_id=link_id,
            link_dir=link_dir,
            workspace_name="proj",
            file_filter="*.pdf",
            progress_callback=None,
            downloaded_paths=[],
            db_path=db,
        )
        assert results == []

    @responses_lib.activate
    def test_returns_error_on_http_failure(self, db, tmp_path):
        url = "https://files.example.com/missing.pdf"
        responses_lib.add(responses_lib.GET, url, status=404)

        create_workspace("proj", db_path=db)
        link_id = add_link("proj", url, db_path=db)

        results = _sync_url_link(
            url=url,
            link_id=link_id,
            link_dir=tmp_path / "dest",
            workspace_name="proj",
            file_filter=None,
            progress_callback=None,
            downloaded_paths=[],
            db_path=db,
        )
        assert len(results) == 1
        assert results[0]["status"] == "error"

    @responses_lib.activate
    def test_sync_workspace_routes_url_link(self, db, tmp_path):
        url = "https://files.example.com/data.csv"
        responses_lib.add(responses_lib.GET, url, body=b"a,b,c", status=200)

        create_workspace("proj", db_path=db)
        add_link("proj", url, db_path=db)

        results = sync_workspace(
            workspace_name="proj",
            download_dir=tmp_path,
            db_path=db,
            opencode_index=False,
        )
        assert len(results) == 1
        assert results[0]["status"] == "ok"
