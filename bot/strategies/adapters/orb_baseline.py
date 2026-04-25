"""Stub adapter — Opening Range Breakout (ORB) baseline (not implemented yet).

Placeholder for a deterministic ORB baseline used to A/B-test other
intraday strategies. Returns ``StrategyScanResult(status="not_implemented", ...)``
until the real implementation lands. NEVER touches the broker.
"""

from __future__ import annotations

from ..base import StrategyContext, StrategyMetadata, StrategyScanResult, _utc_now_iso

METADATA = StrategyMetadata(
    key="orb_baseline",
    name="Opening Range Breakout (Baseline)",
    version="0.0.0",
    description_zh="开盘区间突破基线策略（占位）。用作日内策略 A/B 对照；当前仅注册，不下单。",
    timeframes=("5min", "1min"),
    horizon="intraday",
    research_only=True,
    requires_ibkr=True,
    enabled_by_default=False,
    status="not_implemented",
    tags=("orb", "intraday", "baseline", "stub"),
)


class OrbBaselineStrategy:
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
                "orb_baseline: placeholder — to be implemented in a later "
                "phase. No IBKR connection, no orders.",
            ],
        )


__all__ = ["METADATA", "OrbBaselineStrategy"]
