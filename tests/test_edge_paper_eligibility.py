"""Paper eligibility from ticker edge profiles (Prompt 13L-alt)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from bot.config import load_config
from bot.edge.eligibility import evaluate_edge_for_paper
from bot.execution.ict_paper_invariants import (
    STRUCTURE_CONTEXT_MISSING,
    WAITING_FOR_1M_TRIGGER,
)
from bot.execution.intraday_paper_execution import build_intraday_paper_intent
from bot.edge.ticker_edge import (
    REC_DISABLED,
    REC_STRICT_AND_AGGRESSIVE,
    REC_STRICT_ONLY,
    REC_WATCH_ONLY,
    TickerEdgeProfile,
)
from bot.strategies.ict_smc_intraday.model import (
    SIGNAL_DAY_TRADE_READY_AGGRESSIVE,
    SIGNAL_DAY_TRADE_READY_STRICT,
)


def _merge_intraday_paper(
    project: Path, updates: dict[str, Any]
) -> None:
    p = project / "config" / "settings.yaml"
    data: dict = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    t = data.setdefault("trading", {})
    if not isinstance(t, dict):
        t = {}
    ip = t.setdefault("intraday_paper", {})
    if not isinstance(ip, dict):
        ip = {}
    ip.update(updates)
    t["intraday_paper"] = ip
    data["trading"] = t
    p.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _seed_profile(
    project: Path,
    *,
    symbol: str,
    mode: str = REC_STRICT_AND_AGGRESSIVE,
    mrm: float = 1.0,
) -> None:
    d = project / "data" / "edge_profiles"
    d.mkdir(parents=True, exist_ok=True)
    prof = TickerEdgeProfile(
        symbol=symbol,
        strategy_id="ict_smc_intraday_v1",
        sample_start="a",
        sample_end="b",
        total_signals=30,
        filled_trades=30,
        fill_rate=0.6,
        win_rate=0.5,
        average_r=0.2,
        median_r=0.1,
        total_r=5.0,
        max_drawdown_r=-1.0,
        profit_factor=1.5,
        strict_count=10,
        strict_win_rate=0.5,
        strict_average_r=0.1,
        aggressive_count=5,
        aggressive_win_rate=0.5,
        aggressive_average_r=0.1,
        long_count=20,
        long_win_rate=0.5,
        long_average_r=0.1,
        short_count=10,
        short_win_rate=0.5,
        short_average_r=0.0,
        best_hours=[],
        weak_hours=[],
        best_direction="long",
        reliability_score=50.0,
        edge_score=50.0,
        confidence_level="strong",
        recommended_mode=mode,
        max_risk_multiplier=mrm,
        notes="t",
    )
    payload = {
        "date": "2026-01-20",
        "profiles": [prof.to_dict()],
    }
    (d / "2026-01-20-edge-profiles.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )


def test_disabled_blocks_submit(tmp_project: Path) -> None:
    (tmp_project / "data" / "edge_profiles").mkdir(parents=True, exist_ok=True)
    _merge_intraday_paper(
        tmp_project, {"edge_profile_enabled": True, "allow_strict_entries": True}
    )
    _seed_profile(tmp_project, symbol="AAPL", mode=REC_DISABLED, mrm=0.0)
    cfg = load_config(project_root=tmp_project)
    d = evaluate_edge_for_paper(
        cfg,
        "AAPL",
        SIGNAL_DAY_TRADE_READY_STRICT,
        base_risk_pct=0.1,
    )
    assert d.allow_submit is False
    assert "edge_recommended_mode_disabled" in d.skip_reasons


def test_watch_only_blocks_paper(tmp_project: Path) -> None:
    (tmp_project / "data" / "edge_profiles").mkdir(parents=True, exist_ok=True)
    _merge_intraday_paper(
        tmp_project,
        {
            "edge_profile_enabled": True,
            "allow_strict_entries": True,
            "allow_aggressive_entries": True,
        },
    )
    _seed_profile(tmp_project, symbol="MSFT", mode=REC_WATCH_ONLY, mrm=0.0)
    cfg = load_config(project_root=tmp_project)
    d = evaluate_edge_for_paper(
        cfg,
        "MSFT",
        SIGNAL_DAY_TRADE_READY_STRICT,
        base_risk_pct=0.1,
    )
    assert d.allow_submit is False
    assert "edge_recommended_mode_watch_only" in d.skip_reasons


def test_strict_only_blocks_aggressive(tmp_project: Path) -> None:
    (tmp_project / "data" / "edge_profiles").mkdir(parents=True, exist_ok=True)
    _merge_intraday_paper(
        tmp_project, {"edge_profile_enabled": True, "allow_aggressive_entries": True}
    )
    _seed_profile(
        tmp_project, symbol="NVDA", mode=REC_STRICT_ONLY, mrm=0.5
    )
    cfg = load_config(project_root=tmp_project)
    d = evaluate_edge_for_paper(
        cfg,
        "NVDA",
        SIGNAL_DAY_TRADE_READY_AGGRESSIVE,
        base_risk_pct=0.1,
    )
    assert d.allow_submit is False
    assert "edge_strict_only_blocks_aggressive" in d.skip_reasons


def test_unknown_profile_allows_small_strict(tmp_project: Path) -> None:
    (tmp_project / "data" / "edge_profiles").mkdir(parents=True, exist_ok=True)
    _merge_intraday_paper(
        tmp_project,
        {
            "edge_profile_enabled": True,
            "unknown_edge_policy": "allow_strict_small_risk",
            "unknown_edge_risk_multiplier": 0.25,
            "allow_strict_entries": True,
        },
    )
    cfg = load_config(project_root=tmp_project)
    d = evaluate_edge_for_paper(
        cfg,
        "NOCACHE",
        SIGNAL_DAY_TRADE_READY_STRICT,
        base_risk_pct=0.1,
    )
    assert d.allow_submit is True
    assert abs(d.effective_risk_pct - 0.025) < 1e-9
    assert d.edge_audit.get("max_risk_multiplier_effective") == 0.25


def test_unknown_profile_blocks_aggressive_by_default(
    tmp_project: Path,
) -> None:
    (tmp_project / "data" / "edge_profiles").mkdir(parents=True, exist_ok=True)
    _merge_intraday_paper(
        tmp_project,
        {
            "edge_profile_enabled": True,
            "unknown_edge_policy": "allow_strict_small_risk",
            "allow_aggressive_without_edge_profile": False,
        },
    )
    cfg = load_config(project_root=tmp_project)
    d = evaluate_edge_for_paper(
        cfg,
        "NOCACHE",
        SIGNAL_DAY_TRADE_READY_AGGRESSIVE,
        base_risk_pct=0.1,
    )
    assert d.allow_submit is False
    assert "edge_profile_missing" in d.skip_reasons


def test_risk_multiplier_scales_risk(
    tmp_project: Path,
) -> None:
    (tmp_project / "data" / "edge_profiles").mkdir(parents=True, exist_ok=True)
    _merge_intraday_paper(
        tmp_project, {"edge_profile_enabled": True}
    )
    ddir = tmp_project / "data" / "edge_profiles"
    p1 = TickerEdgeProfile(
        symbol="P",
        strategy_id="ict_smc_intraday_v1",
        sample_start="a",
        sample_end="b",
        total_signals=40,
        filled_trades=40,
        fill_rate=0.5,
        win_rate=0.5,
        average_r=0.2,
        median_r=0.1,
        total_r=6.0,
        max_drawdown_r=-1.0,
        profit_factor=1.6,
        strict_count=20,
        strict_win_rate=0.5,
        strict_average_r=0.1,
        aggressive_count=0,
        aggressive_win_rate=None,
        aggressive_average_r=None,
        long_count=20,
        long_win_rate=0.5,
        long_average_r=0.1,
        short_count=20,
        short_win_rate=0.5,
        short_average_r=0.0,
        best_hours=[],
        weak_hours=[],
        best_direction="both",
        reliability_score=50.0,
        edge_score=50.0,
        confidence_level="strong",
        recommended_mode=REC_STRICT_AND_AGGRESSIVE,
        max_risk_multiplier=0.5,
        notes="",
    )
    (ddir / "x-edge-profiles.json").write_text(
        json.dumps(
            {
                "date": "2026-01-20",
                "profiles": [p1.to_dict()],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    cfg = load_config(project_root=tmp_project)
    d = evaluate_edge_for_paper(
        cfg,
        "P",
        SIGNAL_DAY_TRADE_READY_STRICT,
        base_risk_pct=0.20,
    )
    assert d.allow_submit is True
    assert abs(d.effective_risk_pct - 0.1) < 1e-9
    assert d.edge_audit.get("edge_risk_multiplier_applied") is True


def test_strong_edge_allows_risk_gate_but_ict_chain_blocks_intent(
    tmp_project: Path, write_yaml: object,
) -> None:
    """13L-alt: edge may approve risk; no paper intent without 1m trigger."""
    (tmp_project / "data" / "edge_profiles").mkdir(parents=True, exist_ok=True)
    _merge_intraday_paper(tmp_project, {"edge_profile_enabled": True})
    _seed_profile(
        tmp_project,
        symbol="AAPL",
        mode=REC_STRICT_AND_AGGRESSIVE,
        mrm=1.0,
    )
    from tests.test_intraday_paper_execution import _enable_intraday_paper  # noqa: PLC0415

    _enable_intraday_paper(tmp_project, write_yaml)  # type: ignore[arg-type]
    cfg = load_config(project_root=tmp_project)
    d = evaluate_edge_for_paper(
        cfg, "AAPL", SIGNAL_DAY_TRADE_READY_STRICT, base_risk_pct=0.1
    )
    assert d.allow_submit is True
    it, err = build_intraday_paper_intent(
        {
            "symbol": "AAPL",
            "signal_category": SIGNAL_DAY_TRADE_READY_STRICT,
            "direction": "long",
            "five_min_setup_found": True,
            "one_min_trigger_found": False,
            "higher_timeframe_context_ok": True,
            "entry": 100.0,
            "stop": 99.0,
            "target": 102.0,
        },
        {"net_liquidation": 100_000.0},
        cfg,
    )
    assert it is None
    assert any(WAITING_FOR_1M_TRIGGER in e for e in err)


def test_strong_edge_cannot_bypass_missing_5m_structure(
    tmp_project: Path, write_yaml: object,
) -> None:
    """13L-alt: strong profile + READY label does not help without 5m flag."""
    (tmp_project / "data" / "edge_profiles").mkdir(parents=True, exist_ok=True)
    _merge_intraday_paper(tmp_project, {"edge_profile_enabled": True})
    _seed_profile(
        tmp_project,
        symbol="NVDA",
        mode=REC_STRICT_AND_AGGRESSIVE,
        mrm=1.0,
    )
    from tests.test_intraday_paper_execution import _enable_intraday_paper  # noqa: PLC0415

    _enable_intraday_paper(tmp_project, write_yaml)  # type: ignore[arg-type]
    cfg = load_config(project_root=tmp_project)
    assert evaluate_edge_for_paper(
        cfg, "NVDA", SIGNAL_DAY_TRADE_READY_STRICT, base_risk_pct=0.1
    ).allow_submit
    it, err = build_intraday_paper_intent(
        {
            "symbol": "NVDA",
            "signal_category": SIGNAL_DAY_TRADE_READY_STRICT,
            "direction": "long",
            "five_min_setup_found": False,
            "one_min_trigger_found": True,
            "higher_timeframe_context_ok": True,
            "entry": 100.0,
            "stop": 99.0,
            "target": 102.0,
        },
        {"net_liquidation": 100_000.0},
        cfg,
    )
    assert it is None
    assert any(STRUCTURE_CONTEXT_MISSING in e for e in err)


def test_aggressive_requires_edge_when_profile_enforced_and_no_allow_flag(
    tmp_project: Path,
) -> None:
    """ICT chain alone is insufficient for aggressive if profile missing and policy requires it."""
    (tmp_project / "data" / "edge_profiles").mkdir(parents=True, exist_ok=True)
    _merge_intraday_paper(
        tmp_project,
        {
            "edge_profile_enabled": True,
            "unknown_edge_policy": "allow_strict_small_risk",
            "allow_aggressive_without_edge_profile": False,
        },
    )
    cfg = load_config(project_root=tmp_project)
    d = evaluate_edge_for_paper(
        cfg,
        "NOSYM",
        SIGNAL_DAY_TRADE_READY_AGGRESSIVE,
        base_risk_pct=0.1,
    )
    assert d.allow_submit is False
    assert "edge_profile_missing" in d.skip_reasons
