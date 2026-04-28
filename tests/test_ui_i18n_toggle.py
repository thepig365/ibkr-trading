"""Language toggle: query, cookie, fallback (display only)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.i18n import COOKIE_NAME, get_locale
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore


def _client(root: Path) -> TestClient:
    (root / "data").mkdir(exist_ok=True)
    state = LocalFileStateStore(root)
    queue = LocalCommandRunner(
        project_root=root,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=root / "ui_audit.jsonl",
    )
    return TestClient(
        create_app(project_root=root, state_store=state, command_queue=queue)
    )


def test_unknown_lang_defaults_to_english(tmp_project: Path) -> None:
    c = _client(tmp_project)
    r = c.get("/dashboard?lang=de")
    assert r.status_code == 200
    assert 'lang="en"' in r.text
    assert "交易员驾驶舱" not in r.text


def test_lang_switcher_present(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/dashboard")
    assert r.status_code == 200
    assert "?lang=en" in r.text
    assert "?lang=zh" in r.text


def test_cookie_persists_locale(tmp_project: Path) -> None:
    c = _client(tmp_project)
    r1 = c.get("/dashboard?lang=zh")
    assert r1.status_code == 200
    assert COOKIE_NAME in c.cookies
    assert c.cookies.get(COOKIE_NAME) == "zh"
    r2 = c.get("/dashboard")
    assert r2.status_code == 200
    assert "交易员驾驶舱" in r2.text
