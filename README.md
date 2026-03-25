# openknow

A local agent that automatically accesses knowledge via OneDrive shared folders and SharePoint sites, downloads files to your machine, and provides a ChatGPT-style web UI for asking questions about the content.

---

## Tech Stack Decision: OpenHands vs Microsoft Power Apps

| Factor | OpenHands (Python agent) | Microsoft Power Apps / Power Automate |
|---|---|---|
| **Cost** | Free (open-source, no licensing) | Requires Microsoft 365 / Power Apps plan (~$5–$20/user/month) |
| **Local execution** | Runs entirely on local computer | Cloud-first; local agent requires a gateway |
| **opencode CLI integration** | Native Python CLI, integrates directly | Requires custom connectors or webhooks |
| **Authentication** | Microsoft 365 username + password | Native connectors (premium connectors add cost) |
| **Data privacy** | Files stay on local machine | Files flow through Microsoft cloud services |
| **Setup complexity** | Minimal — just run `openknow configure` | Requires M365 admin access |

**Decision: Python-based local agent (this project)**

- **Zero operational cost** – no per-user licensing required
- **Local-first** – files stay on your machine, no cloud intermediaries
- **Native opencode integration** – automatically indexes downloaded files for AI-powered Q&A
- **Simple auth** – uses your existing Microsoft 365 username and password

---

## Features

- 🗂 **Workspace management** – isolated knowledge spaces with local SQLite memory
- 🔗 **1-5 links per workspace** – add OneDrive or SharePoint share URLs to each workspace
- 📂 **Folder scanning** – list all files in a shared folder without downloading
- ⬇️ **File sync** – download files from OneDrive/SharePoint to local storage
- 🔍 **File filtering** – filter by pattern (e.g. `*.pdf`, `*report*`)
- 🤖 **opencode integration** – downloaded files are automatically indexed by opencode for AI-powered answers
- 💬 **Chat web UI** – ask questions about your knowledge base via a ChatGPT-style browser interface

---

## Chat UI

![OpenKnow Chat UI](https://github.com/user-attachments/assets/cf119ac6-b582-45db-a66a-d394e8a2dd0d)

---

## Prerequisites

- Python 3.9+
- A Microsoft 365 account (personal, work, or school) with access to the OneDrive/SharePoint links
- [opencode](https://opencode.ai) installed locally (optional — required for AI-powered answers)

---

## Installation

```bash
git clone https://github.com/chuongxl/openknow.git
cd openknow
pip install -e .
```

---

## Quick Start

```bash
# 1. Store your Microsoft 365 credentials (saved locally, mode 600)
openknow configure

# 2. Create a workspace
openknow workspace create research --description "AI research papers"

# 3. Add OneDrive or SharePoint links (up to 5 per workspace)
openknow link add research https://1drv.ms/f/s!AbCdEfGhIjKlMnOp
openknow link add research https://company.sharepoint.com/sites/AITeam --label "Team Docs"

# 4. Scan to see what files are available (no download)
openknow scan research

# 5. Download all files (also indexes them with opencode if installed)
openknow sync research

# 6. Launch the chat UI and ask questions
openknow ui
# Then open http://127.0.0.1:5000 in your browser
```

---

## Commands

### `openknow configure`
Store Microsoft 365 credentials for OneDrive/SharePoint access. No Azure AD app registration required.

```bash
openknow configure
# Prompts for username (e.g. user@company.com) and password
# Saved to ~/.openknow/credentials.json (mode 600)
```

Alternatively, set environment variables:
```bash
export OPENKNOW_USERNAME="user@company.com"
export OPENKNOW_PASSWORD="your_password"
```

### `openknow workspace`
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
Download all files from a workspace's links. After downloading, indexes them with opencode (if installed).

```bash
openknow sync <workspace> [--filter PATTERN] [--output-dir PATH] [--no-index]
# Example: openknow sync research --filter "*.docx"
```

### `openknow files`
List all locally downloaded files for a workspace.

```bash
openknow files <workspace>
```

### `openknow ui`
Launch the chat web UI for asking questions about downloaded knowledge.

```bash
openknow ui [--host HOST] [--port PORT] [--workspace WORKSPACE]
# Default: http://127.0.0.1:5000
```

---

## opencode Integration

After syncing, openknow automatically calls:
```bash
opencode add <file> --context <workspace>
```
for each downloaded file, making it queryable through opencode's AI. When the chat UI receives a question, it:

1. Searches the downloaded text files for relevant excerpts (keyword scoring)
2. Builds a context string from the most relevant content
3. Calls `opencode ask <question>` with the context for AI-powered answers
4. Falls back to showing raw excerpts if opencode is not installed

---

## Authentication

### OneDrive shared links
Public share links (shared with "anyone with the link") are downloaded directly — no authentication needed. Private links require the configured Microsoft 365 credentials.

### SharePoint sites
Authenticates using your Microsoft 365 username and password via the `Office365-REST-Python-Client` library. No Azure AD app registration required.

---

## File Storage

Downloaded files are saved to `~/openknow_files/<workspace>/<link-label>/` by default.

Override with the `OPENKNOW_DOWNLOAD_DIR` environment variable or the `--output-dir` flag.

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `OPENKNOW_CONFIG_DIR` | Directory for config, credentials, and database | `~/.openknow` |
| `OPENKNOW_DOWNLOAD_DIR` | Base directory for downloaded files | `~/openknow_files` |
| `OPENKNOW_USERNAME` | Microsoft 365 username (overrides credentials file) | – |
| `OPENKNOW_PASSWORD` | Microsoft 365 password (overrides credentials file) | – |

---

## Architecture

```
openknow/
├── openknow/
│   ├── __init__.py       # Package version
│   ├── cli.py            # Click CLI entry point
│   ├── config.py         # Configuration, paths, credential storage
│   ├── workspace.py      # SQLite-backed workspace/link/file memory
│   ├── graph_client.py   # OneDrive (requests) + SharePoint (Office365) clients
│   ├── downloader.py     # File sync, path-traversal protection, opencode indexing
│   └── webapp.py         # Flask chat web UI
├── openknow/templates/
│   └── index.html        # Chat UI template
└── tests/
    ├── test_workspace.py
    ├── test_graph_client.py
    ├── test_downloader.py
    ├── test_cli.py
    └── test_webapp.py
```

### Local Memory (SQLite)

Stored in `~/.openknow/openknow.db`:

- **workspaces** – named knowledge spaces with descriptions
- **workspace_links** – 1-5 OneDrive/SharePoint URLs per workspace
- **file_cache** – record of synced files, keyed per `(link_id, remote_path)` so two links with the same filename don't overwrite each other

---

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

---

## License

MIT License – see [LICENSE](LICENSE).
