"""MultiStrategyEngine — runs registered strategies and aggregates results.

The engine is a thin coordinator. It does NOT:

* place orders (the registry layer is research-only),
* connect to IBKR (each adapter does its own lazy connection inside
  ``scan`` if it needs to),
* mutate the registry,
* swallow programmer errors (TypeError / KeyError propagate).

It DOES:

* enumerate enabled strategies (or a caller-supplied subset),
* invoke ``Strategy.scan(ctx)`` per strategy,
* collect ``StrategyScanResult`` objects into a single
  :class:`MultiStrategyRunSummary`,
* write a per-day JSON snapshot under ``data/strategies/`` if a writer
  is provided.

Design notes:

* ``execution_allowed`` on the run summary is ALWAYS False at this
  stage. Wiring paper execution is a separate phase that will add a
  guarded ``execute_signals`` method, NOT extend ``run``.
* Strategies that raise (instead of returning ``status="error"``) are
  caught here so one buggy strategy cannot poison the rest of the run.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from click.exceptions import Exit as ClickExit

from .base import StrategyContext, StrategyScanResult, _utc_now_iso
from .config import StrategyRuntimeConfig
from .registry import StrategyRegistry, default_registry

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MultiStrategyRunSummary:
    """Aggregate of all strategy scans in one engine run."""

    started_utc: str
    finished_utc: str
    enabled_keys: list[str]
    skipped_keys: list[str]
    requested_keys: list[str]
    results: list[StrategyScanResult]
    paper_only: bool = True
    execution_allowed: bool = False  # invariant at this stage

    def __post_init__(self) -> None:
        if self.execution_allowed:
            raise ValueError(
                "MultiStrategyRunSummary.execution_allowed must be False at this stage."
            )
        if not self.paper_only:
            raise ValueError(
                "MultiStrategyRunSummary.paper_only must be True (paper-only invariant)."
            )

    @property
    def total_signals(self) -> int:
        return sum(r.signal_count for r in self.results)

    def to_dict(self) -> dict[str, object]:
        return {
            "started_utc": self.started_utc,
            "finished_utc": self.finished_utc,
            "enabled_keys": list(self.enabled_keys),
            "skipped_keys": list(self.skipped_keys),
            "requested_keys": list(self.requested_keys),
            "results": [r.to_dict() for r in self.results],
            "total_signals": self.total_signals,
            "paper_only": self.paper_only,
            "execution_allowed": self.execution_allowed,
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class MultiStrategyEngine:
    """Coordinates strategy scans across the registry."""

    def __init__(
        self,
        *,
        registry: StrategyRegistry | None = None,
        runtime_config: StrategyRuntimeConfig | None = None,
    ) -> None:
        self.registry = registry or default_registry()
        self.runtime_config = runtime_config or StrategyRuntimeConfig()

    # ------------------------------------------------------------------
    def resolve_keys_to_run(
        self,
        *,
        only: Sequence[str] | None = None,
        include_disabled: bool = False,
    ) -> tuple[list[str], list[str]]:
        """Compute (run_keys, skipped_keys).

        * If ``only`` is provided, run that subset (intersected with
          the registry; unknown keys go to ``skipped_keys``).
        * Otherwise run every registered key whose runtime config has
          ``enabled: true``, unless ``include_disabled=True``.
        """
        registered = self.registry.keys()
        if only:
            run: list[str] = []
            skipped: list[str] = []
            for k in only:
                if k in registered:
                    run.append(k)
                else:
                    skipped.append(k)
            return run, skipped

        if include_disabled:
            return list(registered), []

        run = []
        skipped = []
        for k in registered:
            entry = self.runtime_config.get(k)
            if entry.enabled:
                run.append(k)
            else:
                skipped.append(k)
        return run, skipped

    # ------------------------------------------------------------------
    def run(
        self,
        ctx: StrategyContext,
        *,
        only: Sequence[str] | None = None,
        include_disabled: bool = False,
    ) -> MultiStrategyRunSummary:
        if not ctx.paper_only:
            raise ValueError("MultiStrategyEngine.run: ctx.paper_only must be True.")
        if ctx.paper_execution_allowed:
            raise ValueError(
                "MultiStrategyEngine.run: ctx.paper_execution_allowed must be False at this stage."
            )

        run_keys, skipped_keys = self.resolve_keys_to_run(
            only=only, include_disabled=include_disabled
        )

        started = _utc_now_iso()
        results: list[StrategyScanResult] = []
        for key in run_keys:
            strategy = self.registry.get(key)
            entry = self.runtime_config.get(key)
            # Merge per-strategy params into ctx.extras so adapters can
            # read their own config without us baking strategy-specific
            # knowledge into the engine.
            merged_extras = dict(ctx.extras or {})
            merged_extras.update(dict(entry.params))
            sub_ctx = StrategyContext(
                cfg=ctx.cfg,
                journal=ctx.journal,
                symbols=ctx.symbols,
                market_regime=ctx.market_regime,
                regime_confidence=ctx.regime_confidence,
                paper_only=True,
                paper_execution_allowed=False,
                extras=merged_extras,
            )
            try:
                result = strategy.scan(sub_ctx)
            except ClickExit:
                raise
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001
                LOG.exception("strategy %s scan raised", key)
                now = _utc_now_iso()
                result = StrategyScanResult(
                    strategy_key=key,
                    started_utc=now,
                    finished_utc=now,
                    status="error",
                    symbol_count=0,
                    notes=[f"engine: scan raised ({type(exc).__name__})."],
                    error=str(exc),
                )
            results.append(result)

        return MultiStrategyRunSummary(
            started_utc=started,
            finished_utc=_utc_now_iso(),
            enabled_keys=run_keys,
            skipped_keys=skipped_keys,
            requested_keys=list(only) if only else list(run_keys),
            results=results,
        )


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def write_run_summary(
    summary: MultiStrategyRunSummary,
    *,
    output_dir: Path,
    name: str = "multi-strategy-scan",
) -> Path:
    """Write ``<YYYY-MM-DD>-<name>.json`` and return the path."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = output_dir / f"{day}-{name}.json"
    payload = summary.to_dict()
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def write_single_scan(
    result: StrategyScanResult,
    *,
    output_dir: Path,
) -> Path:
    """Write ``<YYYY-MM-DD>-<key>-scan.json`` and return the path."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = output_dir / f"{day}-{result.strategy_key}-scan.json"
    out.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return out


def render_summary_zh(summary: MultiStrategyRunSummary) -> str:
    """Compact Chinese console summary for CLI output."""
    lines: list[str] = []
    lines.append(f"多策略扫描完成 (started_utc={summary.started_utc})")
    lines.append(f"  paper_only: {summary.paper_only}, execution_allowed: {summary.execution_allowed}")
    lines.append(f"  执行: {summary.enabled_keys or '(无)'}")
    lines.append(f"  跳过: {summary.skipped_keys or '(无)'}")
    lines.append(f"  信号合计: {summary.total_signals}")
    for r in summary.results:
        lines.append(
            f"  - {r.strategy_key}: status={r.status} symbols={r.symbol_count} "
            f"signals={r.signal_count} notes={r.notes or '[]'}"
        )
    return "\n".join(lines)


def iter_results(summary: MultiStrategyRunSummary) -> Iterable[StrategyScanResult]:
    yield from summary.results


__all__ = [
    "MultiStrategyEngine",
    "MultiStrategyRunSummary",
    "iter_results",
    "render_summary_zh",
    "write_run_summary",
    "write_single_scan",
]
