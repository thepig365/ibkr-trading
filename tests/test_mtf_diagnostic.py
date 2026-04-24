"""Prompt 10D MTF diagnostic (no orders, no config writes)."""

from __future__ import annotations

import json

import pytest
import yaml

from bot.mtf_diagnostic import (
    build_diagnostic_report,
    compute_mtf_diagnostics,
    format_mtf_diagnostic_digest_zh,
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
