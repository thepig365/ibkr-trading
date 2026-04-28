"""Dashboard cumulative R curve, USD gate, Edge health — file-only."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore


def _client(root: Path) -> TestClient:
    (root / "data").mkdir(exist_ok=True)
    state = LocalFileStateStore(root)
    queue = LocalCommandRunner(
        project_root=root,
        python_executable=sys.executable,
        timeout_seconds=20,
        audit_file=root / "ui_audit.jsonl",
    )
    return TestClient(
        create_app(project_root=root, state_store=state, command_queue=queue)
    )


def test_dashboard_shows_cumulative_r_curve_heading_en(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    assert "Cumulative R curve" in r.text
    assert "Trading journal core" in r.text
    assert "Edge health" in r.text


def test_dashboard_zh_shows_cum_r_zh(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard?lang=zh")
    assert r.status_code == 200
    assert "累计R曲线" in r.text


def test_dashboard_no_closed_trades_empty_state(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    assert "Not enough closed trades yet. The R curve will appear" in r.text


def test_dashboard_no_fake_usd_curve_without_pnl(tmp_path: Path) -> None:
    """USD card shows the hidden message when reliable USD is absent."""
    body = _client(tmp_path).get("/dashboard").text
    assert "USD equity curve is hidden until reliable realized USD" in body


def test_dashboard_edge_health_and_links(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    assert "Edge health" in r.text
    assert 'href="' in r.text and "/trades" in r.text
    assert "/reports" in r.text


def test_dashboard_developer_diagnostics_collapsed(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard")
    assert "<details" in r.text
    assert "Developer / Engine Diagnostics" in r.text


def test_dashboard_ibkr_safe_on_get(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[object] = []

    def _spy(*_a, **_k):
        calls.append(1)

    monkeypatch.setattr(
        "bot.ibkr_connection.connect_readonly_roster_retry",
        _spy,
    )
    assert _client(tmp_path).get("/dashboard").status_code == 200
    assert calls == []


def test_dashboard_cum_r_polyline_when_two_closed(tmp_path: Path) -> None:
    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    base = {
        "symbol": "SPY",
        "submitted": True,
        "direction": "long",
        "strategy_id": "ict_smc_intraday_v1",
        "signal_category": "x",
        "entry": 100.0,
        "stop": 99.0,
        "skipped_reasons": [],
    }
    rows = [
        {
            **base,
            "timestamp": "2026-05-01T10:00:00",
            "exit_time": "2026-05-01T15:00:00",
            "exit_price": 101.0,
            "realized_r": 1.0,
        },
        {
            **base,
            "timestamp": "2026-05-02T10:00:00",
            "exit_time": "2026-05-02T15:00:00",
            "exit_price": 100.5,
            "realized_r": -0.5,
        },
    ]
    (pod / "2026-test-intraday-paper-orders.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    html = _client(tmp_path).get("/dashboard").text
    assert '<polyline' in html
    assert 'Not enough closed trades yet. The R curve will appear' not in html
    assert "Total R" in html
