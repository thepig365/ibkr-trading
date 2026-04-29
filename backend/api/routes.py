"""REST API routes for connection management, status, journal, analytics."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["engine"])


@router.get("/connection-status")
async def connection_status(request: Request) -> dict[str, Any]:
    """Return the current IBKR connection status and auto-disconnect countdown."""

    return request.app.state.connection_manager.status_dict()


@router.post("/reconnect")
async def reconnect(request: Request) -> dict[str, Any]:
    """Reconnect to IBKR after AUTO_PAUSED, ERROR, or a manual dashboard action."""

    cm = request.app.state.connection_manager
    try:
        await cm.reconnect()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Reconnect endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reconnect failed: {exc}",
        ) from exc
    return cm.status_dict()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Basic process health check."""

    cm = request.app.state.connection_manager
    return {"ok": True, "connection_state": cm.state.value}


@router.get("/engine-status")
async def engine_status(request: Request) -> dict[str, Any]:
    """Aggregate engine + risk + positions status."""

    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return {"available": False}
    return {"available": True, **engine.status_dict()}


@router.get("/positions")
async def positions(request: Request) -> list[dict[str, Any]]:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return []
    return engine.trades.open_positions_dict()


@router.get("/trades")
async def trades(request: Request, limit: int = 100) -> list[dict[str, Any]]:
    db = request.app.state.database
    return await db.fetch_trades(limit=limit)


@router.get("/trade/{trade_id}")
async def trade(request: Request, trade_id: str) -> dict[str, Any]:
    db = request.app.state.database
    row = await db.fetch_trade(trade_id)
    if row is None:
        raise HTTPException(status_code=404, detail="trade not found")
    scale_ins = await db.fetch_all(
        "SELECT * FROM scale_ins WHERE trade_id = ? ORDER BY time ASC",
        (trade_id,),
    )
    return {"trade": row, "scale_ins": scale_ins}


@router.get("/trade/{trade_id}/candles")
async def trade_candles(
    request: Request,
    trade_id: str,
    minutes_before: int = Query(60, ge=0, le=240),
    minutes_after: int = Query(60, ge=0, le=240),
) -> dict[str, Any]:
    """Return ~2 hours of 1m candles bracketing a trade for chart annotation."""

    db = request.app.state.database
    row = await db.fetch_trade(trade_id)
    if row is None:
        raise HTTPException(status_code=404, detail="trade not found")

    rows = await db.fetch_all(
        """
        SELECT * FROM candle_snapshots
        WHERE symbol = ? AND timeframe = '1m'
          AND timestamp >= datetime(?, '-' || ? || ' minutes')
          AND timestamp <= datetime(?, '+' || ? || ' minutes')
        ORDER BY timestamp ASC
        """,
        (
            row["symbol"],
            row["entry_time"],
            minutes_before,
            row["exit_time"] or row["entry_time"],
            minutes_after,
        ),
    )
    scale_ins = await db.fetch_all(
        "SELECT * FROM scale_ins WHERE trade_id = ? ORDER BY time ASC",
        (trade_id,),
    )
    return {
        "trade": row,
        "candles": rows,
        "scale_ins": scale_ins,
    }


@router.get("/signals")
async def signals(request: Request, limit: int = 100) -> list[dict[str, Any]]:
    db = request.app.state.database
    return await db.fetch_signals(limit=limit)


@router.get("/daily-performance")
async def daily_performance(
    request: Request, limit: int = 60
) -> list[dict[str, Any]]:
    db = request.app.state.database
    return await db.fetch_daily_performance(limit=limit)


@router.post("/engine/pause")
async def engine_pause(request: Request) -> dict[str, Any]:
    engine = request.app.state.engine
    await engine.pause()
    return engine.status_dict()


@router.post("/engine/resume")
async def engine_resume(request: Request) -> dict[str, Any]:
    engine = request.app.state.engine
    await engine.resume()
    return engine.status_dict()


@router.post("/positions/{symbol}/close")
async def close_position(request: Request, symbol: str) -> dict[str, Any]:
    engine = request.app.state.engine
    ok = await engine.trades.manual_close(symbol.upper())
    if not ok:
        raise HTTPException(status_code=404, detail="no open position")
    return {"closed": True}


@router.get("/analytics")
async def analytics(request: Request) -> dict[str, Any]:
    """Aggregate trade analytics for the analytics page."""

    db = request.app.state.database
    trades_rows = await db.fetch_all(
        "SELECT * FROM trades WHERE status = 'closed' ORDER BY entry_time ASC"
    )
    daily = await db.fetch_all(
        "SELECT * FROM daily_performance ORDER BY date ASC"
    )
    signals_rows = await db.fetch_all(
        "SELECT score, executed FROM signals"
    )

    total = len(trades_rows)
    wins = sum(1 for t in trades_rows if (t.get("realized_pnl") or 0) > 0)
    losses = sum(1 for t in trades_rows if (t.get("realized_pnl") or 0) < 0)
    win_rate = (wins / total * 100.0) if total else 0.0
    avg_r = (
        sum(float(t.get("realized_r") or 0.0) for t in trades_rows) / total
        if total
        else 0.0
    )

    by_symbol: dict[str, dict[str, Any]] = {}
    by_strategy: dict[str, dict[str, Any]] = {}
    by_kill_zone: dict[str, dict[str, Any]] = {}
    r_buckets: dict[str, int] = {}

    for trade in trades_rows:
        symbol = trade.get("symbol") or "unknown"
        strategy = trade.get("strategy") or "unknown"
        zone = _classify_kill_zone(trade.get("entry_time"))
        r = float(trade.get("realized_r") or 0.0)
        win = (trade.get("realized_pnl") or 0) > 0

        for bucket, key in (
            (by_symbol, symbol),
            (by_strategy, strategy),
            (by_kill_zone, zone),
        ):
            entry = bucket.setdefault(key, {"wins": 0, "losses": 0, "trades": 0, "r_sum": 0.0})
            entry["trades"] += 1
            entry["r_sum"] += r
            if win:
                entry["wins"] += 1
            else:
                entry["losses"] += 1

        bucket_label = _classify_r(r)
        r_buckets[bucket_label] = r_buckets.get(bucket_label, 0) + 1

    monthly: dict[str, float] = {}
    for entry in daily:
        date = (entry.get("date") or "")[:7]
        monthly[date] = monthly.get(date, 0.0) + float(entry.get("daily_pnl") or 0.0)

    score_outcome = [
        {
            "score": float(row.get("score") or 0.0),
            "executed": bool(row.get("executed")),
        }
        for row in signals_rows
    ]

    return {
        "summary": {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "avg_r": avg_r,
        },
        "equity_curve": [
            {
                "date": row.get("date"),
                "ending_equity": row.get("ending_equity"),
                "daily_pnl": row.get("daily_pnl"),
            }
            for row in daily
        ],
        "monthly_pnl": [
            {"month": k, "pnl": monthly[k]} for k in sorted(monthly)
        ],
        "by_symbol": [
            {"symbol": k, **v, "win_rate": _safe_pct(v["wins"], v["trades"])}
            for k, v in by_symbol.items()
        ],
        "by_strategy": [
            {"strategy": k, **v, "win_rate": _safe_pct(v["wins"], v["trades"])}
            for k, v in by_strategy.items()
        ],
        "by_kill_zone": [
            {"zone": k, **v, "win_rate": _safe_pct(v["wins"], v["trades"])}
            for k, v in by_kill_zone.items()
        ],
        "r_histogram": [
            {"bucket": k, "count": v} for k, v in sorted(r_buckets.items())
        ],
        "score_vs_outcome": score_outcome,
    }


def _classify_kill_zone(entry_time: Optional[str]) -> str:
    if not entry_time:
        return "unknown"
    try:
        dt = entry_time.replace("Z", "+00:00")
        from datetime import datetime as _dt

        parsed = _dt.fromisoformat(dt)
    except Exception:  # noqa: BLE001
        return "unknown"
    hhmm = parsed.hour * 60 + parsed.minute
    if 8 * 60 + 30 <= hhmm < 10 * 60:
        return "ny_open"
    if 10 * 60 <= hhmm < 11 * 60:
        return "silver_bullet"
    if 14 * 60 <= hhmm < 15 * 60:
        return "pm_silver_bullet"
    return "other"


def _classify_r(r: float) -> str:
    if r <= -2:
        return "<= -2R"
    if r <= -1:
        return "-2R..-1R"
    if r < 0:
        return "-1R..0"
    if r < 1:
        return "0..1R"
    if r < 2:
        return "1R..2R"
    if r < 3:
        return "2R..3R"
    return ">= 3R"


def _safe_pct(num: int, denom: int) -> float:
    return (num / denom * 100.0) if denom else 0.0


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    cfg = request.app.state.config
    from backend.strategy.registry import StrategyRegistry

    return {
        "strategy": cfg.strategy,
        "symbols": cfg.symbols,
        "available_strategies": StrategyRegistry.available(),
        "risk": cfg.risk.model_dump(),
        "ict": cfg.ict.model_dump(),
        "ibkr": {"host": cfg.ibkr.host, "port": cfg.ibkr.port, "account": cfg.ibkr.account},
        "connection": cfg.connection.model_dump(),
    }


@router.post("/config")
async def update_config(request: Request) -> dict[str, Any]:
    """In-memory update of strategy / symbols / risk / ICT params.

    Persistence to ``config.yaml`` is intentionally not done here - it would
    overwrite secrets-from-env. Edit ``config.yaml`` directly to persist.
    """

    payload = await request.json()
    cfg = request.app.state.config
    if "strategy" in payload:
        cfg.strategy = str(payload["strategy"])
    if "symbols" in payload and isinstance(payload["symbols"], list):
        cfg.symbols = [str(s).upper() for s in payload["symbols"]]
    if "risk" in payload and isinstance(payload["risk"], dict):
        for key, value in payload["risk"].items():
            if hasattr(cfg.risk, key):
                setattr(cfg.risk, key, value)
    if "ict" in payload and isinstance(payload["ict"], dict):
        for key, value in payload["ict"].items():
            if hasattr(cfg.ict, key):
                setattr(cfg.ict, key, value)
    return await get_config(request)
