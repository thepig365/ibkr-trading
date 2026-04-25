"""Stub adapter — ICT/SMC Intraday V1 (not implemented yet).

Registered for two reasons:

1. Discoverability: the UI / CLI list every planned strategy so the
   roadmap is visible without grepping comments.
2. Architectural sanity: the registry / engine code paths must work
   with multiple strategies from day one.

Until the real implementation lands, ``scan`` immediately returns
``StrategyScanResult(status="not_implemented", ...)``. It NEVER
connects to IBKR, NEVER places orders, NEVER imports the broker.
"""

from __future__ import annotations

from ..base import StrategyContext, StrategyMetadata, StrategyScanResult, _utc_now_iso

METADATA = StrategyMetadata(
    key="ict_smc_intraday_v1",
    name="ICT / SMC Intraday V1",
    version="0.0.0",
    description_zh="ICT/SMC 日内策略 V1（占位）。1m/5m 触发，等待后续阶段实现；当前仅注册，不下单。",
    timeframes=("5min", "1min"),
    horizon="intraday",
    research_only=True,
    requires_ibkr=True,
    enabled_by_default=False,
    status="not_implemented",
    tags=("smc", "ict", "intraday", "stub"),
)


class IctSmcIntradayV1Strategy:
    metadata: StrategyMetadata = METADATA

    def scan(self, ctx: StrategyContext) -> StrategyScanResult:
        now = _utc_now_iso()
        return StrategyScanResult(
            strategy_key=self.metadata.key,
            started_utc=now,
            finished_utc=now,
            status="not_implemented",
            symbol_count=len(ctx.symbols),
            notes=[
                "ict_smc_intraday_v1: placeholder — to be implemented in a "
                "later phase. No IBKR connection, no orders.",
            ],
        )


__all__ = ["METADATA", "IctSmcIntradayV1Strategy"]
