"""Adapter — ICT/SMC Intraday Liquidity Reversal V1 (Prompt 13D).

Wraps :mod:`bot.strategies.ict_smc_intraday` so the multi-strategy
engine and CLI can drive it through the same Protocol used by the MTF
SMC adapter.

Hard rules (also enforced by tests):

* No top-level imports of :mod:`bot.broker`, :mod:`bot.ibkr_client`, or
  any TWS-touching code. The IBKR client is imported lazily *inside*
  :py:meth:`scan` (transitively via ``scan_watchlist_with_ibkr``).
* ``StrategyScanResult.execution_allowed`` is hard-coded to ``False``.
* When ``ctx.symbols`` is empty (e.g. UI render path), ``scan`` returns
  ``status="skipped"`` immediately without touching the broker.
* Any unexpected error is captured into ``status="error"`` so a single
  bad symbol does not poison the multi-strategy run.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..base import (
    StrategyContext,
    StrategyMetadata,
    StrategyScanResult,
    StrategySignal,
    _utc_now_iso,
)

METADATA = StrategyMetadata(
    key="ict_smc_intraday_v1",
    name="ICT/SMC Intraday Liquidity Reversal V1",
    version="0.1.0",
    description_zh=(
        "ICT/SMC 日内策略 V1。4H/30m 提供软方向, 5m sweep+reclaim 形成 setup, "
        "1m sweep+MSS+FVG/OB 触发, 输出 STRICT/AGGRESSIVE/WATCH 候选; 仅研究, 不下单。"
    ),
    timeframes=("4h", "30min", "5min", "1min"),
    horizon="intraday",
    research_only=True,
    requires_ibkr=True,
    enabled_by_default=True,
    status="experimental",
    tags=("smc", "ict", "intraday", "liquidity_reversal", "v1"),
)


def _to_signal(item: Mapping[str, Any]) -> StrategySignal | None:
    """Convert a watchlist-summary item dict into a :class:`StrategySignal`."""
    cat = str(item.get("signal_category") or "")
    if cat not in {
        "DAY_TRADE_READY_STRICT",
        "DAY_TRADE_READY_AGGRESSIVE",
        "WATCH_ONLY",
    }:
        return None
    direction = str(item.get("direction") or "flat")
    if direction not in {"long", "short", "flat", "unknown"}:
        direction = "flat"
    confidence_map = {
        "DAY_TRADE_READY_STRICT": "high",
        "DAY_TRADE_READY_AGGRESSIVE": "medium",
        "WATCH_ONLY": "low",
    }
    return StrategySignal(
        strategy_key=METADATA.key,
        symbol=str(item.get("symbol") or ""),
        direction=direction,
        confidence=confidence_map.get(cat, "low"),
        horizon="intraday",
        timeframe="1min",
        score=float(item.get("score") or 0.0),
        reason=cat,
        payload={
            "signal_category": cat,
            "entry": item.get("entry"),
            "stop": item.get("stop"),
            "target": item.get("target"),
            "risk_reward": item.get("risk_reward"),
            "stop_distance_pct": item.get("stop_distance_pct"),
            "next_condition_to_watch": item.get("next_condition_to_watch"),
            "explanation_zh": item.get("explanation_zh"),
            "chart_paths": list(item.get("chart_paths") or []),
            "data_source": item.get("data_source"),
            "data_quality": item.get("data_quality") or {},
        },
    )


class IctSmcIntradayV1Strategy:
    """Adapter that maps the engine Protocol onto the intraday scanner."""

    metadata: StrategyMetadata = METADATA

    def scan(self, ctx: StrategyContext) -> StrategyScanResult:
        started = _utc_now_iso()
        notes: list[str] = []

        # Hard invariants: even if a future caller flips them, we refuse.
        if not ctx.paper_only:
            return StrategyScanResult(
                strategy_key=self.metadata.key,
                started_utc=started,
                finished_utc=_utc_now_iso(),
                status="error",
                symbol_count=len(ctx.symbols or ()),
                notes=["paper_only must be True; refusing to run."],
                error="paper_only_violation",
            )
        if ctx.paper_execution_allowed:
            return StrategyScanResult(
                strategy_key=self.metadata.key,
                started_utc=started,
                finished_utc=_utc_now_iso(),
                status="error",
                symbol_count=len(ctx.symbols or ()),
                notes=["paper_execution_allowed must be False at this stage."],
                error="paper_execution_violation",
            )

        symbols = list(ctx.symbols or ())
        if not symbols:
            return StrategyScanResult(
                strategy_key=self.metadata.key,
                started_utc=started,
                finished_utc=_utc_now_iso(),
                status="skipped",
                symbol_count=0,
                notes=[
                    "ict_smc_intraday_v1: no symbols provided; nothing to scan.",
                ],
            )

        # Lazy imports — keep module load free of IBKR/broker imports.
        try:
            from ..ict_smc_intraday import (  # noqa: PLC0415
                IntradayRiskConfig,
                scan_symbol_with_ibkr,
            )
        except Exception as exc:  # noqa: BLE001
            return StrategyScanResult(
                strategy_key=self.metadata.key,
                started_utc=started,
                finished_utc=_utc_now_iso(),
                status="error",
                symbol_count=len(symbols),
                notes=[f"failed to import intraday scanner: {exc!r}"],
                error=str(exc),
            )

        risk_cfg = IntradayRiskConfig.from_extras(ctx.extras or {})
        signals: list[StrategySignal] = []
        per_symbol_summary: list[dict[str, Any]] = []
        cfg = ctx.cfg
        journal = ctx.journal
        last_error: str | None = None

        # Multi-strategy engine path: scan each symbol via the IBKR
        # backed pipeline. Per-symbol JSON is NOT written here (the
        # multi-strategy engine writes one summary per run); the
        # dedicated CLI commands are the canonical writers.
        for sym in symbols:
            try:
                eval_obj = scan_symbol_with_ibkr(
                    sym, cfg, journal, risk_cfg=risk_cfg, chart=False
                )
                row = {
                    "symbol": eval_obj.symbol,
                    "signal_category": eval_obj.signal_category,
                    "direction": eval_obj.direction,
                    "score": eval_obj.score,
                    "entry": eval_obj.trade_plan.entry if eval_obj.trade_plan else None,
                    "stop": eval_obj.trade_plan.stop if eval_obj.trade_plan else None,
                    "target": eval_obj.trade_plan.target if eval_obj.trade_plan else None,
                    "risk_reward": (
                        eval_obj.trade_plan.risk_reward if eval_obj.trade_plan else None
                    ),
                    "stop_distance_pct": (
                        eval_obj.trade_plan.stop_distance_pct
                        if eval_obj.trade_plan else None
                    ),
                    "next_condition_to_watch": eval_obj.next_condition_to_watch,
                    "explanation_zh": eval_obj.explanation_zh,
                    "chart_paths": list(eval_obj.chart_paths or []),
                    "data_source": eval_obj.data_source,
                    "data_quality": dict(eval_obj.data_quality or {}),
                }
                per_symbol_summary.append(row)
                sig = _to_signal(row)
                if sig is not None:
                    signals.append(sig)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                notes.append(f"{sym}: scan raised ({exc!r})")
                per_symbol_summary.append(
                    {
                        "symbol": sym,
                        "signal_category": "ERROR",
                        "direction": "flat",
                        "error": str(exc),
                    }
                )

        status = "ok" if not (last_error and not signals) else (
            "ok" if signals else "error"
        )
        return StrategyScanResult(
            strategy_key=self.metadata.key,
            started_utc=started,
            finished_utc=_utc_now_iso(),
            status=status if status in {"ok", "error", "skipped"} else "ok",
            symbol_count=len(symbols),
            signals=signals,
            summary={
                "items": per_symbol_summary,
                "ready_strict_symbols": [
                    r["symbol"] for r in per_symbol_summary
                    if r.get("signal_category") == "DAY_TRADE_READY_STRICT"
                ],
                "ready_aggressive_symbols": [
                    r["symbol"] for r in per_symbol_summary
                    if r.get("signal_category") == "DAY_TRADE_READY_AGGRESSIVE"
                ],
                "watch_symbols": [
                    r["symbol"] for r in per_symbol_summary
                    if r.get("signal_category") == "WATCH_ONLY"
                ],
                "execution_allowed": False,
                "paper_only": True,
            },
            notes=notes,
            error=last_error if status == "error" else None,
        )


__all__ = ["METADATA", "IctSmcIntradayV1Strategy"]
