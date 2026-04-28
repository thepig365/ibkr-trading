"""Optional IBKR read-only fetch for edge profile builds (Prompt 13L-alt).

Call **only** when the CLI passes ``--fetch``; never on UI render paths.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import AppConfig

LOG = logging.getLogger(__name__)


def fetch_1min_range_for_backtest(
    cfg: AppConfig,
    symbol: str,
    start: str,
    end: str,
    *,
    use_rth: bool = True,
) -> bool:
    """Connect to IBKR, pull 1m history, write ``data/candles/.../1min``.

    Returns True if at least one row was written or merge succeeded; False
    on hard failure. Never places orders.
    """
    from ..backtests.candle_cache import (
        CandleCacheError,
        save_candles_csv,
    )
    from ..ibkr_client import IBKRClientError
    from ..ibkr_connection import connect_readonly_roster_retry
    from ..smc_timeframes import resolve_timeframe_spec

    sym = (symbol or "").strip().upper()
    timeframe = "1min"
    spec = resolve_timeframe_spec(timeframe, cfg)
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    days = max((d1 - d0).days + 2, 2)
    duration = f"{days} D"
    bar_size = str(spec.bar_size)
    use_rth_flag = bool(use_rth)

    client: Any = None
    try:
        oc = connect_readonly_roster_retry(cfg, "edge")
        if oc.live_blocked:
            LOG.warning("edge fetch: blocked: %s", oc.live_blocked)
            return False
        if oc.client is None:
            LOG.warning("edge fetch: IBKR connect failed: %s", oc.fatal_message)
            return False
        client = oc.client
        bars: list[dict] = []
        try:
            bars = client.get_intraday_bars(
                sym,
                duration=duration,
                bar_size=bar_size,
                what_to_show=str(spec.what_to_show or "TRADES"),
                use_rth=use_rth_flag,
            ) or []
        except Exception as exc:  # noqa: BLE001
            LOG.warning("edge fetch: IBKR get_intraday_bars failed: %s", exc)
            bars = []
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    if not bars:
        return False
    try:
        stats = save_candles_csv(
            project_root=Path(cfg.absolute("")),
            symbol=sym,
            timeframe=timeframe,
            bars=bars,
            start=start,
            end=end,
            force=False,
        )
    except CandleCacheError as exc:
        LOG.warning("edge fetch: save_candles_csv: %s", exc)
        return False
    return bool(stats.rows_written or stats.days_written or stats.files)
