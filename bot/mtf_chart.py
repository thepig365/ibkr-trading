"""Multi-timeframe chart helpers (MTF) — research-only PNGs.

Each chart reuses :func:`smc_chart.render_smc_chart` for SMC marks.
Filenames: ``data/debug_charts/YYYY-MM-DD-SYMBOL-mtf-{label}.png``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .market_structure import (
    candles_from_records,
    detect_swing_highs,
    detect_swing_lows,
)
from .smc_chart import render_smc_chart
from .strategy_engine import StrategyEvaluation

logger = logging.getLogger(__name__)

_LABELS = {
    "daily": "mtf-daily",
    "4h": "mtf-4h",
    "30min": "mtf-30min",
    "5min": "mtf-5min",
}


def render_mtf_smc_charts(
    symbol: str,
    rows_by_key: Mapping[str, list[dict[str, Any]]],
    eval_by_key: Mapping[str, StrategyEvaluation | None],
    *,
    output_dir: Path,
) -> list[str]:
    """Write up to four PNGs. Skips timeframes with no rows or no evaluation."""
    out: list[str] = []
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe = symbol.upper().replace("/", "_")
    for k, lab in _LABELS.items():
        ev = eval_by_key.get(k)
        rows = list(rows_by_key.get(k) or [])
        if not ev or not rows:
            continue
        try:
            c = candles_from_records(rows)
        except Exception as exc:  # noqa: BLE001
            logger.debug("mtf chart skip %s: %s", k, exc)
            continue
        if not c:
            continue
        name = f"{day}-{safe}-{lab}.png"
        try:
            p = render_smc_chart(
                ev,
                c,
                output_dir=output_dir,
                swings_high=detect_swing_highs(c, left=2, right=2),
                swings_low=detect_swing_lows(c, left=2, right=2),
                filename=name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("mtf chart render %s: %s", k, exc)
            continue
        out.append(str(p))
    return out


__all__ = ["render_mtf_smc_charts"]
