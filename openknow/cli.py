"""CLI entry point for OpenKnow agent.

Provides commands for managing workspaces, adding OneDrive/SharePoint links,
scanning remote folders, and downloading files to local storage.

Usage:
    openknow configure               Set up Azure AD credentials
    openknow workspace create NAME   Create a new workspace
    openknow workspace list          List all workspaces
    openknow workspace delete NAME   Delete a workspace
    openknow link add NAME URL       Add a link to a workspace
    openknow link list NAME          List links in a workspace
    openknow link remove NAME ID     Remove a link from a workspace
    openknow scan NAME               Scan files in all workspace links
    openknow sync NAME               Download files from all workspace links
    openknow files NAME              List locally downloaded files for a workspace
"""

import sys
from pathlib import Path
from typing import Optional

import click

from . import __version__
from .config import get_config_dir, get_download_dir, save_auth_config
from .graph_client import AuthError, GraphClient, GraphError
from .workspace import (
    WorkspaceError,
    add_link,
    create_workspace,
    delete_workspace,
    init_db,
    list_cached_files,
    list_links,
    list_workspaces,
    remove_link,
)
from .downloader import DownloadError, sync_workspace


def _ensure_db() -> None:
    """Initialize the database on first use."""
    init_db()


def _make_client() -> GraphClient:
    """Create a configured GraphClient."""
    return GraphClient()


def _format_size(size: int) -> str:
    """Format byte size to human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


@click.group()
@click.version_option(version=__version__, prog_name="openknow")
def cli() -> None:
    """OpenKnow - Local agent for accessing OneDrive and SharePoint knowledge.

    This tool lets you manage workspaces, add OneDrive/SharePoint share links,
    scan remote folders, and download files to your local machine.

    Quick start:
    \b
        openknow configure
        openknow workspace create myproject
        openknow link add myproject https://1drv.ms/f/...
        openknow sync myproject
    """
    _ensure_db()


# ---------------------------------------------------------------------------
# Configure
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--client-id", prompt="Azure AD Application (client) ID", help="Azure AD app client ID.")
@click.option(
    "--tenant-id",
    default="common",
    show_default=True,
    help="Azure AD tenant ID or 'common' for personal/work accounts.",
)
def configure(client_id: str, tenant_id: str) -> None:
    """Configure Azure AD credentials for Microsoft Graph API access.

    \b
    Steps to get your client_id:
    1. Go to https://portal.azure.com -> Azure Active Directory -> App registrations
    2. Click 'New registration', choose 'Public client/native' redirect URI
    3. Under 'API permissions', add:
       - Microsoft Graph: Files.Read, Files.Read.All, Sites.Read.All
    4. Copy the 'Application (client) ID' and use it here.
    """
    save_auth_config(client_id=client_id, tenant_id=tenant_id)
    click.echo(f"Configuration saved to {get_config_dir() / 'auth.json'}")
    click.echo("Run 'openknow workspace create <name>' to get started.")


# ---------------------------------------------------------------------------
# Workspace commands
# ---------------------------------------------------------------------------

@cli.group()
def workspace() -> None:
    """Manage workspaces for organizing OneDrive/SharePoint links."""


@workspace.command("create")
@click.argument("name")
@click.option("--description", "-d", default="", help="Optional description for the workspace.")
def workspace_create(name: str, description: str) -> None:
    """Create a new workspace named NAME."""
    try:
        ws_id = create_workspace(name, description)
        click.echo(f"Workspace '{name}' created (id={ws_id}).")
    except WorkspaceError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@workspace.command("list")
def workspace_list() -> None:
    """List all workspaces."""
    workspaces = list_workspaces()
    if not workspaces:
        click.echo("No workspaces found. Run 'openknow workspace create <name>' to create one.")
        return

    click.echo(f"{'NAME':<20} {'LINKS':>5}  {'DESCRIPTION'}")
    click.echo("-" * 60)
    for ws in workspaces:
        click.echo(f"{ws['name']:<20} {ws['link_count']:>5}  {ws['description']}")


@workspace.command("delete")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
def workspace_delete(name: str, yes: bool) -> None:
    """Delete workspace NAME and all its data."""
    if not yes:
        click.confirm(f"Delete workspace '{name}' and all its links and cached file records?", abort=True)
    try:
        delete_workspace(name)
        click.echo(f"Workspace '{name}' deleted.")
    except WorkspaceError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Link commands
# ---------------------------------------------------------------------------

@cli.group()
def link() -> None:
    """Manage OneDrive and SharePoint links within a workspace."""


@link.command("add")
@click.argument("workspace_name")
@click.argument("url")
@click.option("--label", "-l", default="", help="Optional label for this link.")
def link_add(workspace_name: str, url: str, label: str) -> None:
    """Add a OneDrive or SharePoint share URL to WORKSPACE_NAME.

    Each workspace can hold between 1 and 5 links.

    \b
    Example:
        openknow link add myproject https://1drv.ms/f/s!AbCdEfGh
        openknow link add myproject https://company.sharepoint.com/sites/... --label "Team Docs"
    """
    try:
        link_id = add_link(workspace_name, url, label)
        click.echo(f"Link added to workspace '{workspace_name}' (link id={link_id}).")
    except WorkspaceError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@link.command("list")
@click.argument("workspace_name")
def link_list(workspace_name: str) -> None:
    """List all links in WORKSPACE_NAME."""
    try:
        links = list_links(workspace_name)
    except WorkspaceError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if not links:
        click.echo(f"No links in workspace '{workspace_name}'. Use 'openknow link add' to add one.")
        return

    click.echo(f"Links in workspace '{workspace_name}':")
    click.echo(f"{'ID':>4}  {'TYPE':<11}  {'LABEL':<20}  URL")
    click.echo("-" * 80)
    for lnk in links:
        label = (lnk["label"] or "")[:20]
        click.echo(f"{lnk['id']:>4}  {lnk['link_type']:<11}  {label:<20}  {lnk['url']}")


@link.command("remove")
@click.argument("workspace_name")
@click.argument("link_id", type=int)
def link_remove(workspace_name: str, link_id: int) -> None:
    """Remove link LINK_ID from WORKSPACE_NAME."""
    try:
        remove_link(workspace_name, link_id)
        click.echo(f"Link {link_id} removed from workspace '{workspace_name}'.")
    except WorkspaceError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Scan command
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("workspace_name")
@click.option("--filter", "-f", "file_filter", default=None, help="Filter files by pattern (e.g. '*.pdf').")
def scan(workspace_name: str, file_filter: Optional[str]) -> None:
    """Scan and list all remote files in WORKSPACE_NAME without downloading.

    Requires authentication with Microsoft. You will be prompted to sign in
    via device code flow on the first run.
    """
    try:
        links = list_links(workspace_name)
    except WorkspaceError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if not links:
        click.echo(f"No links in workspace '{workspace_name}'.")
        return

    client = _make_client()
    total_files = 0

    for lnk in links:
        url = lnk["url"]
        label = lnk.get("label") or url
        click.echo(f"\nScanning: {label}")
        click.echo(f"  URL: {url}")

        try:
            files = client.scan_share_url(url)
        except (AuthError, GraphError) as exc:
            click.echo(f"  Error: {exc}", err=True)
            continue

        filtered = [
            f for f in files
            if not file_filter or _match_filter_cli(f["name"], file_filter)
        ]

        click.echo(f"  Found {len(filtered)} file(s){' (filtered)' if file_filter else ''}:")
        for file_info in filtered:
            size_str = _format_size(file_info["size"])
            click.echo(f"    {file_info['path']}  [{size_str}]  {file_info.get('last_modified', '')}")
        total_files += len(filtered)

    click.echo(f"\nTotal: {total_files} file(s)")


# ---------------------------------------------------------------------------
# Sync command
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("workspace_name")
@click.option("--filter", "-f", "file_filter", default=None, help="Filter files by pattern (e.g. '*.pdf').")
@click.option("--output-dir", "-o", default=None, help="Override the default download directory.")
def sync(workspace_name: str, file_filter: Optional[str], output_dir: Optional[str]) -> None:
    """Download all files from WORKSPACE_NAME links to local storage.

    Files are saved under the download directory in a folder named after
    the workspace. The download location is shown after syncing.
    """
    download_dir = Path(output_dir) if output_dir else get_download_dir()
    client = _make_client()

    click.echo(f"Syncing workspace '{workspace_name}' to {download_dir / workspace_name} ...")

    def _progress(filename: str, done: int, total: int) -> None:
        if total > 0:
            pct = int(done / total * 100)
            click.echo(f"  Downloading {filename}: {pct}%", nl=False)
            click.echo("\r", nl=False)
        else:
            click.echo(f"  Downloading {filename}...", nl=False)
            click.echo("\r", nl=False)

    try:
        results = sync_workspace(
            workspace_name=workspace_name,
            client=client,
            download_dir=download_dir,
            file_filter=file_filter,
            progress_callback=_progress,
        )
    except (AuthError, WorkspaceError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    ok = [r for r in results if r["status"] == "ok"]
    errors = [r for r in results if r["status"] == "error"]

    click.echo(f"\nSync complete: {len(ok)} file(s) downloaded, {len(errors)} error(s).")

    if errors:
        click.echo("\nErrors:")
        for err in errors:
            click.echo(f"  {err['file']}: {err['error']}")

    if ok:
        click.echo(f"\nFiles saved to: {download_dir / workspace_name}")


# ---------------------------------------------------------------------------
# Files command
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("workspace_name")
def files(workspace_name: str) -> None:
    """List files that have been downloaded for WORKSPACE_NAME."""
    try:
        cached = list_cached_files(workspace_name)
    except WorkspaceError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if not cached:
        click.echo(f"No downloaded files for workspace '{workspace_name}'. Run 'openknow sync {workspace_name}'.")
        return

    click.echo(f"Downloaded files for workspace '{workspace_name}':")
    click.echo(f"{'REMOTE PATH':<40}  {'SIZE':>8}  {'SYNCED AT'}")
    click.echo("-" * 80)
    for entry in cached:
        size_str = _format_size(entry["file_size"])
        click.echo(f"{entry['remote_path']:<40}  {size_str:>8}  {entry['synced_at']}")


def _match_filter_cli(filename: str, pattern: str) -> bool:
    """Check if filename matches a glob-style pattern (CLI helper)."""
    from fnmatch import fnmatch
    return fnmatch(filename.lower(), pattern.lower())


def main() -> None:
    """Entry point for the openknow CLI."""
    cli()


if __name__ == "__main__":
    main()
