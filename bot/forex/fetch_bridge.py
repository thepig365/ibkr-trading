"""IBKR read-only pull for Forex 1m — CASH/IDEALPRO via :class:`IBKRClient`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bot.config import AppConfig, load_config
from bot.ibkr_connection import connect_readonly_roster_retry
from bot.journal import Journal

from .candle_store import save_forex_candles_csv
from .pairs import parse_pair


def fetch_forex_1m_duration(
    *,
    project_root: Path,
    pair_display: str,
    duration: str = "1 D",
    bar_size: str = "1 min",
    what_to_show: str = "MIDPOINT",
    start: str | None = None,
    end: str | None = None,
    force: bool = False,
    cfg: AppConfig | None = None,
) -> dict[str, Any]:
    spec = parse_pair(pair_display)
    cfg = cfg or load_config(project_root=Path(project_root).resolve())

    outcome = connect_readonly_roster_retry(cfg, "forex_fetch")
    if outcome.client is None:
        return {
            "ok": False,
            "pair": pair_display,
            "error": outcome.fatal_message or "no_client",
            "bars": 0,
        }

    cli = outcome.client
    bars: list[dict[str, Any]] = []
    try:
        bars = cli.get_intraday_bars(
            spec.base,
            duration=duration,
            bar_size=bar_size,
            what_to_show=what_to_show,
            use_rth=False,
            sec_type="CASH",
            exchange="IDEALPRO",
            currency=spec.quote,
        ) or []
    finally:
        try:
            cli.disconnect()
        except Exception:
            pass

    stats = save_forex_candles_csv(
        Path(project_root),
        spec.slug,
        "1min",
        bars,
        start=start,
        end=end,
        force=force,
    )
    jr = Journal(cfg)
    jr.record_event(
        "research",
        "fetch-forex-candles",
        level="INFO",
        payload={
            "pair": pair_display,
            "rows": stats.get("rows_written"),
            "slug": spec.slug,
            "paper_only": True,
        },
    )
    return {
        "ok": True,
        "pair": pair_display,
        "slug": spec.slug,
        "bars": len(bars),
        **stats,
    }


__all__ = ["fetch_forex_1m_duration"]
