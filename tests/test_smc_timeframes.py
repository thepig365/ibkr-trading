"""Tests for :mod:`bot.smc_timeframes` (Prompt 10A).

Covers:
    * timeframe registry (daily, 4h, 30min, 5min),
    * IBKR request preset for 30min (``20 D`` / ``30 mins`` / TRADES / RTH),
    * strategy threshold resolution + apply-to-block,
    * session guard: first/last 15 min of US RTH for 30min,
    * ``strategy_engine`` honours per-timeframe ``max_allowed_stop_pct``
      / ``min_risk_reward`` / ``max_extension_pct`` / risk per trade,
    * no module imports ``bot.broker``.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from bot.smc_timeframes import (
    DEFAULT_TIMEFRAME_SPECS,
    DEFAULT_TIMEFRAME_STRATEGY,
    SUPPORTED_TIMEFRAMES,
    apply_thresholds_to_block,
    evaluate_session_guard,
    normalise_timeframe,
    resolve_strategy_thresholds,
    resolve_timeframe_spec,
)
from bot.config import load_config


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_supported_timeframes_includes_mtf() -> None:
    # Prompt 13D added "1min" for the ICT/SMC Intraday strategy.
    assert SUPPORTED_TIMEFRAMES == ("daily", "4h", "30min", "5min", "1min")


def test_normalise_timeframe_aliases() -> None:
    assert normalise_timeframe(None) == "daily"
    assert normalise_timeframe("") == "daily"
    assert normalise_timeframe("1d") == "daily"
    assert normalise_timeframe("DAILY") == "daily"
    assert normalise_timeframe("30min") == "30min"
    assert normalise_timeframe("30 mins") == "30min"
    assert normalise_timeframe("30 Min") == "30min"
    assert normalise_timeframe("4h") == "4h"
    assert normalise_timeframe("5m") == "5min"
    assert normalise_timeframe("5min") == "5min"
    assert normalise_timeframe("weekly") == "daily"  # unknown → safe default


def test_default_30min_preset_matches_ibkr_request_spec() -> None:
    preset = DEFAULT_TIMEFRAME_SPECS["30min"]
    assert preset["duration"] == "20 D"
    assert preset["bar_size"] == "30 mins"
    assert preset["what_to_show"] == "TRADES"
    assert preset["use_rth"] is True
    assert preset["min_bars"] == 100
    assert preset["max_bars"] == 300


def test_resolve_timeframe_spec_uses_repo_yaml(tmp_project) -> None:
    cfg = load_config(project_root=tmp_project)
    spec_d = resolve_timeframe_spec("daily", cfg)
    spec_30 = resolve_timeframe_spec("30min", cfg)
    assert spec_d.name == "daily"
    assert spec_d.bar_size == "1 day"
    assert spec_d.is_intraday is False
    assert spec_30.name == "30min"
    assert spec_30.duration == "20 D"
    assert spec_30.bar_size == "30 mins"
    assert spec_30.use_rth is True
    assert spec_30.what_to_show == "TRADES"
    assert spec_30.min_bars == 100
    assert spec_30.max_bars == 300
    assert spec_30.is_intraday is True


def test_resolve_timeframe_spec_falls_back_when_cfg_missing() -> None:
    spec = resolve_timeframe_spec("30min", cfg=None)
    assert spec.duration == "20 D"
    assert spec.bar_size == "30 mins"


# ---------------------------------------------------------------------------
# Strategy thresholds per timeframe
# ---------------------------------------------------------------------------
def test_daily_thresholds_are_empty_without_timeframes_block() -> None:
    """Legacy strategy blocks (no ``timeframes:``) stay exactly as-is for daily."""
    block = {"stop": {"max_allowed_stop_pct": 99.0}}
    thr = resolve_strategy_thresholds("daily", block)
    assert thr == {}


def test_daily_thresholds_use_yaml_when_configured(tmp_project) -> None:
    cfg = load_config(project_root=tmp_project)
    block = cfg.strategies.get("SMC_LIQUIDITY_REVERSAL_RESEARCH") or {}
    thr = resolve_strategy_thresholds("daily", block)
    # Our repo YAML defines daily overrides that match defaults.
    assert thr["max_allowed_stop_pct"] == 5.0
    assert thr["max_extension_pct"] == 3.0
    assert thr["min_risk_reward"] == 2.0
    assert thr["risk_per_trade_pct"] == 1.0


def test_30min_thresholds_are_stricter_than_daily(tmp_project) -> None:
    cfg = load_config(project_root=tmp_project)
    block = cfg.strategies.get("SMC_LIQUIDITY_REVERSAL_RESEARCH") or {}
    daily = resolve_strategy_thresholds("daily", block)
    thirty = resolve_strategy_thresholds("30min", block)
    assert thirty["max_allowed_stop_pct"] == 2.0
    assert thirty["max_extension_pct"] == 1.0
    assert thirty["min_risk_reward"] == 1.8
    assert thirty["risk_per_trade_pct"] == 0.25
    assert thirty["avoid_first_minutes_after_open"] == 15
    assert thirty["avoid_last_minutes_before_close"] == 15
    assert thirty["max_hold_bars"] == 13
    # And stricter than daily.
    assert thirty["max_allowed_stop_pct"] < daily["max_allowed_stop_pct"]
    assert thirty["max_extension_pct"] < daily["max_extension_pct"]
    assert thirty["min_risk_reward"] < daily["min_risk_reward"]
    assert thirty["risk_per_trade_pct"] < daily["risk_per_trade_pct"]


def test_30min_defaults_apply_when_no_user_overrides() -> None:
    """A strategy block without a ``timeframes:`` entry for 30min still
    gets the stricter built-in defaults — 30min is a new research mode
    that must never fall back to the looser daily numbers."""
    thr = resolve_strategy_thresholds("30min", {})
    assert thr == DEFAULT_TIMEFRAME_STRATEGY["30min"]


def test_apply_thresholds_writes_to_nested_sections() -> None:
    block = {
        "stop": {"max_allowed_stop_pct": 99.0, "buffer_cents": 0.05},
        "entry": {"reject_if_price_extended_from_entry_pct": 99.0},
        "risk": {"min_reward_to_risk": 99.0,
                 "max_account_risk_per_trade_pct": 99.0},
        "target": {"min_risk_reward": 99.0},
        "sweep": {"lookback_period": 99},
    }
    merged = apply_thresholds_to_block(
        block, resolve_strategy_thresholds("30min", {})
    )
    assert merged["stop"]["max_allowed_stop_pct"] == 2.0
    assert merged["stop"]["buffer_cents"] == 0.05  # untouched
    assert merged["entry"]["reject_if_price_extended_from_entry_pct"] == 1.0
    assert merged["risk"]["min_reward_to_risk"] == 1.8
    assert merged["risk"]["max_account_risk_per_trade_pct"] == 0.25
    assert merged["target"]["min_risk_reward"] == 1.8
    assert merged["sweep"]["lookback_period"] == 20
    # Per-timeframe thresholds are stashed for downstream scorers.
    assert merged["_timeframe_thresholds"]["max_allowed_stop_pct"] == 2.0


def test_apply_thresholds_is_noop_when_empty() -> None:
    block = {"stop": {"max_allowed_stop_pct": 99.0}}
    merged = apply_thresholds_to_block(block, {})
    assert merged["stop"]["max_allowed_stop_pct"] == 99.0
    assert merged["_timeframe_thresholds"] == {}


# ---------------------------------------------------------------------------
# Session guard
# ---------------------------------------------------------------------------
def test_daily_session_guard_is_always_allowed() -> None:
    guard = evaluate_session_guard("daily", now_et_hhmm="09:30")
    assert guard.allowed is True
    assert guard.reason == ""


def test_30min_session_guard_allows_middle_of_session() -> None:
    guard = evaluate_session_guard("30min", now_et_hhmm="12:00")
    assert guard.allowed is True


def test_30min_session_guard_blocks_first_15_after_open() -> None:
    for ts in ("09:30", "09:44"):
        guard = evaluate_session_guard("30min", now_et_hhmm=ts)
        assert guard.allowed is False, ts
        assert "first" in guard.reason


def test_30min_session_guard_allows_edge_of_avoid_window() -> None:
    # 09:45 is the first minute outside the first-15m block.
    guard = evaluate_session_guard("30min", now_et_hhmm="09:45")
    assert guard.allowed is True


def test_30min_session_guard_blocks_last_15_before_close() -> None:
    for ts in ("15:45", "15:59"):
        guard = evaluate_session_guard("30min", now_et_hhmm=ts)
        assert guard.allowed is False, ts
        assert "last" in guard.reason


def test_30min_session_guard_blocks_outside_rth() -> None:
    for ts in ("04:30", "09:29", "16:00", "18:00"):
        guard = evaluate_session_guard("30min", now_et_hhmm=ts)
        assert guard.allowed is False, ts


def test_30min_session_guard_allowed_when_clock_missing() -> None:
    """No real clock → guard defaults to *allowed* so the pure review-
    queue builder stays easy to test and reason about."""
    guard = evaluate_session_guard("30min", now_et_hhmm=None)
    assert guard.allowed is True


# ---------------------------------------------------------------------------
# Safety invariant: module must not import broker
# ---------------------------------------------------------------------------
def test_smc_timeframes_never_imports_broker() -> None:
    src = pathlib.Path(__file__).resolve().parent.parent / "bot" / "smc_timeframes.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [n.name for n in node.names]
            module = getattr(node, "module", "") or ""
            assert "bot.broker" not in module, module
            assert "broker" not in names, names
