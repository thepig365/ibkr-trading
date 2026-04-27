"""Resend readiness helper (no network, no secrets in output)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from bot.config import load_config
from bot.reports.email_config_status import build_email_config_status

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


def test_env_resend_from_plus_config_recipient_is_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(tmp_path)
    monkeypatch.setenv("RESEND_API_KEY", "re_example")
    monkeypatch.setenv("REPORT_EMAIL_FROM", "onboarding@resend.dev")
    monkeypatch.delenv("REPORT_EMAIL_TO", raising=False)
    cfg = load_config(project_root=tmp_path)
    d = build_email_config_status(cfg)
    assert d["resend_api_key_present"] is True
    assert d["report_email_from_present"] is True
    assert d["report_email_to_present"] is False
    assert d["reports_email_to_configured"] is True
    assert d["email_resend_configured"] is True
    assert d["missing_fields"] == []


def test_missing_resend_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(tmp_path)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("REPORT_EMAIL_FROM", "onboarding@resend.dev")
    cfg = load_config(project_root=tmp_path)
    d = build_email_config_status(cfg)
    assert d["email_resend_configured"] is False
    assert "RESEND_API_KEY" in d["missing_fields"]


def test_missing_from(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(tmp_path)
    monkeypatch.setenv("RESEND_API_KEY", "re_example")
    monkeypatch.delenv("REPORT_EMAIL_FROM", raising=False)
    cfg = load_config(project_root=tmp_path)
    d = build_email_config_status(cfg)
    assert d["email_resend_configured"] is False
    assert "REPORT_EMAIL_FROM" in d["missing_fields"]


def test_gmail_from_sets_verification_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(tmp_path)
    monkeypatch.setenv("RESEND_API_KEY", "re_example")
    monkeypatch.setenv("REPORT_EMAIL_FROM", "me@gmail.com")
    cfg = load_config(project_root=tmp_path)
    d = build_email_config_status(cfg)
    assert d["from_address_may_need_resend_verification"] is True


def test_json_has_no_long_secret_like_strings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(tmp_path)
    monkeypatch.setenv("RESEND_API_KEY", "x" * 80)
    monkeypatch.setenv("REPORT_EMAIL_FROM", "a@b.co")
    cfg = load_config(project_root=tmp_path)
    d = build_email_config_status(cfg)
    raw = json.dumps(d)
    assert len(raw) < 2000
    assert "x" * 40 not in raw  # env value must not appear in structured output
