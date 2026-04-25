"""Adapter wrapping the existing MTF SMC/ICT swing scanner.

This adapter is the FIRST-CLASS strategy of the registry. It does NOT
re-implement MTF SMC; it calls into the existing
:func:`bot.mtf_smc_batch.run_mtf_smc_watchlist_scan` and translates the
``items`` list into :class:`StrategySignal` objects.

Hard rules:

* This module MUST NOT import the broker / IBKR client at module load.
  All such imports happen lazily inside :py:meth:`scan`.
* ``paper_bracket=False`` is FORCED on every call. The Strategy
  Registry layer NEVER places orders, even when invoked from a
  worker. Order placement still belongs to ``run-auto-paper-mtf-loop``
  / ``auto-paper-mtf`` and is gated by their own runtime guards.
* If the existing scanner raises (no IBKR running, no watchlist, etc.),
  the adapter returns ``StrategyScanResult(status="error", ...)``
  rather than propagating. The engine logs and moves on.
"""

from __future__ import annotations

from typing import Any

from ..base import (
    Strategy,
    StrategyContext,
    StrategyMetadata,
    StrategyScanResult,
    StrategySignal,
    _utc_now_iso,
)


# Module-level metadata so the registry / UI can read it without
# instantiating the strategy.
METADATA = StrategyMetadata(
    key="mtf_smc",
    name="MTF SMC/ICT Swing",
    version="1.0",
    description_zh="多周期 SMC/ICT 摆动级研究扫描（Daily/4H/30m/5m）。研究模式，绝不下单。",
    timeframes=("daily", "4h", "30min", "5min"),
    horizon="swing",
    research_only=True,
    requires_ibkr=True,
    enabled_by_default=True,
    status="ready",
    tags=("smc", "ict", "swing", "mtf"),
)


class MtfSmcStrategy:
    """Strategy wrapper around :func:`run_mtf_smc_watchlist_scan`."""

    metadata: StrategyMetadata = METADATA

    # ------------------------------------------------------------------
    def scan(self, ctx: StrategyContext) -> StrategyScanResult:
        started = _utc_now_iso()

        if ctx.cfg is None or ctx.journal is None:
            return StrategyScanResult(
                strategy_key=self.metadata.key,
                started_utc=started,
                finished_utc=_utc_now_iso(),
                status="skipped",
                symbol_count=0,
                notes=["mtf_smc: ctx.cfg / ctx.journal not provided; skipped."],
            )

        params = dict(ctx.extras or {})
        include_5min = bool(params.get("include_5min", True))
        include_daily = bool(params.get("include_daily", True))
        max_symbols = params.get("max_symbols")
        try:
            max_symbols_int = int(max_symbols) if max_symbols is not None else None
        except (TypeError, ValueError):
            max_symbols_int = None
        source = str(params.get("source") or "dynamic")
        chart = bool(params.get("chart", False))
        save_json = bool(params.get("save_json", True))

        # Lazy import keeps `bot.strategies` importable on machines that
        # never need to touch IBKR (e.g. the FastAPI render path).
        try:
            from ...mtf_smc_batch import run_mtf_smc_watchlist_scan  # type: ignore
        except Exception as exc:  # noqa: BLE001
            return StrategyScanResult(
                strategy_key=self.metadata.key,
                started_utc=started,
                finished_utc=_utc_now_iso(),
                status="error",
                symbol_count=0,
                notes=[f"mtf_smc: failed to import mtf_smc_batch ({exc})."],
                error=str(exc),
            )

        try:
            summary = run_mtf_smc_watchlist_scan(
                ctx.cfg,
                ctx.journal,
                use_ibkr=True,
                chart=chart,
                telegram=False,
                limit=max_symbols_int,
                source=source,
                save_json=save_json,
                include_5min=include_5min,
                include_daily=include_daily,
                # invariant: registry layer NEVER places orders
                paper_bracket=False,
                max_paper_trades=0,
            )
        except FileNotFoundError as exc:
            return StrategyScanResult(
                strategy_key=self.metadata.key,
                started_utc=started,
                finished_utc=_utc_now_iso(),
                status="error",
                symbol_count=0,
                notes=[
                    "mtf_smc: dynamic watchlist not built; "
                    "run `python -m bot.cli build-watchlist --ibkr` first."
                ],
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            return StrategyScanResult(
                strategy_key=self.metadata.key,
                started_utc=started,
                finished_utc=_utc_now_iso(),
                status="error",
                symbol_count=0,
                notes=[f"mtf_smc: scan raised ({type(exc).__name__})."],
                error=str(exc),
            )

        signals = self._summary_to_signals(summary)
        symbol_count = int(summary.get("symbols_scanned") or 0)

        # Compact summary for JSON / UI; we deliberately avoid pulling
        # in the full per-symbol dict (it's huge).
        compact_summary: dict[str, Any] = {
            "date": summary.get("date"),
            "source": summary.get("source"),
            "counts": dict(summary.get("counts") or {}),
            "top_by_alignment_score": list(summary.get("top_by_alignment_score") or []),
            "eligible_for_future_paper_trade": list(
                summary.get("eligible_for_future_paper_trade") or []
            ),
            "saved_summary_path": summary.get("_saved_summary_path"),
            "research_only": True,
            "execution_allowed": False,
        }

        return StrategyScanResult(
            strategy_key=self.metadata.key,
            started_utc=started,
            finished_utc=_utc_now_iso(),
            status="ok",
            symbol_count=symbol_count,
            signals=signals,
            summary=compact_summary,
            notes=[],
        )

    # ------------------------------------------------------------------
    def _summary_to_signals(self, summary: dict[str, Any]) -> list[StrategySignal]:
        items = summary.get("items") or []
        out: list[StrategySignal] = []
        if not isinstance(items, list):
            return out
        for it in items:
            if not isinstance(it, dict):
                continue
            sym = str(it.get("symbol") or "").upper().strip()
            if not sym:
                continue
            cat = str(it.get("alignment_category") or "")
            score = it.get("mtf_alignment_score")
            try:
                score_val = float(score) if score is not None else None
            except (TypeError, ValueError):
                score_val = None
            # Direction is unknown at this layer (the engine reports
            # bias separately); we keep "unknown" rather than guess.
            out.append(
                StrategySignal(
                    strategy_key=self.metadata.key,
                    symbol=sym,
                    direction="unknown",
                    confidence=_confidence_from_category(cat),
                    horizon="swing",
                    timeframe="30min",
                    score=score_val,
                    reason=cat or "mtf_smc",
                    payload={
                        "alignment_category": cat,
                        "eligible_for_future_paper_trade": bool(
                            it.get("eligible_for_future_paper_trade")
                        ),
                    },
                )
            )
        return out


def _confidence_from_category(cat: str) -> str:
    if cat == "FULL_ALIGNMENT":
        return "high"
    if cat == "SETUP_READY_WAITING_TRIGGER":
        return "medium"
    if cat in {"BIAS_OK_SETUP_INCOMPLETE", "CONFLICTED"}:
        return "low"
    return "unknown"


__all__ = ["METADATA", "MtfSmcStrategy"]


# Make this module satisfy the runtime-checkable Protocol via a sentinel
# instance; useful for unit tests that want to assert isinstance.
_INSTANCE: Strategy = MtfSmcStrategy()
