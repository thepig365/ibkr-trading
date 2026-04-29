"""TWS health alert helpers — no orders; IBKR mocked or snapshot-only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bot.config import TelegramEnv
from bot.tws_health_alerts import (
    ALERT_NOT_PAPER,
    ALERT_TW_PORT_DOWN,
    TWS_HEALTH_ALERT_STATE_RELPATH,
    TWSHealthStatus,
    check_tws_health_for_alerts,
    health_status_from_broker_snapshot,
    maybe_send_tws_health_alert,
)


def _mock_cfg(root: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.project_root = Path(root).resolve()
    cfg.settings.account.mode = "paper"
    cfg.settings.trading.intraday_paper.require_reconciliation_pass = False
    tac = MagicMock()
    tac.enabled = True
    tac.min_interval_minutes = 15
    tac.send_recovery = True
    cfg.settings.trading.tws_health_alerts = tac
    cfg.ibkr.host = "127.0.0.1"
    cfg.ibkr.port = 7497
    cfg.ibkr.account_mode = "paper"
    cfg.telegram = TelegramEnv(bot_token="dummy", chat_id="1")
    return cfg


def test_port_down_status(monkeypatch: pytest.MonkeyPatch, tmp_project: Path) -> None:
    monkeypatch.setattr(
        "bot.tws_health_alerts.tws_port_listening", lambda *_a, **_k: False,
    )
    cfg = _mock_cfg(tmp_project)
    st = check_tws_health_for_alerts(cfg, None)
    assert st.alert_code == ALERT_TW_PORT_DOWN
    assert st.status == "port_down"


def test_broker_snap_error_maps_connect_failed(tmp_project: Path) -> None:
    st = health_status_from_broker_snapshot(
        {
            "checked_at_utc": "2026-01-02T01:02:03Z",
            "status": "unavailable",
            "error_summary": "Connection refused [Errno 61]",
            "tws_listening": False,
            "ibkr_connected": False,
        },
    )
    assert st.alert_code == ALERT_TW_PORT_DOWN
    assert st.status == "port_down"


def test_broker_snap_lt_blocked_maps_not_paper(tmp_project: Path) -> None:
    st = health_status_from_broker_snapshot(
        {
            "status": "error",
            "checked_at_utc": "2026-01-02T01:02:03Z",
            "error_summary": "LiveTradingBlocked: refuse live",
            "paper_account": True,
            "ibkr_connected": False,
            "tws_listening": True,
        },
    )
    assert st.alert_code == ALERT_NOT_PAPER


def test_unhealthy_sent_once_duplicate_throttled(tmp_project: Path) -> None:
    cfg = _mock_cfg(tmp_project)
    path = Path(tmp_project) / TWS_HEALTH_ALERT_STATE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    iso = recent.strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(
        '{"was_alerting": true, '
        '"last_sent_alert_code": "tws_port_down", '
        '"last_sent_alert_at_utc": "'
        + iso
        + "\"}\n",
        encoding="utf-8",
    )
    dup = TWSHealthStatus(
        checked_at_utc="2099-01-01T12:01:00Z",
        alert_code="tws_port_down",
        status="port_down",
        reason="still down",
    )
    with patch(
        "bot.notifications.telegram.send_telegram_message", side_effect=AssertionError(
            "telegram must not fire within throttle window"
        ),
    ):
        info = maybe_send_tws_health_alert(
            cfg, None, dup, source="manual", send_telegram=True,
        )
    assert info.get("skipped") or info.get("reason") == "throttled"


def test_recovery_sends_when_configured(tmp_project: Path) -> None:
    cfg = _mock_cfg(tmp_project)
    path = Path(tmp_project) / TWS_HEALTH_ALERT_STATE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"was_alerting": true, "last_sent_alert_code": "tws_port_down"}\n',
        encoding="utf-8",
    )
    ok = TWSHealthStatus(checked_at_utc="2099-01-02Z", status="healthy", alert_code=None)
    msgs: list[str] = []

    def _capt(body: str, **kwargs: object) -> None:
        msgs.append(body)

    with patch("bot.notifications.telegram.send_telegram_message", side_effect=_capt):
        info = maybe_send_tws_health_alert(cfg, None, ok, source="manual", send_telegram=True)
    assert info.get("recovery_sent") or any("恢复" in m or "recovery" in m.lower() or "恢复了" for m in msgs)


def test_telegram_failure_does_not_crash_maybe_send(tmp_project: Path) -> None:
    cfg = _mock_cfg(tmp_project)
    path = Path(tmp_project) / TWS_HEALTH_ALERT_STATE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{}\n', encoding="utf-8")
    bad = TWSHealthStatus(
        status="connect_failed",
        alert_code="tws_port_down",
        reason="probe",
        checked_at_utc="2099-01-01",
    )

    def _boom(*args: object, **kwargs: object) -> None:
        raise ConnectionError("no network")

    with patch(
        "bot.notifications.telegram.send_telegram_message", side_effect=_boom,
    ):
        maybe_send_tws_health_alert(
            cfg, None, bad, source="manual", send_telegram=True,
        )


def test_recovery_message_contains_no_secrets(tmp_project: Path) -> None:
    from bot.tws_health_alerts import format_recovery_telegram_zh

    body = format_recovery_telegram_zh(
        checked_at_utc="2026-06-06T08:09:10Z",
        paper_account=True,
        source="test",
    )
    low = body.lower()
    assert "token" not in low

