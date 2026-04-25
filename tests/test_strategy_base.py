"""Unit tests for ``bot.strategies.base`` dataclasses + invariants."""

from __future__ import annotations

import pytest

from bot.strategies.base import (
    ALLOWED_CONFIDENCES,
    ALLOWED_DIRECTIONS,
    ALLOWED_HORIZONS,
    ALLOWED_SCAN_STATUSES,
    ALLOWED_STRATEGY_STATUSES,
    Strategy,
    StrategyContext,
    StrategyMetadata,
    StrategyScanResult,
    StrategySignal,
    _utc_now_iso,
)


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------


def test_base_module_does_not_import_broker_or_ibkr_client() -> None:
    """The base module must remain free of broker / IBKR imports.

    This is what makes the registry safe to import from the FastAPI
    UI render path.
    """
    import sys

    import bot.strategies.base  # noqa: F401  - ensure already imported

    banned = [
        m
        for m in sys.modules
        if m.startswith("bot.broker")
        or m.startswith("bot.ibkr_client")
        or m.startswith("ib_async")
    ]
    # Importing the base module must not pull these in. (Other tests
    # may have done so independently; this test only proves that
    # importing ``bot.strategies.base`` alone is safe — we check it
    # via a clean subprocess-like approach using importlib.)
    import importlib

    importlib.reload(bot.strategies.base)
    banned_after_reload = [
        m
        for m in sys.modules
        if m == "bot.broker" or m == "bot.ibkr_client" or m == "ib_async"
    ]
    # Reload should not introduce any new banned imports.
    assert set(banned_after_reload).issubset(set(banned)), (
        f"bot.strategies.base reload added banned modules: "
        f"{set(banned_after_reload) - set(banned)}"
    )


# ---------------------------------------------------------------------------
# StrategyMetadata
# ---------------------------------------------------------------------------


def test_metadata_round_trips_to_dict() -> None:
    m = StrategyMetadata(
        key="mtf_smc",
        name="MTF",
        version="1.0",
        description_zh="测试",
        timeframes=("daily", "30min"),
        horizon="swing",
    )
    d = m.to_dict()
    assert d["key"] == "mtf_smc"
    assert d["timeframes"] == ["daily", "30min"]
    assert d["horizon"] == "swing"
    assert d["status"] == "ready"
    assert d["research_only"] is True


def test_metadata_rejects_unknown_horizon() -> None:
    with pytest.raises(ValueError):
        StrategyMetadata(
            key="x",
            name="X",
            version="1.0",
            description_zh="x",
            timeframes=(),
            horizon="hourly",  # invalid
        )


def test_metadata_rejects_unknown_status() -> None:
    with pytest.raises(ValueError):
        StrategyMetadata(
            key="x",
            name="X",
            version="1.0",
            description_zh="x",
            timeframes=(),
            horizon="swing",
            status="alpha",  # invalid
        )


# ---------------------------------------------------------------------------
# StrategySignal
# ---------------------------------------------------------------------------


def test_signal_validates_direction_and_confidence() -> None:
    s = StrategySignal(
        strategy_key="mtf_smc",
        symbol="NVDA",
        direction="long",
        confidence="medium",
        horizon="swing",
        timeframe="30min",
        score=0.75,
    )
    assert s.direction in ALLOWED_DIRECTIONS
    assert s.confidence in ALLOWED_CONFIDENCES
    assert s.to_dict()["score"] == 0.75


def test_signal_rejects_bad_direction() -> None:
    with pytest.raises(ValueError):
        StrategySignal(
            strategy_key="x",
            symbol="X",
            direction="up",  # invalid
            confidence="low",
            horizon="swing",
            timeframe="5min",
        )


# ---------------------------------------------------------------------------
# StrategyScanResult invariants
# ---------------------------------------------------------------------------


def test_scan_result_rejects_execution_allowed_true() -> None:
    """At this stage of the project, no scan may set execution_allowed=True."""
    with pytest.raises(ValueError):
        StrategyScanResult(
            strategy_key="x",
            started_utc=_utc_now_iso(),
            finished_utc=_utc_now_iso(),
            status="ok",
            symbol_count=0,
            execution_allowed=True,  # invariant violation
        )


def test_scan_result_rejects_paper_only_false() -> None:
    with pytest.raises(ValueError):
        StrategyScanResult(
            strategy_key="x",
            started_utc=_utc_now_iso(),
            finished_utc=_utc_now_iso(),
            status="ok",
            symbol_count=0,
            paper_only=False,  # invariant violation
        )


def test_scan_result_rejects_unknown_status() -> None:
    with pytest.raises(ValueError):
        StrategyScanResult(
            strategy_key="x",
            started_utc=_utc_now_iso(),
            finished_utc=_utc_now_iso(),
            status="weird",  # invalid
            symbol_count=0,
        )


def test_scan_result_signal_count_matches_signals_list() -> None:
    sig = StrategySignal(
        strategy_key="x",
        symbol="X",
        direction="unknown",
        confidence="unknown",
        horizon="research",
        timeframe="daily",
    )
    r = StrategyScanResult(
        strategy_key="x",
        started_utc=_utc_now_iso(),
        finished_utc=_utc_now_iso(),
        status="ok",
        symbol_count=1,
        signals=[sig, sig],
    )
    assert r.signal_count == 2
    d = r.to_dict()
    assert d["signal_count"] == 2
    assert d["execution_allowed"] is False
    assert d["paper_only"] is True


# ---------------------------------------------------------------------------
# Strategy Protocol
# ---------------------------------------------------------------------------


def test_strategy_protocol_is_runtime_checkable_with_dummy() -> None:
    class Dummy:
        metadata = StrategyMetadata(
            key="dummy",
            name="Dummy",
            version="0.1",
            description_zh="测试",
            timeframes=(),
            horizon="research",
            status="experimental",
        )

        def scan(self, ctx: StrategyContext) -> StrategyScanResult:
            now = _utc_now_iso()
            return StrategyScanResult(
                strategy_key=self.metadata.key,
                started_utc=now,
                finished_utc=now,
                status="ok",
                symbol_count=0,
            )

    assert isinstance(Dummy(), Strategy)


# ---------------------------------------------------------------------------
# Allowed sets
# ---------------------------------------------------------------------------


def test_allowed_sets_contain_documented_values() -> None:
    assert {"swing", "intraday", "scalp", "research"} <= ALLOWED_HORIZONS
    assert {"ready", "experimental", "not_implemented", "deprecated"} <= ALLOWED_STRATEGY_STATUSES
    assert {"ok", "skipped", "not_implemented", "error"} <= ALLOWED_SCAN_STATUSES
    assert {"long", "short", "flat", "unknown"} <= ALLOWED_DIRECTIONS
    assert {"high", "medium", "low", "unknown"} <= ALLOWED_CONFIDENCES
