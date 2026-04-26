"""ICT chain invariants: paper orders only on full scan + Edge never replaces ICT.

Edge profiling, news, watchlist score, and volume do not open orders alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.config import load_config
from bot.execution.intraday_paper_execution import (
    READY_STRICT,
    build_intraday_paper_intent,
)
from bot.execution.ict_paper_invariants import (
    ICT_EXECUTION_FLAGS_INCOMPLETE,
    STRUCTURE_CONTEXT_MISSING,
    WAITING_FOR_1M_TRIGGER,
    validate_ict_chain_flags_for_paper,
)
from bot.strategies.ict_smc_intraday.model import (
    SIGNAL_DAY_TRADE_READY_STRICT,
    SIGNAL_WATCH_ONLY,
)


def _ready_row(**overrides: object) -> dict:
    base: dict = {
        "symbol": "AAPL",
        "signal_category": READY_STRICT,
        "direction": "long",
        "five_min_setup_found": True,
        "one_min_trigger_found": True,
        "higher_timeframe_context_ok": True,
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
    }
    base.update(overrides)
    return base


def test_flags_reject_five_without_one() -> None:
    ok, r = validate_ict_chain_flags_for_paper(
        _ready_row(five_min_setup_found=True, one_min_trigger_found=False)
    )
    assert not ok and WAITING_FOR_1M_TRIGGER in r


def test_flags_reject_one_without_five() -> None:
    ok, r = validate_ict_chain_flags_for_paper(
        _ready_row(five_min_setup_found=False, one_min_trigger_found=True)
    )
    assert not ok and STRUCTURE_CONTEXT_MISSING in r


def test_flags_reject_htf_false() -> None:
    ok, r = validate_ict_chain_flags_for_paper(
        _ready_row(higher_timeframe_context_ok=False)
    )
    assert not ok and STRUCTURE_CONTEXT_MISSING in r


def test_flags_incomplete_keys() -> None:
    row = {k: v for k, v in _ready_row().items() if k != "five_min_setup_found"}
    ok, r = validate_ict_chain_flags_for_paper(row)
    assert not ok and ICT_EXECUTION_FLAGS_INCOMPLETE in r


def test_5m_setup_ok_but_1m_trigger_missing_blocks_intent(
    tmp_project: Path, write_yaml: object,
) -> None:
    from tests.test_intraday_paper_execution import _enable_intraday_paper  # noqa: PLC0415

    _enable_intraday_paper(tmp_project, write_yaml)  # type: ignore[arg-type]
    cfg = load_config(project_root=tmp_project)
    it, err = build_intraday_paper_intent(
        _ready_row(
            five_min_setup_found=True,
            one_min_trigger_found=False,
        ),
        {"net_liquidation": 100_000.0},
        cfg,
    )
    assert it is None
    assert any(WAITING_FOR_1M_TRIGGER in x for x in err)


def test_1m_fivem_ok_htf_context_missing_blocks(
    tmp_project: Path, write_yaml: object,
) -> None:
    from tests.test_intraday_paper_execution import _enable_intraday_paper  # noqa: PLC0415

    _enable_intraday_paper(tmp_project, write_yaml)  # type: ignore[arg-type]
    cfg = load_config(project_root=tmp_project)
    it, err = build_intraday_paper_intent(
        _ready_row(
            one_min_trigger_found=True,
            five_min_setup_found=True,
            higher_timeframe_context_ok=False,
        ),
        {"net_liquidation": 100_000.0},
        cfg,
    )
    assert it is None
    assert any(STRUCTURE_CONTEXT_MISSING in x for x in err)


def test_news_volume_hot_does_not_override_missing_ict_chain(
    tmp_project: Path, write_yaml: object,
) -> None:
    from tests.test_intraday_paper_execution import _enable_intraday_paper  # noqa: PLC0415

    _enable_intraday_paper(tmp_project, write_yaml)  # type: ignore[arg-type]
    cfg = load_config(project_root=tmp_project)
    row = _ready_row(
        one_min_trigger_found=False,
        research_flags=("macro_catalyst", "rel_vol_extreme"),
    )
    it, _ = build_intraday_paper_intent(
        row, {"net_liquidation": 100_000.0}, cfg
    )
    assert it is None


def test_no_paper_intent_for_watch_only_despite_filled_flags(
    tmp_project: Path, write_yaml: object,
) -> None:
    """Simulated 'edge strong' is irrelevant: execution requires READY_* only."""
    from tests.test_intraday_paper_execution import _enable_intraday_paper  # noqa: PLC0415

    _enable_intraday_paper(tmp_project, write_yaml)  # type: ignore[arg-type]
    cfg = load_config(project_root=tmp_project)
    it, err = build_intraday_paper_intent(
        {
            "symbol": "AAPL",
            "signal_category": SIGNAL_WATCH_ONLY,
            "direction": "long",
            "five_min_setup_found": True,
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
    assert any("not paper-eligible" in x for x in err)


def test_valid_ict_row_builds_intent(
    tmp_project: Path, write_yaml: object,
) -> None:
    from tests.test_intraday_paper_execution import _enable_intraday_paper  # noqa: PLC0415

    _enable_intraday_paper(tmp_project, write_yaml)  # type: ignore[arg-type]
    cfg = load_config(project_root=tmp_project)
    it, err = build_intraday_paper_intent(
        _ready_row(),
        {"net_liquidation": 100_000.0, "mode": "paper", "block_live_trading": True},
        cfg,
    )
    assert err == [] and it is not None
    assert it.signal_category == SIGNAL_DAY_TRADE_READY_STRICT
