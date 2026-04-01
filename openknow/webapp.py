"""Flask web application providing the OpenKnow chat UI.

Serves a ChatGPT-style interface that lets users ask questions about
knowledge files that have been downloaded locally from OneDrive/SharePoint.

Answers are produced by:
1. Searching the downloaded text/document files for relevant content.
2. Delegating to the opencode CLI for AI-powered synthesis when available.
"""

import json
import re
import subprocess
import shutil
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from .config import get_download_dir
from .workspace import init_db, list_cached_files, list_workspaces


def create_app(default_workspace: Optional[str] = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        default_workspace: Pre-select a workspace in the UI.

    Returns:
        Configured Flask application.
    """
    app = Flask(__name__, template_folder="templates")
    app.config["DEFAULT_WORKSPACE"] = default_workspace
    init_db()

    @app.route("/")
    def index():
        workspaces = list_workspaces()
        default_ws = app.config.get("DEFAULT_WORKSPACE") or (workspaces[0]["name"] if workspaces else "")
        return render_template("index.html", workspaces=workspaces, default_workspace=default_ws)

    @app.route("/api/workspaces")
    def api_workspaces():
        workspaces = list_workspaces()
        return jsonify(workspaces)

    @app.route("/api/files/<workspace_name>")
    def api_files(workspace_name: str):
        try:
            cached = list_cached_files(workspace_name)
            return jsonify(cached)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/chat", methods=["POST"])
    def api_chat():
        """Handle a chat message and return the agent's answer."""
        data = request.get_json(force=True)
        question = (data.get("question") or "").strip()
        workspace_name = (data.get("workspace") or "").strip()

        if not question:
            return jsonify({"error": "No question provided."}), 400

        try:
            answer = _answer_question(question, workspace_name)
        except Exception as exc:
            answer = f"An error occurred while processing your question: {exc}"

        return jsonify({"answer": answer, "workspace": workspace_name})

    return app


# ---------------------------------------------------------------------------
# Knowledge retrieval
# ---------------------------------------------------------------------------

_READABLE_SUFFIXES = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".log", ".py", ".js", ".ts"}
_MAX_CONTEXT_CHARS = 8000  # cap to avoid overwhelming the CLI


def _answer_question(question: str, workspace_name: str) -> str:
    """Answer a user question using locally downloaded files as knowledge.

    Strategy:
    1. Load all readable text files from the workspace.
    2. Score each file's relevance to the question using simple keyword matching.
    3. Build a context string from the most relevant excerpts.
    4. If opencode is available, delegate the final answer to opencode with
       the context injected. Otherwise, return the relevant excerpts directly.

    Args:
        question: The user's question.
        workspace_name: Workspace whose downloaded files to search.

    Returns:
        Answer string.
    """
    try:
        cached_files = list_cached_files(workspace_name) if workspace_name else []
    except Exception:
        cached_files = []

    context_parts = []
    context_chars = 0

    if cached_files:
        keywords = _extract_keywords(question)
        scored = []
        for entry in cached_files:
            local_path = Path(entry.get("local_path", ""))
            if not local_path.exists():
                continue
            if local_path.suffix.lower() not in _READABLE_SUFFIXES:
                continue
            try:
                text = local_path.read_text(errors="replace")
            except OSError:
                continue
            score = _score_relevance(text, keywords)
            if score > 0:
                scored.append((score, local_path.name, text))

        scored.sort(key=lambda x: x[0], reverse=True)
        for _score, fname, text in scored[:3]:
            excerpt = _extract_excerpt(text, keywords, max_chars=2000)
            part = f"--- From: {fname} ---\n{excerpt}"
            if context_chars + len(part) > _MAX_CONTEXT_CHARS:
                break
            context_parts.append(part)
            context_chars += len(part)

    context = "\n\n".join(context_parts)

    # Try opencode for a better answer
    opencode_bin = shutil.which("opencode")
    if opencode_bin:
        return _ask_opencode(question, context, workspace_name, opencode_bin)

    # Fallback: return the raw excerpts
    if context:
        return (
            f"Based on the downloaded knowledge:\n\n{context}\n\n"
            "*(Note: Install opencode for AI-powered answers.)*"
        )

    if not cached_files:
        return (
            f"No files have been downloaded for workspace '{workspace_name}'. "
            f"Run 'openknow sync {workspace_name}' to download knowledge files."
        )

    return (
        "No relevant content found for your question in the downloaded files. "
        "Try rephrasing or syncing more documents."
    )


def _ask_opencode(question: str, context: str, workspace_name: str, opencode_bin: str) -> str:
    """Delegate the question to opencode with the extracted context.

    Args:
        question: User's question.
        context: Extracted text excerpts from knowledge files.
        workspace_name: Workspace name (used as opencode context label).
        opencode_bin: Path to the opencode binary.

    Returns:
        Answer from opencode, or a fallback message on failure.
    """
    prompt = question
    if context:
        prompt = (
            f"Use the following information from the knowledge base to answer the question.\n\n"
            f"Knowledge base (workspace: {workspace_name}):\n{context}\n\n"
            f"Question: {question}"
        )

    try:
        result = subprocess.run(
            [opencode_bin, "ask", prompt, "--context", workspace_name],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        stderr = result.stderr.strip()
        if stderr:
            return f"opencode error: {stderr}"
    except subprocess.TimeoutExpired:
        return "opencode timed out. Please try a shorter question."
    except OSError as exc:
        return f"Failed to run opencode: {exc}"

    # If opencode returned nothing, fall back to context excerpts
    if context:
        return f"Based on the downloaded knowledge:\n\n{context}"
    return "No relevant content found."


def _extract_keywords(question: str) -> list:
    """Extract meaningful keywords from a question (simple tokenizer)."""
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "do", "does", "did",
        "what", "when", "where", "who", "why", "how", "can", "could", "will",
        "would", "should", "this", "that", "these", "those", "of", "in",
        "on", "at", "to", "for", "with", "by", "from", "about",
    }
    tokens = re.findall(r"\w+", question.lower())
    return [t for t in tokens if len(t) > 2 and t not in stop_words]


def _score_relevance(text: str, keywords: list) -> int:
    """Count keyword occurrences in text (case-insensitive)."""
    lower = text.lower()
    return sum(lower.count(kw) for kw in keywords)


def _extract_excerpt(text: str, keywords: list, max_chars: int = 1500) -> str:
    """Extract the most relevant excerpt from text for the given keywords."""
    if len(text) <= max_chars:
        return text

    lower = text.lower()
    best_pos = 0
    best_score = 0

    window = max_chars // 2
    for kw in keywords:
        pos = lower.find(kw)
        while pos != -1:
            start = max(0, pos - window)
            end = min(len(text), pos + window)
            score = sum(lower[start:end].count(k) for k in keywords)
            if score > best_score:
                best_score = score
                best_pos = start
            pos = lower.find(kw, pos + 1)

    start = max(0, best_pos)
    end = min(len(text), start + max_chars)
    excerpt = text[start:end]

    if start > 0:
        excerpt = "..." + excerpt
    if end < len(text):
        excerpt = excerpt + "..."

    return excerpt
