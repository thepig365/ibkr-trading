"""Journal UI page (Prompt 13F PART E).

Tests:

* /journal renders 200 with an empty project (no paper orders yet).
* /journal reads ``data/paper_orders/*.jsonl`` and renders rows.
* The journal route does not import the broker / IBKR client.
* The journal template never invites the user to place an order.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    return tmp_path


def _client(project_root: Path) -> TestClient:
    state = LocalFileStateStore(project_root)
    queue = LocalCommandRunner(
        project_root=project_root,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=project_root / "ui_audit.jsonl",
    )
    app = create_app(project_root=project_root, state_store=state, command_queue=queue)
    return TestClient(app)


def _write_paper_order(
    project: Path,
    *,
    symbol: str = "AAPL",
    submitted: bool = True,
    direction: str = "long",
    signal_category: str = "DAY_TRADE_READY_STRICT",
    order_ids: list[int] | None = None,
    skipped_reasons: list[str] | None = None,
) -> Path:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = project / "data" / "paper_orders" / f"{day}-intraday-paper-orders.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strategy_id": "ict_smc_intraday_v1",
        "symbol": symbol,
        "direction": direction,
        "signal_category": signal_category,
        "submitted": submitted,
        "skipped_reasons": skipped_reasons or [],
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
        "planned_rr": 2.0,
        "quantity": 10,
        "order_ids": order_ids if order_ids is not None else ([1, 2, 3] if submitted else []),
        "paper_only": True,
        "live_trading_allowed": False,
        "source_scan_path": f"data/intraday_smc/{day}-{symbol}-intraday-smc.json",
        "chart_paths": [
            f"data/debug_charts/{day}-{symbol}-intraday-5min.png",
            f"data/debug_charts/{day}-{symbol}-intraday-30min.png",
        ],
    }
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return out


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


def test_journal_page_returns_200_with_empty_project(project: Path) -> None:
    r = _client(project).get("/journal")
    assert r.status_code == 200, r.text
    assert "Trade Journal" in r.text
    # Empty-state copy must hint at the right CLI commands.
    assert "Empty" in r.text or "No paper" in r.text or "auto-paper-intraday-smc" in r.text


def test_journal_link_present_in_navigation(project: Path) -> None:
    r = _client(project).get("/dashboard")
    assert r.status_code == 200
    assert "/journal" in r.text


def test_journal_page_shows_tif_and_sizing_columns(project: Path) -> None:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = project / "data" / "paper_orders" / f"{day}-intraday-paper-orders.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strategy_id": "ict_smc_intraday_v1",
        "symbol": "TIF1",
        "direction": "long",
        "signal_category": "DAY_TRADE_READY_STRICT",
        "submitted": True,
        "submitted_to_broker": True,
        "skipped_reasons": [],
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
        "planned_rr": 2.0,
        "quantity": 5,
        "order_ids": [1, 2, 3],
        "paper_only": True,
        "live_trading_allowed": False,
        "tif": "DAY",
        "estimated_notional": 500.0,
        "bracket_integrity": "complete",
        "sizing_audit": {
            "final_quantity": 5,
            "estimated_notional": 500.0,
            "per_trade_cap_applied": True,
        },
    }
    out.write_text(json.dumps(row) + "\n", encoding="utf-8")
    r = _client(project).get("/journal")
    assert r.status_code == 200, r.text
    text = r.text
    assert "TIF1" in text
    assert "DAY" in text
    assert "500" in text


def test_journal_page_renders_paper_order_row(project: Path) -> None:
    _write_paper_order(project, submitted=True, symbol="NVDA")
    r = _client(project).get("/journal")
    assert r.status_code == 200, r.text
    text = r.text
    assert "NVDA" in text
    assert "ict_smc_intraday_v1" in text
    assert "complete" in text.lower() or "submitted" in text.lower()
    assert "100.00" in text  # entry
    assert "99.00" in text   # stop
    assert "102.00" in text  # target
    assert "2.00" in text    # planned R/R
    # Strict label appears as a "strict" pill.
    assert "strict" in text.lower()


def test_journal_page_shows_incomplete_bracket_warning(project: Path) -> None:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = project / "data" / "paper_orders" / f"{day}-intraday-paper-orders.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strategy_id": "ict_smc_intraday_v1",
        "symbol": "AMD",
        "direction": "long",
        "signal_category": "DAY_TRADE_READY_STRICT",
        "submitted": False,
        "submitted_to_broker": True,
        "skipped_reasons": ["bracket incomplete"],
        "entry": 346.63,
        "stop": 345.41,
        "target": 348.45,
        "planned_rr": 1.5,
        "quantity": 1,
        "order_ids": [5, 6, 7],
        "paper_only": True,
        "live_trading_allowed": False,
        "original_entry": 346.63,
        "original_stop": 345.4167,
        "original_target": 348.45,
        "min_tick": "0.01",
        "min_tick_source": "contract_details",
        "bracket_integrity": "incomplete",
        "broker_error_codes": [110],
        "verify_in_tws_required": True,
    }
    with out.open("w", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    r = _client(project).get("/journal")
    assert r.status_code == 200
    text = r.text
    assert "incomplete" in text.lower()
    assert "345.4167" in text or "345.42" in text


def test_journal_page_renders_skipped_rows_with_reasons(project: Path) -> None:
    _write_paper_order(
        project,
        symbol="TSLA",
        submitted=False,
        signal_category="DAY_TRADE_READY_AGGRESSIVE",
        order_ids=[],
        skipped_reasons=["kill switch active", "duplicate paper entry"],
    )
    r = _client(project).get("/journal")
    assert r.status_code == 200
    text = r.text
    assert "TSLA" in text
    assert "skipped" in text.lower()
    assert "kill switch active" in text
    assert "duplicate paper entry" in text


def test_journal_page_renders_multiple_files(project: Path) -> None:
    """Two .jsonl files (e.g. yesterday + today) should both contribute rows."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    older_name = "2026-04-01-intraday-paper-orders.jsonl"
    p1 = project / "data" / "paper_orders" / older_name
    p1.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": "2026-04-01T18:00:00Z",
        "strategy_id": "ict_smc_intraday_v1",
        "symbol": "OLDER",
        "direction": "long",
        "signal_category": "DAY_TRADE_READY_STRICT",
        "submitted": True,
        "skipped_reasons": [],
        "entry": 50.0,
        "stop": 49.0,
        "target": 52.0,
        "planned_rr": 2.0,
        "quantity": 5,
        "order_ids": [11, 12, 13],
        "paper_only": True,
        "live_trading_allowed": False,
    }
    with p1.open("w", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    _write_paper_order(project, symbol="NEWER")
    r = _client(project).get("/journal")
    assert "OLDER" in r.text
    assert "NEWER" in r.text


# ---------------------------------------------------------------------------
# UI / broker decoupling
# ---------------------------------------------------------------------------


def test_journal_route_module_does_not_import_broker_or_ibkr() -> None:
    """The /journal handler is a pure-render route. Importing it must not
    cause :mod:`bot.broker` / :mod:`bot.ibkr_client` to load."""
    import importlib
    import sys as _sys
    from typing import Any

    to_clear = [
        k for k in list(_sys.modules)
        if k in {"bot.broker", "bot.ibkr_client"}
        or k.startswith("ib_async")
        or k.startswith("ib_insync")
    ]
    removed: dict[str, Any] = {k: _sys.modules.pop(k) for k in to_clear if k in _sys.modules}
    try:
        importlib.import_module("bot_ui.routes.journal")
        leaked = [
            m for m in _sys.modules
            if m in {"bot.broker", "bot.ibkr_client"}
            or m.startswith("ib_async")
            or m.startswith("ib_insync")
        ]
        assert leaked == [], f"/journal route leaked broker imports: {leaked}"
    finally:
        _sys.modules.update(removed)


def test_journal_template_does_not_have_order_submit_form(project: Path) -> None:
    """/journal is read-only — no <form action=...api/commands/run>."""
    import re as _re

    _write_paper_order(project, symbol="AAPL")
    r = _client(project).get("/journal")
    assert r.status_code == 200
    text = r.text
    assert "/api/commands/run" not in text, (
        "/journal must be read-only — no command-runner forms allowed."
    )
    # And no actionable CTA may invite live / market actions. Reassuring
    # informational text ("live trading is hard-blocked") is allowed.
    cta_re = _re.compile(
        r"<(?:button|a)\b[^>]*>([^<]*)</(?:button|a)>", _re.IGNORECASE,
    )
    forbidden_phrases = (
        "place order",
        "place_order",
        "market order",
        "go live",
        "enable live",
        "submit live",
        "live trade",
        "live trading",
    )
    for m in cta_re.finditer(text):
        label = (m.group(1) or "").strip().lower()
        for bad in forbidden_phrases:
            assert bad not in label, (
                f"/journal exposes a CTA labelled {label!r} — forbidden."
            )


def test_journal_sizing_fields_only_under_show_sizing_details_fold(
    project: Path,
) -> None:
    """Long sizing keys must not appear before the fold summary (main row compact)."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = project / "data" / "paper_orders" / f"{day}-intraday-paper-orders.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strategy_id": "ict_smc_intraday_v1",
        "symbol": "SZ1",
        "direction": "long",
        "signal_category": "DAY_TRADE_READY_STRICT",
        "submitted": True,
        "skipped_reasons": [],
        "entry": 10.0,
        "stop": 9.5,
        "target": 11.0,
        "planned_rr": 1.0,
        "quantity": 2,
        "estimated_notional": 20.0,
        "order_ids": [101],
        "paper_only": True,
        "live_trading_allowed": False,
        "sizing_audit": {
            "risk_based_quantity": 2.5,
            "per_trade_notional_cap_quantity": 1,
            "daily_remaining_quantity": 10,
            "account_cap_quantity": 50,
            "final_quantity": 2,
            "estimated_notional": 20.0,
            "per_trade_cap_applied": True,
            "daily_cap_applied": False,
            "account_cap_applied": False,
            "quantity_cap_applied": False,
        },
    }
    out.write_text(json.dumps(row) + "\n", encoding="utf-8")
    html = _client(project).get("/journal").text
    assert "Show sizing details" in html
    pos_fold = html.index("Show sizing details")
    key_pos = html.index("risk_based_quantity")
    assert key_pos > pos_fold
    zh = _client(project).get("/journal?lang=zh").text
    assert "展开仓位细节" in zh


def test_journal_view_aggregates_state_store(project: Path) -> None:
    """The /journal route delegates to ``state_store.get_journal_view``;
    verify that helper round-trips the on-disk file."""
    _write_paper_order(project, symbol="META", submitted=True)
    state = LocalFileStateStore(project)
    view = state.get_journal_view(limit=10)
    assert len(view.paper_orders) >= 1
    assert any(r.symbol == "META" and r.submitted for r in view.paper_orders)
    # The aggregated row must preserve the paper-only invariant.
    for r in view.paper_orders:
        assert r.paper_only is True
        assert r.live_trading_allowed is False
