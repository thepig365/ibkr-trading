"""Tests for the Telegram notification adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import yaml

from bot.config import load_config
from bot.notifications import notify_event, send_telegram_message
from bot.notifications import telegram as telegram_mod


# ---------------------------------------------------------------------------
# Fallback behaviour
# ---------------------------------------------------------------------------
def test_missing_env_falls_back_to_daily_summary(
    tmp_project: Path, capsys: pytest.CaptureFixture
) -> None:
    cfg = load_config(project_root=tmp_project)
    assert cfg.telegram.is_configured is False

    ok = send_telegram_message("hello world", cfg=cfg)
    assert ok is False

    summary_path = cfg.absolute(cfg.settings.paths.daily_summary_md)
    assert summary_path.exists()
    assert "hello world" in summary_path.read_text(encoding="utf-8")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "telegram" in combined.lower()
    assert "DAILY-SUMMARY" in combined or "memory/" in combined


def test_disabled_in_settings_writes_fallback(tmp_project: Path, write_yaml) -> None:
    settings_path = tmp_project / "config" / "settings.yaml"
    settings = yaml.safe_load(settings_path.read_text())
    settings["notifications"]["telegram"]["enabled"] = False
    write_yaml(settings_path, settings)
    cfg = load_config(project_root=tmp_project)

    ok = send_telegram_message("disabled-test", cfg=cfg)
    assert ok is False
    summary_path = cfg.absolute(cfg.settings.paths.daily_summary_md)
    assert "disabled-test" in summary_path.read_text(encoding="utf-8")


def test_network_error_does_not_crash(monkeypatch, tmp_project: Path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tkn")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    def fake_post(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "post", fake_post)
    cfg = load_config(project_root=tmp_project)
    assert cfg.telegram.is_configured is True

    ok = send_telegram_message("net-error", cfg=cfg)
    assert ok is False
    summary_path = cfg.absolute(cfg.settings.paths.daily_summary_md)
    assert "net-error" in summary_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Payload formatting
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status_code: int = 200, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {"ok": True}
        self.text = str(self._body)

    def json(self) -> dict:
        return self._body


def test_successful_send_uses_html_parse_mode(monkeypatch, tmp_project: Path) -> None:
    """Payload must include parse_mode=HTML and the chat/text fields."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234567890:ABCDEF")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    cfg = load_config(project_root=tmp_project)

    ok = send_telegram_message("<b>ping</b>", cfg=cfg)
    assert ok is True
    assert captured["url"].startswith("https://api.telegram.org/bot")
    assert captured["json"]["chat_id"] == "42"
    assert captured["json"]["parse_mode"] == "HTML"
    assert captured["json"]["disable_web_page_preview"] is True
    assert "ping" in captured["json"]["text"]


def test_notify_event_formats_html_message(monkeypatch, tmp_project: Path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tkn:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c1")

    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured["json"] = json
        return _FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    cfg = load_config(project_root=tmp_project)

    notify_event(
        event_type="test.event",
        title="Hello <world>",
        body="line1 & line2",
        severity="warning",
        cfg=cfg,
    )

    text = captured["json"]["text"]
    assert "<b>" in text
    assert "[WARN]" in text
    assert "Hello &lt;world&gt;" in text  # HTML-escaped
    assert "line1 &amp; line2" in text
    assert "test.event" in text


def test_notify_event_rejects_unknown_severity(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    with pytest.raises(ValueError):
        notify_event(
            event_type="x", title="t", body="b", severity="critical", cfg=cfg  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Privacy mode
# ---------------------------------------------------------------------------
def test_privacy_mode_redacts_account_and_dollar(monkeypatch, tmp_project: Path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tkn:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c1")

    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured["json"] = json
        return _FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    cfg = load_config(project_root=tmp_project)

    msg = (
        "Account DU1234567 NetLiquidation: 102345.67 "
        "TotalCash=42000 BuyingPower=$250,000 api_key=sekret"
    )
    send_telegram_message(msg, cfg=cfg)

    sent = captured["json"]["text"]
    assert "DU1234567" not in sent
    assert "DU***67" in sent or "DU****" in sent
    assert "102345.67" not in sent
    assert "42000" not in sent
    assert "$250,000" not in sent
    assert "sekret" not in sent
    assert "NetLiquidation=***" in sent or "NetLiquidation =***" in sent or "Net Liquidation=***" in sent


def test_privacy_mode_off_does_not_redact(
    monkeypatch, tmp_project: Path, write_yaml
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tkn:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c1")

    settings_path = tmp_project / "config" / "settings.yaml"
    settings = yaml.safe_load(settings_path.read_text())
    settings["notifications"]["telegram"]["privacy_mode"] = False
    write_yaml(settings_path, settings)

    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured["json"] = json
        return _FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    cfg = load_config(project_root=tmp_project)

    send_telegram_message("DU1234567 has $1,000.00", cfg=cfg)
    sent = captured["json"]["text"]
    assert "DU1234567" in sent
    assert "$1,000.00" in sent


def test_redact_function_handles_bot_token() -> None:
    text = "token=1234567890:ABCDEF_ghij-klmno_pqrstuvwxyz012345"
    out = telegram_mod._redact(text)
    assert "ABCDEF" not in out
    assert "token=***" in out or "***" in out


# ---------------------------------------------------------------------------
# CLI: test-telegram does not touch IBKR
# ---------------------------------------------------------------------------
def test_test_telegram_command_does_not_connect_to_ibkr(
    monkeypatch, tmp_project: Path
) -> None:
    from typer.testing import CliRunner

    from bot import cli as cli_module
    from bot.ibkr_client import IBKRClient

    def boom(*args, **kwargs):
        raise AssertionError("test-telegram must not connect to IBKR")

    monkeypatch.setattr(IBKRClient, "connect", boom)
    monkeypatch.setattr(IBKRClient, "disconnect", boom)
    monkeypatch.setattr(
        cli_module, "load_config", lambda: load_config(project_root=tmp_project)
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["test-telegram"])
    # No credentials in tmp_project env -> fallback path -> exit code 4.
    assert result.exit_code == 4, result.stdout
    summary = (tmp_project / "memory" / "DAILY-SUMMARY.md").read_text(encoding="utf-8")
    assert "Telegram connectivity test" in summary
