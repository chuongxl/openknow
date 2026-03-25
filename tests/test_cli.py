"""Tests for the updated CLI interface."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from openknow.cli import cli
from openknow.workspace import init_db


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Provide an isolated environment with a fresh database."""
    db_path = tmp_path / "openknow.db"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    download_dir = tmp_path / "downloads"

    monkeypatch.setenv("OPENKNOW_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("OPENKNOW_DOWNLOAD_DIR", str(download_dir))

    init_db(db_path)
    with patch("openknow.workspace.get_db_path", return_value=db_path), \
         patch("openknow.cli._ensure_db", lambda: init_db(db_path)):
        yield {"db_path": db_path, "config_dir": config_dir, "download_dir": download_dir}


# ---------------------------------------------------------------------------
# configure command (now uses username/password)
# ---------------------------------------------------------------------------

class TestConfigureCommand:
    def test_saves_credentials(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENKNOW_CONFIG_DIR", str(tmp_path))
        with patch("openknow.cli._ensure_db"):
            result = runner.invoke(
                cli,
                ["configure", "--username", "user@company.com", "--password", "secret"],
            )
        assert result.exit_code == 0, result.output
        assert "Credentials saved" in result.output

    def test_credentials_file_created(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENKNOW_CONFIG_DIR", str(tmp_path))
        with patch("openknow.cli._ensure_db"):
            runner.invoke(
                cli,
                ["configure", "--username", "user@company.com", "--password", "mysecret"],
            )
        creds_file = tmp_path / "credentials.json"
        assert creds_file.exists()
        data = json.loads(creds_file.read_text())
        assert data["username"] == "user@company.com"


# ---------------------------------------------------------------------------
# Workspace commands
# ---------------------------------------------------------------------------

class TestWorkspaceCommands:
    def test_create_workspace(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            result = runner.invoke(cli, ["workspace", "create", "myproject"])
        assert result.exit_code == 0
        assert "myproject" in result.output

    def test_list_workspaces_empty(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            result = runner.invoke(cli, ["workspace", "list"])
        assert result.exit_code == 0
        assert "No workspaces" in result.output

    def test_list_workspaces_shows_created(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            runner.invoke(cli, ["workspace", "create", "proj1"])
            result = runner.invoke(cli, ["workspace", "list"])
        assert result.exit_code == 0
        assert "proj1" in result.output

    def test_delete_workspace(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            runner.invoke(cli, ["workspace", "create", "todelete"])
            result = runner.invoke(cli, ["workspace", "delete", "todelete", "--yes"])
        assert result.exit_code == 0
        assert "deleted" in result.output

    def test_create_duplicate_fails(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            runner.invoke(cli, ["workspace", "create", "proj"])
            result = runner.invoke(cli, ["workspace", "create", "proj"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Link commands
# ---------------------------------------------------------------------------

class TestLinkCommands:
    def test_add_link(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            runner.invoke(cli, ["workspace", "create", "proj"])
            result = runner.invoke(cli, ["link", "add", "proj", "https://1drv.ms/f/abc"])
        assert result.exit_code == 0
        assert "Link added" in result.output

    def test_list_links_empty(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            runner.invoke(cli, ["workspace", "create", "proj"])
            result = runner.invoke(cli, ["link", "list", "proj"])
        assert result.exit_code == 0
        assert "No links" in result.output

    def test_list_links_shows_added(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            runner.invoke(cli, ["workspace", "create", "proj"])
            runner.invoke(cli, ["link", "add", "proj", "https://1drv.ms/f/abc"])
            result = runner.invoke(cli, ["link", "list", "proj"])
        assert result.exit_code == 0
        assert "https://1drv.ms/f/abc" in result.output

    def test_remove_link(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            runner.invoke(cli, ["workspace", "create", "proj"])
            runner.invoke(cli, ["link", "add", "proj", "https://1drv.ms/f/abc"])
            from openknow.workspace import list_links
            links = list_links("proj", db)
            link_id = links[0]["id"]
            result = runner.invoke(cli, ["link", "remove", "proj", str(link_id)])
        assert result.exit_code == 0
        assert "removed" in result.output

    def test_add_link_nonexistent_workspace_fails(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            result = runner.invoke(cli, ["link", "add", "ghost", "https://1drv.ms/f/abc"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Files command
# ---------------------------------------------------------------------------

class TestFilesCommand:
    def test_no_downloads(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            runner.invoke(cli, ["workspace", "create", "proj"])
            result = runner.invoke(cli, ["files", "proj"])
        assert result.exit_code == 0
        assert "No downloaded files" in result.output

    def test_nonexistent_workspace_fails(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            result = runner.invoke(cli, ["files", "ghost"])
        assert result.exit_code != 0
