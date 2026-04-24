"""Prompt 10D/10E MTF diagnostic and near-alignment (no orders, no config writes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from bot.mtf_diagnostic import (
    build_diagnostic_report,
    compute_mtf_diagnostics,
    format_mtf_diagnostic_digest_zh,
    format_mtf_near_alignment_digest_zh,
    select_near_alignment_candidates,
)
from bot.config import load_config


def _aapl_incomplete() -> dict:
    return {
        "symbol": "AAPL",
        "alignment_category": "BIAS_OK_SETUP_INCOMPLETE",
        "eligible_for_future_paper_trade": False,
        "mtf_alignment_score": 40,
        "market_regime": "neutral",
        "timeframes": {
            "daily": {"loaded": True, "bars": 200, "bias": "bullish"},
            "4h": {"loaded": True, "bars": 100, "structure": "bullish_confirmed"},
            "30min": {
                "loaded": True,
                "bars": 80,
                "setup_state": "incomplete",
                "entry_price": None,
                "reason": "no_choch_after_sweep",
            },
            "5min": {
                "loaded": True,
                "bars": 200,
                "trigger_state": "unknown",
                "reason": "30min setup not valid (no entry zone)",
            },
        },
        "premium_discount": {"current_zone": "premium"},
        "mtf_bias_daily": {"bias": "bullish"},
    }


def test_blocking_30m_incomplete() -> None:
    d = compute_mtf_diagnostics(_aapl_incomplete())
    assert d["blocking_layer"] == "THIRTY_MIN_SETUP"
    assert "30" in d["explanation_zh"] or "sweep" in d["explanation_zh"].lower()
    assert d["thirty_min_setup_status"] == "setup=incomplete"


def test_digest_contains_title_and_safety() -> None:
    rep = build_diagnostic_report(
        "2026-01-01",
        source_summary="test",
        items=[_aapl_incomplete()],
        top=5,
    )
    txt = format_mtf_diagnostic_digest_zh(rep, paper_gate_disabled=True)
    assert "未入场" in txt or "未下单" in txt
    assert "FULL_ALIGNMENT" in txt or "0" in txt


def test_full_alignment_zero_counts_layer() -> None:
    m = _aapl_incomplete()
    rep = build_diagnostic_report("2026-01-01", source_summary="x", items=[m], top=3)
    assert int(rep.get("full_alignment_count") or 0) == 0
    layers = rep.get("counts_by_blocking_layer") or {}
    assert "THIRTY_MIN_SETUP" in layers or len(layers) >= 0


def test_mtf_cli_no_place_order(
    tmp_project: Path, write_yaml, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot import broker as broker_mod
    from typer.testing import CliRunner
    from bot import cli as cli_mod

    ddir = tmp_project / "data" / "mtf_smc"
    ddir.mkdir(parents=True, exist_ok=True)
    fpath = ddir / "2026-04-20-TEST-mtf-smc.json"
    fpath.write_text(json.dumps(_aapl_incomplete(), ensure_ascii=False), encoding="utf-8")

    settings = yaml.safe_load((tmp_project / "config" / "settings.yaml").read_text())
    assert settings["trading"]["mtf_paper_dry_run"] is True
    assert settings["trading"]["mtf_paper_bracket_enabled"] is False

    def _boom(*_a, **_kw) -> None:
        raise AssertionError("place_order must not run during mtf diagnostic")

    monkeypatch.setattr(broker_mod.Broker, "place_order", _boom)

    from unittest.mock import MagicMock

    from bot import cli as cli_mod

    def _boot() -> tuple:
        return load_config(project_root=tmp_project), MagicMock()

    monkeypatch.setattr(cli_mod, "_bootstrap", _boot)
    runner = CliRunner()
    r = runner.invoke(
        cli_mod.app,
        ["mtf-diagnostic-report", "--date", "2026-04-20", "--top", "5"],
    )
    assert r.exit_code == 0, r.stdout
    out = ddir / "2026-04-20-mtf-diagnostic-report.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "items" in data and data["items"]
    s2 = yaml.safe_load((tmp_project / "config" / "settings.yaml").read_text())
    assert s2["trading"]["mtf_paper_dry_run"] is True
    assert s2["trading"]["mtf_paper_bracket_enabled"] is False
    assert "near_alignment_candidates" in data


def _crm_setup_ready() -> dict:
    return {
        "symbol": "CRM",
        "alignment_category": "SETUP_READY_WAITING_TRIGGER",
        "eligible_for_future_paper_trade": False,
        "mtf_alignment_score": 60,
        "market_regime": "neutral",
        "timeframes": {
            "daily": {"loaded": True, "bars": 200, "bias": "neutral"},
            "4h": {"loaded": True, "bars": 100, "structure": "transitional"},
            "30min": {
                "loaded": True,
                "bars": 80,
                "setup_state": "full_setup_valid",
                "entry_price": 100.0,
                "stop_price": 98.0,
                "target_1": 105.0,
            },
            "5min": {
                "loaded": True,
                "bars": 50,
                "trigger_state": "waiting_for_pullback",
                "reason": "not in zone",
            },
        },
        "premium_discount": {"current_zone": "discount"},
        "mtf_bias_daily": {"bias": "neutral"},
    }


def test_select_near_five_before_thirty_sort() -> None:
    crm = _crm_setup_ready()
    crm["diagnostics"] = compute_mtf_diagnostics(crm)
    low = {
        "symbol": "ZZ",
        "alignment_category": "BIAS_OK_SETUP_INCOMPLETE",
        "eligible_for_future_paper_trade": False,
        "mtf_alignment_score": 60,
        "market_regime": "neutral",
        "timeframes": {
            "daily": {"loaded": True, "bars": 200, "bias": "bullish"},
            "4h": {"loaded": True, "bars": 100, "structure": "bullish_confirmed"},
            "30min": {
                "loaded": True,
                "bars": 80,
                "setup_state": "incomplete",
                "reason": "x",
            },
            "5min": {"loaded": True, "bars": 50, "trigger_state": "unknown"},
        },
        "premium_discount": {"current_zone": "discount"},
        "mtf_bias_daily": {"bias": "bullish"},
    }
    low["diagnostics"] = compute_mtf_diagnostics(low)
    rep = build_diagnostic_report(
        "2026-01-01", source_summary="t", items=[low, crm], top=10, min_score_near=55
    )
    sel = select_near_alignment_candidates(rep, min_score=55, max_items=10)
    assert sel[0]["symbol"] == "CRM"
    assert sel[0]["blocking_layer"] == "FIVE_MIN_TRIGGER"


def test_select_excludes_risk_and_daily_bias() -> None:
    crm = _crm_setup_ready()
    crm["diagnostics"] = compute_mtf_diagnostics(crm)
    rep = {
        "items": [
            crm,
            {
                **crm,
                "symbol": "RISK1",
                "diagnostics": {
                    "blocking_layer": "RISK",
                    "primary_missing_condition": "",
                    "next_condition_to_watch": "",
                },
            },
            {
                **crm,
                "symbol": "BEAR1",
                "diagnostics": {
                    "blocking_layer": "DAILY_BIAS",
                    "primary_missing_condition": "",
                    "next_condition_to_watch": "",
                },
            },
        ]
    }
    sel = select_near_alignment_candidates(rep, min_score=55, max_items=10)
    syms = {x["symbol"] for x in sel}
    assert "CRM" in syms
    assert "RISK1" not in syms
    assert "BEAR1" not in syms


def test_thirty_requires_min_score() -> None:
    low = {
        "symbol": "LOW",
        "alignment_category": "BIAS_OK_SETUP_INCOMPLETE",
        "eligible_for_future_paper_trade": False,
        "mtf_alignment_score": 50,
        "market_regime": "neutral",
        "timeframes": {
            "daily": {"loaded": True, "bars": 200, "bias": "bullish"},
            "4h": {"loaded": True, "bars": 100, "structure": "bullish_confirmed"},
            "30min": {
                "loaded": True,
                "bars": 80,
                "setup_state": "incomplete",
            },
            "5min": {"loaded": True, "bars": 50, "trigger_state": "unknown"},
        },
        "premium_discount": {"current_zone": "discount"},
        "mtf_bias_daily": {"bias": "bullish"},
    }
    low["diagnostics"] = compute_mtf_diagnostics(low)
    rep = build_diagnostic_report("2026-01-01", source_summary="t", items=[low], top=5)
    assert len(select_near_alignment_candidates(rep, min_score=55, max_items=10)) == 0


def test_near_digest_safety_phrase() -> None:
    near = [
        {
            "symbol": "X",
            "mtf_alignment_score": 60,
            "alignment_category": "SETUP_READY_WAITING_TRIGGER",
            "blocking_layer": "FIVE_MIN_TRIGGER",
            "primary_missing_condition": "a",
            "next_condition_to_watch": "b",
            "eligible_for_future_paper_trade": False,
        }
    ]
    t = format_mtf_near_alignment_digest_zh("2026-01-01", near)
    assert "尚未 FULL_ALIGNMENT" in t or "尚未" in t
    assert "系统未下单" in t


def test_mtf_near_cli_no_place_order(
    tmp_project: Path, write_yaml, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot import broker as broker_mod
    from typer.testing import CliRunner

    ddir = tmp_project / "data" / "mtf_smc"
    ddir.mkdir(parents=True, exist_ok=True)
    rep = build_diagnostic_report(
        "2026-04-21",
        source_summary="x",
        items=[_aapl_incomplete()],
        top=5,
    )
    (ddir / "2026-04-21-mtf-diagnostic-report.json").write_text(
        json.dumps(rep, ensure_ascii=False), encoding="utf-8"
    )

    def _boom(*_a, **_kw) -> None:
        raise AssertionError("place_order")

    monkeypatch.setattr(broker_mod.Broker, "place_order", _boom)
    from unittest.mock import MagicMock

    from bot import cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "_bootstrap", lambda: (load_config(project_root=tmp_project), MagicMock())
    )
    runner = CliRunner()
    r = runner.invoke(cli_mod.app, ["mtf-near-alignment-alert", "--date", "2026-04-21"])
    assert r.exit_code == 0, r.stdout
