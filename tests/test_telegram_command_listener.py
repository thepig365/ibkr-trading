"""Telegram command listener CLI and persistence (no network in most tests)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.config import load_config
from bot.journal import Journal
from bot.telegram_commands import (
    Dispatcher,
    load_command_config,
    process_message,
    run_telegram_command_listener_main,
)
from bot.telegram_listener_state import (
    TelegramListenerFileState,
    load_state,
    save_state,
    state_path_for,
)


def test_status_reply_contains_readiness(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    d = Dispatcher(cfg=cfg, journal=journal, ci=ci)
    r = d.run("/status")
    assert r.status == "success"
    assert "readiness" in r.reply_zh or "【/status" in r.reply_zh


def test_news_no_spam_no_runner(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    calls: list[list[str]] = []

    def fake_runner(argv: list[str]) -> tuple[int, str]:
        calls.append(list(argv))
        return 0, ""

    d = Dispatcher(cfg=cfg, journal=journal, ci=ci, runner=fake_runner)
    r = d.run("/news")
    assert r.status == "success"
    assert calls == []


def test_reports_paths(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    d = Dispatcher(cfg=cfg, journal=journal, ci=ci)
    r = d.run("/reports")
    assert r.status == "success"
    assert "/reports" in r.reply_zh or "纸面" in r.reply_zh or "无" in r.reply_zh


def test_help_lists_core_commands(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    d = Dispatcher(cfg=cfg, journal=journal, ci=ci)
    r = d.run("/help")
    assert "/status" in r.reply_zh and "/ping" in r.reply_zh


def test_unknown_dedup_writes_state(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    sp = state_path_for(cfg.project_root)
    st = TelegramListenerFileState()
    d = Dispatcher(cfg=cfg, journal=journal, ci=ci, file_state=st, state_path=sp)
    d.run("/weird-once")
    save_state(sp, st)
    assert load_state(sp).unknown_last_text == "/weird-once"


def test_unauthorized_no_reply_payload(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    sent: list[dict] = []

    def fake_post(url, json=None, timeout=None, **kw):  # noqa: A002
        sent.append({"url": url})
        class R:
            status_code = 200
            def json(self_inner):
                return {"ok": True}
        return R()

    monkeypatch.setattr("httpx.post", fake_post)
    process_message(
        cfg, journal, ci, chat_id="99", text="/status",
    )
    assert sent == []


def test_forbidden_slash_commands(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    d = Dispatcher(cfg=cfg, journal=journal, ci=ci)
    for cmd in ("/trade", "/buy", "/sell", "/live", "/market"):
        r = d.run(cmd)
        assert r.status == "rejected"


def test_dry_run_main_no_token_printed(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "SECRET_X")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    out = run_telegram_command_listener_main(dry_run=True)
    assert isinstance(out, dict)
    assert out.get("dry_run") is True
    assert "SECRET" not in json.dumps(out)


def test_listener_does_not_place_orders(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    calls: list[list[str]] = []

    def fake_runner(argv: list[str]) -> tuple[int, str]:
        calls.append(list(argv))
        return 0, ""

    d = Dispatcher(cfg=cfg, journal=journal, ci=ci, runner=fake_runner)
    d.run("/status")
    d.run("/ping")
    assert not any("place" in " ".join(x).lower() for x in calls)
