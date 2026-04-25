"""Stub adapter — Chanlun Intraday V1 (not implemented yet).

Placeholder for the Chinese 缠论 (Chanlun) intraday model. Returns
``StrategyScanResult(status="not_implemented", ...)`` until real
logic lands. NEVER touches the broker.
"""

from __future__ import annotations

from ..base import StrategyContext, StrategyMetadata, StrategyScanResult, _utc_now_iso

METADATA = StrategyMetadata(
    key="chanlun_intraday_v1",
    name="Chanlun Intraday V1",
    version="0.0.0",
    description_zh="缠论日内策略 V1（占位）。基于 5m / 1m 分型 / 中枢的研究框架；当前仅注册，不下单。",
    timeframes=("5min", "1min"),
    horizon="intraday",
    research_only=True,
    requires_ibkr=True,
    enabled_by_default=False,
    status="not_implemented",
    tags=("chanlun", "intraday", "stub"),
)


class ChanlunIntradayV1Strategy:
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
                "chanlun_intraday_v1: placeholder — to be implemented in a "
                "later phase. No IBKR connection, no orders.",
            ],
        )


__all__ = ["METADATA", "ChanlunIntradayV1Strategy"]
