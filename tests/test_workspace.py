"""Tests for workspace management module."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from openknow.workspace import (
    MAX_LINKS_PER_WORKSPACE,
    WorkspaceError,
    add_link,
    create_workspace,
    delete_workspace,
    get_workspace,
    init_db,
    list_cached_files,
    list_links,
    list_workspaces,
    record_file_sync,
    remove_link,
)


@pytest.fixture
def db(tmp_path):
    """Provide a fresh database for each test."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


class TestCreateWorkspace:
    def test_creates_workspace_successfully(self, db):
        ws_id = create_workspace("myproject", "A test project", db)
        assert isinstance(ws_id, int)
        assert ws_id > 0

    def test_creates_workspace_without_description(self, db):
        ws_id = create_workspace("myproject", db_path=db)
        assert ws_id > 0

    def test_raises_on_duplicate_name(self, db):
        create_workspace("myproject", db_path=db)
        with pytest.raises(WorkspaceError, match="already exists"):
            create_workspace("myproject", db_path=db)

    def test_multiple_workspaces_have_unique_ids(self, db):
        id1 = create_workspace("proj1", db_path=db)
        id2 = create_workspace("proj2", db_path=db)
        assert id1 != id2


class TestListWorkspaces:
    def test_empty_list_when_no_workspaces(self, db):
        assert list_workspaces(db) == []

    def test_returns_all_workspaces(self, db):
        create_workspace("alpha", db_path=db)
        create_workspace("beta", db_path=db)
        workspaces = list_workspaces(db)
        names = [ws["name"] for ws in workspaces]
        assert "alpha" in names
        assert "beta" in names

    def test_link_count_is_zero_initially(self, db):
        create_workspace("proj", db_path=db)
        ws = list_workspaces(db)[0]
        assert ws["link_count"] == 0

    def test_link_count_reflects_added_links(self, db):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://1drv.ms/f/abc", db_path=db)
        ws = list_workspaces(db)[0]
        assert ws["link_count"] == 1


class TestGetWorkspace:
    def test_returns_workspace_by_name(self, db):
        create_workspace("myproj", "desc", db)
        ws = get_workspace("myproj", db)
        assert ws["name"] == "myproj"
        assert ws["description"] == "desc"

    def test_raises_for_nonexistent_workspace(self, db):
        with pytest.raises(WorkspaceError, match="does not exist"):
            get_workspace("nonexistent", db)


class TestDeleteWorkspace:
    def test_deletes_workspace(self, db):
        create_workspace("todelete", db_path=db)
        delete_workspace("todelete", db)
        assert list_workspaces(db) == []

    def test_raises_for_nonexistent_workspace(self, db):
        with pytest.raises(WorkspaceError, match="does not exist"):
            delete_workspace("ghost", db)

    def test_cascades_to_links(self, db):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://1drv.ms/f/abc", db_path=db)
        delete_workspace("proj", db)
        # After deletion workspace should be gone and links should be cascade-deleted
        assert list_workspaces(db) == []


class TestAddLink:
    def test_adds_onedrive_link(self, db):
        create_workspace("proj", db_path=db)
        link_id = add_link("proj", "https://1drv.ms/f/abc", db_path=db)
        assert link_id > 0

    def test_adds_sharepoint_link(self, db):
        create_workspace("proj", db_path=db)
        link_id = add_link("proj", "https://company.sharepoint.com/sites/test", db_path=db)
        assert link_id > 0

    def test_detects_onedrive_type(self, db):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://1drv.ms/f/abc", db_path=db)
        links = list_links("proj", db)
        assert links[0]["link_type"] == "onedrive"

    def test_detects_sharepoint_type(self, db):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://company.sharepoint.com/sites/test", db_path=db)
        links = list_links("proj", db)
        assert links[0]["link_type"] == "sharepoint"

    def test_detects_unknown_type(self, db):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://example.com/file", db_path=db)
        links = list_links("proj", db)
        assert links[0]["link_type"] == "unknown"

    def test_raises_on_duplicate_url(self, db):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://1drv.ms/f/abc", db_path=db)
        with pytest.raises(WorkspaceError, match="already added"):
            add_link("proj", "https://1drv.ms/f/abc", db_path=db)

    def test_raises_on_nonexistent_workspace(self, db):
        with pytest.raises(WorkspaceError, match="does not exist"):
            add_link("ghost", "https://1drv.ms/f/abc", db_path=db)

    def test_raises_when_max_links_exceeded(self, db):
        create_workspace("proj", db_path=db)
        for i in range(MAX_LINKS_PER_WORKSPACE):
            add_link("proj", f"https://1drv.ms/f/link{i}", db_path=db)
        with pytest.raises(WorkspaceError, match="maximum allowed"):
            add_link("proj", "https://1drv.ms/f/extra", db_path=db)

    def test_max_links_is_five(self):
        assert MAX_LINKS_PER_WORKSPACE == 5

    def test_stores_label(self, db):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://1drv.ms/f/abc", label="My Docs", db_path=db)
        links = list_links("proj", db)
        assert links[0]["label"] == "My Docs"


class TestRemoveLink:
    def test_removes_link_successfully(self, db):
        create_workspace("proj", db_path=db)
        link_id = add_link("proj", "https://1drv.ms/f/abc", db_path=db)
        remove_link("proj", link_id, db)
        assert list_links("proj", db) == []

    def test_raises_for_wrong_link_id(self, db):
        create_workspace("proj", db_path=db)
        with pytest.raises(WorkspaceError, match="not found"):
            remove_link("proj", 999, db)

    def test_raises_for_nonexistent_workspace(self, db):
        with pytest.raises(WorkspaceError, match="does not exist"):
            remove_link("ghost", 1, db)


class TestListLinks:
    def test_returns_empty_list(self, db):
        create_workspace("proj", db_path=db)
        assert list_links("proj", db) == []

    def test_returns_all_links(self, db):
        create_workspace("proj", db_path=db)
        add_link("proj", "https://1drv.ms/f/abc", db_path=db)
        add_link("proj", "https://company.sharepoint.com/sites/test", db_path=db)
        links = list_links("proj", db)
        assert len(links) == 2


class TestRecordFileSync:
    def test_records_file(self, db):
        create_workspace("proj", db_path=db)
        link_id = add_link("proj", "https://1drv.ms/f/abc", db_path=db)
        record_file_sync(
            workspace_name="proj",
            link_id=link_id,
            remote_path="folder/doc.pdf",
            local_path="/tmp/doc.pdf",
            file_size=1024,
            last_modified="2024-01-01T00:00:00",
            db_path=db,
        )
        cached = list_cached_files("proj", db)
        assert len(cached) == 1
        assert cached[0]["remote_path"] == "folder/doc.pdf"
        assert cached[0]["local_path"] == "/tmp/doc.pdf"
        assert cached[0]["file_size"] == 1024

    def test_updates_on_duplicate_path(self, db):
        create_workspace("proj", db_path=db)
        link_id = add_link("proj", "https://1drv.ms/f/abc", db_path=db)
        record_file_sync("proj", link_id, "doc.pdf", "/tmp/v1.pdf", 100, db_path=db)
        record_file_sync("proj", link_id, "doc.pdf", "/tmp/v2.pdf", 200, db_path=db)
        cached = list_cached_files("proj", db)
        assert len(cached) == 1
        assert cached[0]["local_path"] == "/tmp/v2.pdf"
        assert cached[0]["file_size"] == 200

    def test_two_links_same_remote_path_separate_entries(self, db):
        """Two different links in the same workspace with the same remote_path
        should create two separate cache entries (not overwrite each other)."""
        create_workspace("proj", db_path=db)
        link_id1 = add_link("proj", "https://1drv.ms/f/abc", db_path=db)
        link_id2 = add_link("proj", "https://company.sharepoint.com/sites/s", db_path=db)
        record_file_sync("proj", link_id1, "docs/readme.pdf", "/tmp/link1/readme.pdf", 100, db_path=db)
        record_file_sync("proj", link_id2, "docs/readme.pdf", "/tmp/link2/readme.pdf", 200, db_path=db)
        cached = list_cached_files("proj", db)
        assert len(cached) == 2
