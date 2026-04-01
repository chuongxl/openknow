"""Tests for the Flask web application."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from openknow.webapp import _extract_keywords, _score_relevance, _extract_excerpt, create_app
from openknow.workspace import add_link, create_workspace, init_db


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def app(tmp_path, db, monkeypatch):
    monkeypatch.setenv("OPENKNOW_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENKNOW_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    with patch("openknow.workspace.get_db_path", return_value=db), \
         patch("openknow.webapp.init_db", lambda: None):
        flask_app = create_app()
        flask_app.config["TESTING"] = True
        yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


class TestIndexRoute:
    def test_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_contains_openknow(self, client):
        resp = client.get("/")
        assert b"OpenKnow" in resp.data


class TestApiWorkspaces:
    def test_returns_list(self, client, db):
        with patch("openknow.webapp.list_workspaces") as mock_ws:
            mock_ws.return_value = [{"name": "proj", "link_count": 2}]
            resp = client.get("/api/workspaces")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)


class TestApiFiles:
    def test_returns_files_for_workspace(self, client, db):
        with patch("openknow.webapp.list_cached_files") as mock_files:
            mock_files.return_value = [
                {"remote_path": "doc.pdf", "local_path": "/tmp/doc.pdf", "file_size": 1024}
            ]
            resp = client.get("/api/files/proj")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) == 1

    def test_returns_error_for_bad_workspace(self, client, db):
        with patch("openknow.webapp.list_cached_files", side_effect=Exception("not found")):
            resp = client.get("/api/files/ghost")
        assert resp.status_code == 400


class TestApiChat:
    def test_empty_question_returns_400(self, client):
        resp = client.post(
            "/api/chat",
            json={"question": "", "workspace": "proj"},
        )
        assert resp.status_code == 400

    def test_returns_answer(self, client):
        with patch("openknow.webapp._answer_question", return_value="42"):
            resp = client.post(
                "/api/chat",
                json={"question": "What is the meaning?", "workspace": "proj"},
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["answer"] == "42"

    def test_returns_workspace_in_response(self, client):
        with patch("openknow.webapp._answer_question", return_value="ok"):
            resp = client.post(
                "/api/chat",
                json={"question": "test?", "workspace": "myws"},
            )
        data = json.loads(resp.data)
        assert data["workspace"] == "myws"


class TestExtractKeywords:
    def test_removes_stop_words(self):
        kws = _extract_keywords("What is the meaning of life?")
        assert "the" not in kws
        assert "what" not in kws
        assert "meaning" in kws
        assert "life" in kws

    def test_removes_short_words(self):
        kws = _extract_keywords("Is AI good?")
        assert all(len(k) > 2 for k in kws)


class TestScoreRelevance:
    def test_returns_zero_for_no_match(self):
        assert _score_relevance("hello world", ["python", "code"]) == 0

    def test_counts_occurrences(self):
        score = _score_relevance("python python code python", ["python"])
        assert score == 3

    def test_multiple_keywords(self):
        score = _score_relevance("python code is good code", ["python", "code"])
        assert score == 3  # 1 python + 2 code


class TestExtractExcerpt:
    def test_short_text_returned_as_is(self):
        text = "short text"
        assert _extract_excerpt(text, ["short"], max_chars=100) == text

    def test_excerpt_contains_keyword(self):
        text = "x" * 1000 + "target keyword here" + "y" * 1000
        excerpt = _extract_excerpt(text, ["target"], max_chars=200)
        assert "target" in excerpt

    def test_excerpt_within_max_chars(self):
        text = "a" * 5000
        excerpt = _extract_excerpt(text, ["a"], max_chars=500)
        assert len(excerpt) <= 506  # 500 chars + up to 6 for "..." prefix and suffix
