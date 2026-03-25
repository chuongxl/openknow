# openknow

A local agent that automatically accesses knowledge via OneDrive shared folders and SharePoint sites, downloads files to your machine, and integrates with the opencode CLI.

---

## Tech Stack Decision: OpenHands vs Microsoft Power Apps

| Factor | OpenHands (Python agent) | Microsoft Power Apps / Power Automate |
|---|---|---|
| **Cost** | Free (open-source + free Azure AD app registration) | Requires Microsoft 365 / Power Apps per-user or per-app plan (~$5–$20/user/month) |
| **Local execution** | Runs entirely on local computer | Cloud-first; local agent requires a gateway |
| **opencode CLI integration** | Native Python CLI, integrates directly | Requires custom connectors or webhooks |
| **OneDrive/SharePoint access** | Microsoft Graph API (free, no extra licensing) | Native connectors (premium connectors add cost) |
| **Setup complexity** | Moderate (Azure AD app registration needed once) | Low (drag-and-drop) but requires M365 admin access |
| **Customisation** | Full control over logic and data handling | Limited to available connectors and flow steps |
| **Data privacy** | Files stay on local machine | Files flow through Microsoft cloud services |

**Decision: Python-based local agent (this project)**

For a local computer agent that integrates with the opencode CLI, the Python approach is chosen because:
1. **Zero operational cost** – Microsoft Graph API is free; no per-user licensing.
2. **Local-first** – files are downloaded and processed entirely on your machine.
3. **Full CLI integration** – native Click CLI works seamlessly with opencode workflows.
4. **No vendor lock-in** – open-source, auditable, and fully customisable.

---

## Features

- 🗂 **Workspace management** – isolated knowledge spaces with local SQLite memory
- 🔗 **1-5 links per workspace** – add OneDrive or SharePoint share URLs to each workspace
- 📂 **Folder scanning** – list all files in a shared folder without downloading
- ⬇️ **File sync** – download files from OneDrive/SharePoint to local storage
- 🔍 **File filtering** – filter by pattern (e.g. `*.pdf`, `*report*`)
- 🔄 **Token caching** – MSAL token cache avoids re-authentication on every run
- 🖥 **opencode CLI compatible** – runs as a standard CLI tool

---

## Prerequisites

- Python 3.9+
- A Microsoft account (personal, work, or school)
- An Azure AD app registration (free) – see [Setup](#setup)

---

## Installation

```bash
git clone https://github.com/chuongxl/openknow.git
cd openknow
pip install -e .
```

---

## Setup

### 1. Register an Azure AD Application (one-time)

1. Go to [Azure Portal → App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **New registration**
3. Set **Name** (e.g. `openknow`)
4. Under **Supported account types**, choose:
   - *Accounts in any organizational directory and personal Microsoft accounts* (for both OneDrive personal and SharePoint work accounts)
5. Set **Redirect URI** → **Public client/native** → `http://localhost`
6. Click **Register**
7. Go to **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated**:
   - `Files.Read`
   - `Files.Read.All`
   - `Sites.Read.All`
   - `offline_access`
8. Copy the **Application (client) ID**

### 2. Configure openknow

```bash
openknow configure
# Enter your Application (client) ID when prompted
```

Configuration is stored in `~/.openknow/auth.json`.

---

## Quick Start

```bash
# 1. Create a workspace
openknow workspace create research --description "AI research papers"

# 2. Add OneDrive or SharePoint links (up to 5 per workspace)
openknow link add research https://1drv.ms/f/s!AbCdEfGhIjKlMnOp
openknow link add research https://company.sharepoint.com/sites/AITeam/Shared%20Documents --label "Team Docs"

# 3. Scan to see what files are available (no download)
openknow scan research

# 4. Sync (download) all files locally
openknow sync research

# 5. View downloaded files
openknow files research
```

---

## Commands

### `openknow configure`
Set up Azure AD credentials for Microsoft Graph API.

```bash
openknow configure --client-id <YOUR_CLIENT_ID> --tenant-id common
```

### `openknow workspace`
Manage knowledge workspaces.

```bash
openknow workspace create <name> [--description TEXT]
openknow workspace list
openknow workspace delete <name> [--yes]
```

### `openknow link`
Manage OneDrive/SharePoint links within a workspace (1–5 per workspace).

```bash
openknow link add <workspace> <url> [--label TEXT]
openknow link list <workspace>
openknow link remove <workspace> <link-id>
```

### `openknow scan`
List all files in a workspace's links without downloading.

```bash
openknow scan <workspace> [--filter PATTERN]
# Example: openknow scan research --filter "*.pdf"
```

### `openknow sync`
Download all files from a workspace's links to local storage.

```bash
openknow sync <workspace> [--filter PATTERN] [--output-dir PATH]
# Example: openknow sync research --filter "*.docx" --output-dir ~/Documents/research
```

### `openknow files`
List all locally downloaded files for a workspace.

```bash
openknow files <workspace>
```

---

## Authentication

openknow uses the **device code flow** (MSAL) for authentication:

1. On first run, you will see a code like `Enter the code ABC123 at https://microsoft.com/devicelogin`
2. Open the URL in any browser and enter the code
3. Sign in with your Microsoft account
4. Tokens are cached in `~/.openknow/token_cache.json` (file permissions set to 600)

Subsequent runs will use the cached token silently until it expires.

---

## File Storage

Downloaded files are saved to `~/openknow_files/<workspace>/<link-label>/` by default.

Override with the `OPENKNOW_DOWNLOAD_DIR` environment variable or `--output-dir` flag.

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `OPENKNOW_CONFIG_DIR` | Directory for config and token cache | `~/.openknow` |
| `OPENKNOW_DOWNLOAD_DIR` | Base directory for downloaded files | `~/openknow_files` |
| `OPENKNOW_CLIENT_ID` | Azure AD client ID (overrides config file) | – |
| `OPENKNOW_TENANT_ID` | Azure AD tenant ID | `common` |

---

## Architecture

```
openknow/
├── openknow/
│   ├── __init__.py       # Package version
│   ├── cli.py            # Click CLI entry point
│   ├── config.py         # Configuration and paths
│   ├── workspace.py      # SQLite-based workspace/link/file memory
│   ├── graph_client.py   # Microsoft Graph API client (MSAL auth)
│   └── downloader.py     # File download and sync logic
└── tests/
    ├── test_workspace.py
    ├── test_graph_client.py
    ├── test_downloader.py
    └── test_cli.py
```

### Local Memory (SQLite)

The agent maintains local memory in `~/.openknow/openknow.db`:

- **workspaces** – named knowledge spaces with descriptions
- **workspace_links** – 1-5 OneDrive/SharePoint URLs per workspace
- **file_cache** – record of synced files with local paths and timestamps

---

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

---

## License

MIT License – see [LICENSE](LICENSE).
