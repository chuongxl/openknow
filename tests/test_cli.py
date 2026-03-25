"""Tests for the CLI interface."""

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

    # Patch config to use temp paths
    monkeypatch.setenv("OPENKNOW_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("OPENKNOW_DOWNLOAD_DIR", str(download_dir))

    init_db(db_path)
    # Patch get_db_path to return our temp DB
    with patch("openknow.workspace.get_db_path", return_value=db_path), \
         patch("openknow.cli._ensure_db", lambda: init_db(db_path)):
        yield {"db_path": db_path, "config_dir": config_dir, "download_dir": download_dir}


class TestConfigureCommand:
    def test_saves_auth_config(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENKNOW_CONFIG_DIR", str(tmp_path))
        with patch("openknow.cli._ensure_db"):
            result = runner.invoke(
                cli,
                ["configure", "--client-id", "my-client-id", "--tenant-id", "my-tenant"],
            )
        assert result.exit_code == 0
        assert "Configuration saved" in result.output


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

    def test_delete_workspace_with_confirmation(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            runner.invoke(cli, ["workspace", "create", "todelete"])
            result = runner.invoke(cli, ["workspace", "delete", "todelete", "--yes"])
        assert result.exit_code == 0
        assert "deleted" in result.output

    def test_create_duplicate_workspace_fails(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            runner.invoke(cli, ["workspace", "create", "proj"])
            result = runner.invoke(cli, ["workspace", "create", "proj"])
        assert result.exit_code != 0


class TestLinkCommands:
    def test_add_link_to_workspace(self, runner, isolated_env):
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

            # Get link ID from list output
            from openknow.workspace import list_links
            links = list_links("proj", db)
            link_id = links[0]["id"]

            result = runner.invoke(cli, ["link", "remove", "proj", str(link_id)])
        assert result.exit_code == 0
        assert "removed" in result.output

    def test_add_link_to_nonexistent_workspace_fails(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            result = runner.invoke(cli, ["link", "add", "ghost", "https://1drv.ms/f/abc"])
        assert result.exit_code != 0


class TestFilesCommand:
    def test_files_command_no_downloads(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            runner.invoke(cli, ["workspace", "create", "proj"])
            result = runner.invoke(cli, ["files", "proj"])
        assert result.exit_code == 0
        assert "No downloaded files" in result.output

    def test_files_command_nonexistent_workspace(self, runner, isolated_env):
        db = isolated_env["db_path"]
        with patch("openknow.workspace.get_db_path", return_value=db):
            result = runner.invoke(cli, ["files", "ghost"])
        assert result.exit_code != 0
