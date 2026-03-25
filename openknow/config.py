"""Configuration management for OpenKnow agent."""

import json
import os
from pathlib import Path

# Default config directory in user's home
DEFAULT_CONFIG_DIR = Path.home() / ".openknow"

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


def get_credentials_path() -> Path:
    """Return the path to the stored credentials file."""
    return get_config_dir() / "credentials.json"


def save_credentials(username: str, password: str) -> None:
    """Persist Microsoft 365 credentials to disk (mode 600).

    Args:
        username: Microsoft 365 username (e.g. user@company.com).
        password: Microsoft 365 account password.
    """
    creds_file = get_credentials_path()
    with open(creds_file, "w") as f:
        json.dump({"username": username, "password": password}, f, indent=2)
    os.chmod(creds_file, 0o600)


def load_credentials() -> dict:
    """Load Microsoft 365 credentials from environment or config file.

    Environment variables ``OPENKNOW_USERNAME`` and ``OPENKNOW_PASSWORD``
    take precedence over the stored credentials file.

    Returns:
        Dict with keys ``username`` and ``password`` (both may be empty strings).
    """
    creds = {
        "username": os.environ.get("OPENKNOW_USERNAME", ""),
        "password": os.environ.get("OPENKNOW_PASSWORD", ""),
    }
    creds_file = get_credentials_path()
    if creds_file.exists():
        try:
            with open(creds_file) as f:
                stored = json.load(f)
            # env vars override file values
            if not creds["username"]:
                creds["username"] = stored.get("username", "")
            if not creds["password"]:
                creds["password"] = stored.get("password", "")
        except (json.JSONDecodeError, OSError):
            pass
    return creds
