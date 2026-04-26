"""Batch edge profile construction (Prompt 13L-alt)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..backtests.candle_cache import load_candles
from ..backtests.intraday_engine import (
    MODE_AGGRESSIVE_ONLY,
    MODE_BOTH,
    MODE_STRICT_ONLY,
    BacktestConfig,
    backtest_intraday_smc,
)
from ..config import AppConfig
from .ibkr_fetch import fetch_1min_range_for_backtest
from .reports import save_edge_profiles_artifacts
from .ticker_edge import (
    DEFAULT_MIN_TRADES_MODERATE,
    DEFAULT_MIN_TRADES_STRONG,
    TickerEdgeProfile,
    edge_profile_insufficient,
    profile_from_backtest_run,
)


def has_1m_cache(
    project_root: Path, symbol: str, start: str, end: str
) -> bool:
    bars = load_candles(project_root, symbol, "1min", start=start, end=end)
    return len(bars) > 0


def build_edges_for_symbols(
    cfg: AppConfig,
    symbols: list[str],
    *,
    start: str,
    end: str,
    strategy_id: str,
    mode: str = "strict_and_aggressive",
    direction: str = "both",
    fetch: bool = False,
    min_trades_moderate: int = DEFAULT_MIN_TRADES_MODERATE,
    min_trades_strong: int = DEFAULT_MIN_TRADES_STRONG,
    top_n: int | None = None,
) -> tuple[list[TickerEdgeProfile], list[str], dict[str, Any]]:
    """Run per-symbol backtests and return profiles + notes.

    Connects to IBKR only when *fetch* is True.
    """
    root = Path(cfg.absolute(""))
    profiles: list[TickerEdgeProfile] = []
    notes: list[str] = []
    meta: dict[str, Any] = {"fetch_used": bool(fetch), "strategy_id": strategy_id}

    for raw in symbols:
        sym = (raw or "").strip().upper()
        if not sym:
            continue
        if not has_1m_cache(root, sym, start, end):
            if fetch:
                ok = fetch_1min_range_for_backtest(cfg, sym, start, end)
                if not ok:
                    notes.append(f"{sym}: fetch failed or returned no rows")
            if not has_1m_cache(root, sym, start, end):
                profiles.append(
                    edge_profile_insufficient(
                        sym,
                        strategy_id,
                        start,
                        end,
                        "insufficient_data: no 1m candle cache for range "
                        f"({start}..{end}); run fetch-candles or pass --fetch",
                    )
                )
                continue

        mode_norm = (mode or MODE_BOTH).strip()
        if mode_norm == "strict_and_aggressive":
            bt_mode = MODE_BOTH
        elif mode_norm == "strict_only":
            bt_mode = MODE_STRICT_ONLY
        elif mode_norm == "aggressive_only":
            bt_mode = MODE_AGGRESSIVE_ONLY
        else:
            bt_mode = MODE_BOTH

        bcfg = BacktestConfig(
            symbols=(sym,),
            start=start,
            end=end,
            mode=bt_mode,
            direction=direction,
        )
        run = backtest_intraday_smc(root, bcfg)
        p = profile_from_backtest_run(
            sym,
            run,
            strategy_id=strategy_id,
            min_trades_moderate=min_trades_moderate,
            min_trades_strong=min_trades_strong,
        )
        if run.notes:
            notes.extend(f"{sym}: {n}" for n in run.notes if sym in n)
        profiles.append(p)

    paths = save_edge_profiles_artifacts(
        root, profiles, top_n=top_n
    )
    meta["written"] = paths
    return profiles, notes, meta
