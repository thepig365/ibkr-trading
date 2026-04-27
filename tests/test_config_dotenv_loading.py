"""Explicit project-root .env loading; no find_dotenv."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from bot.config import get_dotenv_load_warning, load_config

REPO = Path(__file__).resolve().parent.parent


def _install(tmp: Path) -> None:
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    for n in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
        "strategy_ui.yaml",
    ):
        s = REPO / "config" / n
        if s.is_file():
            shutil.copy(s, tmp / "config" / n)
    (tmp / "data").mkdir(parents=True, exist_ok=True)


def test_load_dotenv_exception_does_not_crash_load_config(tmp_path: Path) -> None:
    _install(tmp_path)
    (tmp_path / ".env").write_text("RESEND_API_KEY=will_not_load_if_mock_raises\n", encoding="utf-8")
    with patch("bot.config.load_dotenv", side_effect=RuntimeError("simulated")):
        cfg = load_config(project_root=tmp_path)
    assert cfg.project_root == tmp_path.resolve()
    w = get_dotenv_load_warning()
    assert w is not None
    assert "dotenv" in w
    assert "RuntimeError" in w


def test_explicit_env_path_used_not_find_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: stdin / odd CWD must not rely on find_dotenv (can assert)."""
    _install(tmp_path)
    (tmp_path / ".env").write_text(
        "RESEND_API_KEY=abc123\nREPORT_EMAIL_FROM=onboarding@resend.dev\n",
        encoding="utf-8",
    )
    for k in (
        "RESEND_API_KEY",
        "REPORT_EMAIL_FROM",
        "REPORT_EMAIL_TO",
    ):
        monkeypatch.delenv(k, raising=False)
    from bot.reports.email_config_status import build_email_config_status

    cfg = load_config(project_root=tmp_path)
    d = build_email_config_status(cfg)
    assert d["resend_api_key_present"] is True
    assert d["report_email_from_present"] is True
