"""Plugin management for OpenKnow.

Plugins extend OpenKnow with optional support for remote storage providers
such as SharePoint and OneDrive.  Core functionality (local folders, generic
HTTP/HTTPS URLs) is always available without installing any plugin.

Usage:
    openknow plugins            - list available plugins and their status
    openknow plugins install sharepoint   - install the SharePoint plugin
    openknow plugins uninstall sharepoint - remove the SharePoint plugin
"""

import json
from pathlib import Path
from typing import Dict, List

from .config import get_config_dir

# ---------------------------------------------------------------------------
# Registry of all available plugins
# ---------------------------------------------------------------------------

PLUGIN_REGISTRY: Dict[str, dict] = {
    "sharepoint": {
        "name": "sharepoint",
        "description": "Microsoft SharePoint Online integration (username/password auth)",
        "requires_credentials": True,
    },
    "onedrive": {
        "name": "onedrive",
        "description": "Microsoft OneDrive integration (public share links; credentials optional)",
        "requires_credentials": False,
    },
}


class PluginError(Exception):
    """Raised when a plugin operation fails."""


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def get_plugins_file() -> Path:
    """Return the path to the plugins state file."""
    return get_config_dir() / "plugins.json"


def _load_plugins_data() -> dict:
    """Load raw plugin state from disk."""
    plugins_file = get_plugins_file()
    if not plugins_file.exists():
        return {"installed": []}
    try:
        with open(plugins_file) as f:
            data = json.load(f)
        if not isinstance(data.get("installed"), list):
            data["installed"] = []
        return data
    except (json.JSONDecodeError, OSError):
        return {"installed": []}


def _save_plugins_data(data: dict) -> None:
    """Save plugin state to disk."""
    plugins_file = get_plugins_file()
    with open(plugins_file, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_plugin_installed(name: str) -> bool:
    """Return True if the named plugin is installed."""
    data = _load_plugins_data()
    return name in data.get("installed", [])


def list_plugins() -> List[dict]:
    """Return all available plugins with their installation status.

    Returns:
        List of dicts with keys: name, description, requires_credentials, installed.
    """
    data = _load_plugins_data()
    installed = set(data.get("installed", []))
    return [
        {**meta, "installed": meta["name"] in installed}
        for meta in PLUGIN_REGISTRY.values()
    ]


def install_plugin(name: str) -> None:
    """Mark a plugin as installed.

    Args:
        name: Plugin name (e.g. ``'sharepoint'``, ``'onedrive'``).

    Raises:
        PluginError: If the plugin name is not recognised.
    """
    if name not in PLUGIN_REGISTRY:
        raise PluginError(
            f"Unknown plugin '{name}'. Available plugins: {', '.join(PLUGIN_REGISTRY)}"
        )
    data = _load_plugins_data()
    if name not in data["installed"]:
        data["installed"].append(name)
    _save_plugins_data(data)


def uninstall_plugin(name: str) -> None:
    """Mark a plugin as uninstalled.

    Args:
        name: Plugin name.

    Raises:
        PluginError: If the plugin is not installed.
    """
    data = _load_plugins_data()
    if name not in data.get("installed", []):
        raise PluginError(f"Plugin '{name}' is not installed.")
    data["installed"].remove(name)
    _save_plugins_data(data)
