"""Tests for file-based daily paper report (Prompt 13M)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from bot.reports.paper_daily import build_daily_paper_report
from bot.reports.render_markdown import render_paper_daily_markdown


REPO = Path(__file__).resolve().parent.parent


def _install_config(target: Path) -> None:
    (target / "config").mkdir(parents=True, exist_ok=True)
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
    ):
        src = REPO / "config" / name
        if src.is_file():
            shutil.copy(src, target / "config" / name)


def test_daily_report_missing_files_graceful(tmp_path: Path) -> None:
    _install_config(tmp_path)
    r = build_daily_paper_report(tmp_path, "2026-01-15")
    assert r.get("data_status") in {"no_data", "partial_data", "ok"}
    assert r.get("date") == "2026-01-15"
    assert (r.get("execution_summary") or {}).get("paper_orders_count") == 0


def test_daily_reads_jsonl_and_counts(tmp_path: Path) -> None:
    _install_config(tmp_path)
    po = tmp_path / "data" / "paper_orders"
    po.mkdir(parents=True)
    line1 = {
        "symbol": "NVDA",
        "submitted_to_broker": True,
        "submitted": True,
        "skipped_reasons": [],
        "bracket_integrity": "complete",
        "estimated_notional": 5000.0,
        "quantity": 10,
        "entry": 100.0,
        "stop": 99.0,
        "planned_rr": 2.0,
    }
    line2 = {
        "symbol": "SPY",
        "submitted_to_broker": True,
        "submitted": False,
        "skipped_reasons": ["tick"],
        "bracket_integrity": "incomplete",
        "estimated_notional": 2000.0,
        "broker_error_codes": [110],
    }
    p = po / "2026-01-20-intraday-paper-orders.jsonl"
    p.write_text(json.dumps(line1) + "\n" + json.dumps(line2) + "\n", encoding="utf-8")
    r = build_daily_paper_report(tmp_path, "2026-01-20")
    ex = r.get("execution_summary") or {}
    assert ex.get("paper_orders_count") == 2
    assert ex.get("submitted_to_broker_count") == 2
    assert ex.get("complete_bracket_count") == 1
    assert ex.get("incomplete_bracket_count") == 1
    assert 110 in (ex.get("broker_error_codes") or [])
    assert (r.get("budget") or {}).get("today_submitted_notional_usd", 0) >= 7000.0


def test_daily_cap_detected(tmp_path: Path) -> None:
    _install_config(tmp_path)
    po = tmp_path / "data" / "paper_orders"
    po.mkdir(parents=True)
    p = po / "2026-01-21-intraday-paper-orders.jsonl"
    big = {
        "submitted_to_broker": True,
        "submitted": True,
        "bracket_integrity": "complete",
        "estimated_notional": 100_000.0,
    }
    p.write_text(json.dumps(big) + "\n", encoding="utf-8")
    r = build_daily_paper_report(tmp_path, "2026-01-21")
    assert (r.get("reasons") or {}).get("daily_cap_reached") is True


def test_scan_counts_in_report(tmp_path: Path) -> None:
    _install_config(tmp_path)
    smc = tmp_path / "data" / "intraday_smc"
    smc.mkdir(parents=True)
    summary = {
        "date": "2026-01-22",
        "symbols_scanned": 5,
        "counts": {
            "DAY_TRADE_READY_STRICT": 2,
            "DAY_TRADE_READY_AGGRESSIVE": 1,
            "WATCH_ONLY": 1,
            "INVALID_RISK": 0,
            "BLOCKED": 0,
            "NO_SETUP": 0,
            "ERROR": 0,
        },
        "ready_strict_symbols": ["A", "B"],
        "ready_aggressive_symbols": ["C"],
        "watch_symbols": ["Z"],
    }
    (smc / "2026-01-22-watchlist-intraday-smc-summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    r = build_daily_paper_report(tmp_path, "2026-01-22")
    sc = r.get("scan_summary") or {}
    assert sc.get("strict_ready_count") == 2
    assert sc.get("aggressive_ready_count") == 1
    assert sc.get("watch_only_count") == 1
    assert "A" in (sc.get("ready_symbols") or [])


def test_edge_profile_summary(tmp_path: Path) -> None:
    _install_config(tmp_path)
    ed = tmp_path / "data" / "edge_profiles"
    ed.mkdir(parents=True)
    payload = {
        "date": "2026-01-23",
        "profiles": [
            {
                "symbol": "X",
                "edge_score": 80.0,
                "recommended_mode": "strict_only",
            },
            {
                "symbol": "Y",
                "edge_score": 10.0,
                "recommended_mode": "watch_only",
            },
        ],
    }
    (ed / "2026-01-23-edge-profiles.json").write_text(json.dumps(payload), encoding="utf-8")
    r = build_daily_paper_report(tmp_path, "2026-01-23")
    eg = r.get("edge_summary") or {}
    assert eg.get("profiles_count") == 2
    assert "X" in (eg.get("strict_only_symbols") or [])


def test_json_and_markdown_render(tmp_path: Path) -> None:
    _install_config(tmp_path)
    r = build_daily_paper_report(tmp_path, "2026-02-01")
    md = render_paper_daily_markdown(r)
    assert "# Paper Trading Daily Report" in md
    assert "## 1. Executive Summary" in md
    assert "## 11. Next Actions" in md
    out = json.dumps(r, default=str)
    assert "2026-02-01" in out


def test_reports_module_has_no_broker_imports() -> None:
    import ast

    import bot.reports.paper_daily as pd
    import bot.reports.paper_weekly as pw
    import bot.reports.render_markdown as rm
    import bot.reports.report_paths as rp

    for mod in (pd, pw, rm, rp):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                m = (node.module or "").lower()
                assert "ibkr" not in m
                assert "broker" not in m
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert "ibkr" not in alias.name.lower()
                    assert alias.name.split(".")[0] != "broker"
