"""Configuration management for OpenKnow agent."""

import json
import os
from pathlib import Path

# Default config directory in user's home
DEFAULT_CONFIG_DIR = Path.home() / ".openknow"

# Azure AD application settings for Microsoft Graph API
# Users register a free Azure AD app to get these values
DEFAULT_CLIENT_ID = ""
DEFAULT_TENANT_ID = "common"  # supports personal and work accounts

# Microsoft Graph API scopes needed for OneDrive/SharePoint access
GRAPH_SCOPES = [
    "Files.Read",
    "Files.Read.All",
    "Sites.Read.All",
    "offline_access",
]

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def get_config_dir() -> Path:
    """Return the configuration directory, creating it if needed."""
    config_dir = Path(os.environ.get("OPENKNOW_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_db_path() -> Path:
    """Return the path to the local SQLite database."""
    return get_config_dir() / "openknow.db"


def get_download_dir() -> Path:
    """Return the default download directory."""
    download_dir = Path(os.environ.get("OPENKNOW_DOWNLOAD_DIR", Path.home() / "openknow_files"))
    download_dir.mkdir(parents=True, exist_ok=True)
    return download_dir


def load_auth_config() -> dict:
    """Load Azure AD application configuration from config file or environment."""
    config_file = get_config_dir() / "auth.json"
    config = {
        "client_id": os.environ.get("OPENKNOW_CLIENT_ID", DEFAULT_CLIENT_ID),
        "tenant_id": os.environ.get("OPENKNOW_TENANT_ID", DEFAULT_TENANT_ID),
    }

    if config_file.exists():
        try:
            with open(config_file) as f:
                file_config = json.load(f)
            config.update(file_config)
        except (json.JSONDecodeError, OSError):
            pass

    return config


def save_auth_config(client_id: str, tenant_id: str = DEFAULT_TENANT_ID) -> None:
    """Save Azure AD application configuration to config file."""
    config_file = get_config_dir() / "auth.json"
    config = {"client_id": client_id, "tenant_id": tenant_id}
    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)
