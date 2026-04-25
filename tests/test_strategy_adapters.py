"""Tests for the four built-in strategy adapters.

Covers:
* mtf_smc adapter wraps the existing batch scanner without orders.
* Stub adapters return ``status="not_implemented"`` and never touch IBKR.
* No adapter places orders or imports the broker at module load.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from bot.strategies.adapters.chanlun_intraday_v1 import ChanlunIntradayV1Strategy
from bot.strategies.adapters.ict_smc_intraday_v1 import IctSmcIntradayV1Strategy
from bot.strategies.adapters.mtf_smc_adapter import (
    METADATA as MTF_SMC_METADATA,
    MtfSmcStrategy,
    _confidence_from_category,
)
from bot.strategies.adapters.orb_baseline import OrbBaselineStrategy
from bot.strategies.base import (
    Strategy,
    StrategyContext,
    StrategyMetadata,
    StrategyScanResult,
)


# ---------------------------------------------------------------------------
# All adapters satisfy the Strategy Protocol
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls",
    [MtfSmcStrategy, IctSmcIntradayV1Strategy, ChanlunIntradayV1Strategy, OrbBaselineStrategy],
)
def test_adapter_satisfies_strategy_protocol(cls: type) -> None:
    inst = cls()
    assert isinstance(inst, Strategy)
    assert isinstance(inst.metadata, StrategyMetadata)
    assert inst.metadata.research_only is True


# ---------------------------------------------------------------------------
# Stub adapters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls,key",
    [
        (IctSmcIntradayV1Strategy, "ict_smc_intraday_v1"),
        (ChanlunIntradayV1Strategy, "chanlun_intraday_v1"),
        (OrbBaselineStrategy, "orb_baseline"),
    ],
)
def test_stub_adapter_returns_not_implemented(cls: type, key: str) -> None:
    s = cls()
    assert s.metadata.status == "not_implemented"
    assert s.metadata.enabled_by_default is False
    ctx = StrategyContext(symbols=("AAPL", "TSLA"))
    r = s.scan(ctx)
    assert isinstance(r, StrategyScanResult)
    assert r.strategy_key == key
    assert r.status == "not_implemented"
    assert r.symbol_count == 2
    assert r.signal_count == 0
    assert r.execution_allowed is False
    assert r.paper_only is True
    assert r.notes  # non-empty explanation


# ---------------------------------------------------------------------------
# mtf_smc adapter — no broker import at module load + no orders on scan
# ---------------------------------------------------------------------------


def test_mtf_smc_adapter_module_does_not_import_broker_at_top_level() -> None:
    """Static AST scan: top-level imports must not include broker / IBKR."""
    p = Path("bot/strategies/adapters/mtf_smc_adapter.py")
    tree = ast.parse(p.read_text(encoding="utf-8"))
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "broker" not in alias.name
                assert "ibkr_client" not in alias.name
                assert alias.name != "ib_async"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert "broker" not in mod
            assert "ibkr_client" not in mod
            assert mod != "ib_async"


def test_mtf_smc_adapter_skips_when_cfg_missing() -> None:
    """Missing cfg / journal -> graceful 'skipped', no exception."""
    s = MtfSmcStrategy()
    r = s.scan(StrategyContext(symbols=("X",), cfg=None, journal=None))
    assert r.status == "skipped"
    assert r.signal_count == 0


def test_mtf_smc_adapter_translates_summary_into_signals_via_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch the underlying batch scanner; assert the adapter never asks
    for paper_bracket and translates ``items`` into ``StrategySignal``."""
    captured: dict[str, Any] = {}

    def fake_scan(cfg: Any, journal: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "date": "2026-04-25",
            "source": "dynamic",
            "symbols_scanned": 3,
            "counts": {"FULL_ALIGNMENT": 1, "BLOCKED": 1, "CONFLICTED": 1},
            "top_by_alignment_score": [
                {"symbol": "NVDA", "mtf_alignment_score": 90,
                 "alignment_category": "FULL_ALIGNMENT",
                 "eligible_for_future_paper_trade": True},
            ],
            "eligible_for_future_paper_trade": ["NVDA"],
            "items": [
                {"symbol": "NVDA", "mtf_alignment_score": 90,
                 "alignment_category": "FULL_ALIGNMENT",
                 "eligible_for_future_paper_trade": True},
                {"symbol": "AAPL", "mtf_alignment_score": 30,
                 "alignment_category": "BIAS_OK_SETUP_INCOMPLETE",
                 "eligible_for_future_paper_trade": False},
                {"symbol": "TSLA", "mtf_alignment_score": 0,
                 "alignment_category": "BLOCKED",
                 "eligible_for_future_paper_trade": False},
            ],
            "_saved_summary_path": "/tmp/x.json",
        }

    # The adapter does a relative ``from ...mtf_smc_batch import`` inside
    # scan(), so we patch the symbol on the resolved batch module.
    import bot.mtf_smc_batch as batch_mod

    monkeypatch.setattr(batch_mod, "run_mtf_smc_watchlist_scan", fake_scan)

    ctx = StrategyContext(
        symbols=("NVDA", "AAPL", "TSLA"),
        cfg=object(),
        journal=object(),
        extras={"max_symbols": 3, "source": "dynamic"},
    )
    s = MtfSmcStrategy()
    r = s.scan(ctx)

    # paper_bracket and max_paper_trades MUST be forced off.
    assert captured["paper_bracket"] is False
    assert captured["max_paper_trades"] == 0
    assert captured["use_ibkr"] is True
    assert captured["save_json"] is True
    assert captured["limit"] == 3
    assert captured["source"] == "dynamic"

    assert r.status == "ok"
    assert r.symbol_count == 3
    assert r.signal_count == 3
    assert r.execution_allowed is False
    assert r.paper_only is True
    by_sym = {sig.symbol: sig for sig in r.signals}
    assert by_sym["NVDA"].confidence == "high"
    assert by_sym["AAPL"].confidence == "low"
    assert by_sym["TSLA"].confidence == "unknown"
    assert by_sym["NVDA"].payload["eligible_for_future_paper_trade"] is True
    assert r.summary["execution_allowed"] is False
    assert r.summary["counts"]["FULL_ALIGNMENT"] == 1


def test_mtf_smc_adapter_handles_filenotfounderror_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise FileNotFoundError("dynamic watchlist not built")

    import bot.mtf_smc_batch as batch_mod

    monkeypatch.setattr(batch_mod, "run_mtf_smc_watchlist_scan", boom)
    ctx = StrategyContext(symbols=("X",), cfg=object(), journal=object())
    r = MtfSmcStrategy().scan(ctx)
    assert r.status == "error"
    assert "build-watchlist" in (r.notes[0] if r.notes else "")
    assert r.error and "watchlist" in r.error


def test_mtf_smc_adapter_handles_generic_exception_without_propagating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("simulated TWS down")

    import bot.mtf_smc_batch as batch_mod

    monkeypatch.setattr(batch_mod, "run_mtf_smc_watchlist_scan", boom)
    ctx = StrategyContext(symbols=("X",), cfg=object(), journal=object())
    r = MtfSmcStrategy().scan(ctx)
    assert r.status == "error"
    assert r.error and "simulated TWS down" in r.error


def test_mtf_smc_metadata_matches_expected_invariants() -> None:
    assert MTF_SMC_METADATA.key == "mtf_smc"
    assert MTF_SMC_METADATA.status == "ready"
    assert MTF_SMC_METADATA.research_only is True
    assert "5min" in MTF_SMC_METADATA.timeframes
    assert MTF_SMC_METADATA.horizon == "swing"


def test_confidence_from_category_mapping() -> None:
    assert _confidence_from_category("FULL_ALIGNMENT") == "high"
    assert _confidence_from_category("SETUP_READY_WAITING_TRIGGER") == "medium"
    assert _confidence_from_category("CONFLICTED") == "low"
    assert _confidence_from_category("BLOCKED") == "unknown"
    assert _confidence_from_category("???") == "unknown"
