"""Watchlist-level SMC research scanner.

This module is the glue between :func:`bot.strategy_engine.
evaluate_smc_liquidity_reversal` and the human-facing surface
(console table, Telegram digest, batch-summary JSON). It is 100%
research-only and never imports :class:`bot.broker.Broker.place_order`.

The scanner adds two derived fields per symbol:

``smc_quality_score``
    An integer in ``[0, 100]`` that ranks setups *within the current
    watchlist pass*. It is a research heuristic, not a gate: every
    existing hard rejection (regime, stop too wide, R/R too low,
    price extended, …) still fires independently. A high score does
    **not** mean the setup is trade-ready.

``bucket``
    One of ``WATCH_NOW`` / ``NEAR_ENTRY`` / ``TOO_EXTENDED`` /
    ``STRUCTURE_INCOMPLETE`` / ``INVALID_RISK`` / ``BLOCKED``. The
    bucket chooses itself based on the evaluation payload; the
    scorer never decides membership. Buckets exist so a reviewer can
    scan the digest line-by-line without re-reading every
    ``rejection_reason``.

Safety invariants (re-stated to be explicit):

* ``execution_allowed`` stays ``False``.
* The scanner never opens an IBKR socket itself; callers pass
  already-loaded candles.
* No module import or code path in this file touches
  ``bot.broker``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import AppConfig
from .strategy_engine import StrategyEvaluation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------
BUCKETS = (
    "WATCH_NOW",
    "NEAR_ENTRY",
    "TOO_EXTENDED",
    "STRUCTURE_INCOMPLETE",
    "INVALID_RISK",
    "BLOCKED",
)

# How close (in percent) the latest close has to be to the planned
# entry for a full-structure setup to count as NEAR_ENTRY.
NEAR_ENTRY_THRESHOLD_PCT = 1.5

# Max absolute distance from entry before a setup flips to TOO_EXTENDED.
TOO_EXTENDED_THRESHOLD_PCT = 3.0


@dataclass
class ScanRow:
    """One row in the scanner's output, independent of the chart/CLI."""

    symbol: str
    evaluation: StrategyEvaluation
    bucket: str
    smc_quality_score: int
    score_breakdown: dict[str, int]
    candle_source: str = ""
    chart_path: str | None = None

    def to_summary_dict(self) -> dict[str, Any]:
        """Flat dict used in the batch summary JSON + Telegram digest."""
        plan = self.evaluation.trade_plan or {}
        seq = self.evaluation.sequence or {}
        return {
            "symbol": self.symbol,
            "bucket": self.bucket,
            "smc_quality_score": self.smc_quality_score,
            "approved_for_dry_run": self.evaluation.approved_for_dry_run,
            "execution_allowed": False,  # reiterate: never true from V0
            "market_regime": self.evaluation.market_regime,
            "sweep": bool((seq.get("sweep") or {}).get("found")),
            "choch": bool((seq.get("choch") or {}).get("found")),
            "fvg": bool((seq.get("fvg") or {}).get("found")),
            "order_block": bool((seq.get("order_block") or {}).get("found")),
            "entry_price": plan.get("entry_price"),
            "structural_stop": plan.get("structural_stop"),
            "target_1": plan.get("target_1"),
            "risk_reward_to_target_1": plan.get("risk_reward_to_target_1"),
            "stop_distance_pct": plan.get("stop_distance_pct"),
            "extension_pct_vs_latest_close": plan.get(
                "extension_pct_vs_latest_close"
            ),
            "rejection_reasons": list(self.evaluation.rejection_reasons),
            "chart_path": self.chart_path,
            "candle_source": self.candle_source,
            "score_breakdown": dict(self.score_breakdown),
        }


@dataclass
class ScanBatch:
    """Result of scanning N watchlist symbols."""

    date: str
    timeframe: str
    rows: list[ScanRow] = field(default_factory=list)

    # Regime context, populated by the CLI from the latest
    # ``data/market_regime/*.json`` snapshot (or the fallback hierarchy
    # in :func:`bot.cli._resolve_regime_context`). These fields exist
    # so the batch-summary JSON and Telegram digest can stay
    # consistent with what ``market-regime --ibkr`` produced instead
    # of silently diverging.
    market_regime: str = "unknown"
    regime_confidence: str = "low"
    regime_missing_fields: list[str] = field(default_factory=list)
    research_scans_allowed: bool = True
    new_positions_allowed: bool = False
    regime_source: str | None = None

    # Counts derived lazily so callers that only need a subset don't
    # pay for them up front.
    def bucket_counts(self) -> dict[str, int]:
        counts = {b: 0 for b in BUCKETS}
        for r in self.rows:
            counts[r.bucket] = counts.get(r.bucket, 0) + 1
        return counts

    def top_by_score(self, n: int = 5) -> list[ScanRow]:
        actionable = [r for r in self.rows if r.bucket != "BLOCKED"]
        return sorted(actionable, key=lambda r: r.smc_quality_score, reverse=True)[:n]

    def closest_to_entry(self, n: int = 5) -> list[ScanRow]:
        def _ext(r: ScanRow) -> float:
            plan = r.evaluation.trade_plan or {}
            ext = plan.get("extension_pct_vs_latest_close")
            return abs(float(ext)) if isinstance(ext, (int, float)) else 1e9

        with_plan = [r for r in self.rows if r.evaluation.trade_plan]
        return sorted(with_plan, key=_ext)[:n]

    def to_dict(self) -> dict[str, Any]:
        buckets: dict[str, list[dict[str, Any]]] = {b: [] for b in BUCKETS}
        for r in self.rows:
            buckets.setdefault(r.bucket, []).append(r.to_summary_dict())
        return {
            "date": self.date,
            "timeframe": self.timeframe,
            "symbols_scanned": len(self.rows),
            "market_regime": self.market_regime,
            "regime_confidence": self.regime_confidence,
            "regime_missing_fields": list(self.regime_missing_fields),
            "research_scans_allowed": self.research_scans_allowed,
            "new_positions_allowed": self.new_positions_allowed,
            "regime_source": self.regime_source,
            "buckets": buckets,
            "bucket_counts": self.bucket_counts(),
            "top_by_score": [r.to_summary_dict() for r in self.top_by_score(5)],
            "closest_to_entry": [
                r.to_summary_dict() for r in self.closest_to_entry(5)
            ],
            "execution_allowed": False,
            "research_only": True,
        }


# ---------------------------------------------------------------------------
# Score + bucket
# ---------------------------------------------------------------------------
def classify_bucket(evaluation: StrategyEvaluation) -> str:
    """Assign one of the :data:`BUCKETS` labels.

    Rules are deliberately explicit (no ML / heuristics) so a reviewer
    can trace the decision in the code. ``BLOCKED`` always wins when
    the regime forbids new setups — we never want to surface something
    as NEAR_ENTRY during a ``risk_off`` / ``crisis`` / news halt.
    """
    regime = (evaluation.market_regime or "").lower()
    reasons = [r.lower() for r in evaluation.rejection_reasons]
    sequence = evaluation.sequence or {}
    plan = evaluation.trade_plan or {}

    # Hard-block first.
    if regime in ("risk_off", "crisis") or any(
        r.startswith("market_regime=") for r in reasons
    ):
        if regime in ("risk_off", "crisis"):
            return "BLOCKED"
        # regime=unknown is NOT a BLOCKED in the spec - the setup is
        # still watchable, see WATCH_NOW below.
    if any("news_halt" in r or "symbol_blocked" in r for r in reasons):
        return "BLOCKED"

    has_full_structure = (
        bool((sequence.get("sweep") or {}).get("found"))
        and bool((sequence.get("choch") or {}).get("found"))
        and bool((sequence.get("fvg") or {}).get("found"))
        and bool((sequence.get("order_block") or {}).get("found"))
    )

    if not has_full_structure:
        return "STRUCTURE_INCOMPLETE"

    # Invalid-risk filters take priority over proximity.
    if any(r.startswith("stop_distance_pct") for r in reasons):
        return "INVALID_RISK"
    if any("r_r_to_target_1" in r for r in reasons):
        return "INVALID_RISK"
    if any(r.startswith("target_1_too_far") for r in reasons):
        return "INVALID_RISK"
    if plan.get("target_1") is None:
        return "INVALID_RISK"

    # Price extension checks: prefer the evaluator's own number so we
    # stay consistent with the no-chasing rule.
    ext = plan.get("extension_pct_vs_latest_close")
    if isinstance(ext, (int, float)):
        if ext > TOO_EXTENDED_THRESHOLD_PCT:
            return "TOO_EXTENDED"
        if abs(ext) <= NEAR_ENTRY_THRESHOLD_PCT:
            return "NEAR_ENTRY"

    # regime==unknown (or simply awaiting a pullback) → watchable.
    return "WATCH_NOW"


def score_setup(
    evaluation: StrategyEvaluation,
    *,
    stop_cfg_max_pct: float = 5.0,
    extension_cfg_max_pct: float = 3.0,
) -> tuple[int, dict[str, int]]:
    """Return ``(score, breakdown)`` where score is clamped to ``[0, 100]``.

    The caller passes the configured max-stop-distance and max-price-
    extension so the thresholds match whatever is in
    ``config/strategy.yaml`` instead of being hard-wired here.
    """
    sequence = evaluation.sequence or {}
    plan = evaluation.trade_plan or {}
    reasons = [r.lower() for r in evaluation.rejection_reasons]
    regime = (evaluation.market_regime or "").lower()

    breakdown: dict[str, int] = {}
    score = 0

    has_full = (
        (sequence.get("sweep") or {}).get("found")
        and (sequence.get("choch") or {}).get("found")
        and (sequence.get("fvg") or {}).get("found")
        and (sequence.get("order_block") or {}).get("found")
    )
    if has_full:
        breakdown["full_structure"] = 35
        score += 35
    else:
        breakdown["incomplete_structure"] = -30
        score -= 30

    stop_pct = plan.get("stop_distance_pct")
    if isinstance(stop_pct, (int, float)):
        if stop_pct <= stop_cfg_max_pct:
            breakdown["stop_distance_ok"] = 15
            score += 15
        else:
            breakdown["stop_distance_over_max"] = -20
            score -= 20

    rr = plan.get("risk_reward_to_target_1")
    if isinstance(rr, (int, float)) and rr >= 2.0:
        breakdown["rr_ok"] = 15
        score += 15
    elif isinstance(rr, (int, float)) and rr > 0:
        # R/R exists but below 2.
        breakdown["rr_below_min"] = -10
        score -= 10

    ext = plan.get("extension_pct_vs_latest_close")
    if isinstance(ext, (int, float)):
        if abs(ext) <= extension_cfg_max_pct:
            breakdown["extension_ok"] = 15
            score += 15
        elif ext > extension_cfg_max_pct:
            breakdown["extension_over_max"] = -20
            score -= 20

    if regime and regime not in ("risk_off", "crisis", "unknown"):
        breakdown["regime_allows_entries"] = 10
        score += 10
    elif regime == "unknown":
        breakdown["regime_unknown"] = -20
        score -= 20

    # Target validity bonus / penalty.
    target_debug = getattr(evaluation, "target_debug", {}) or {}
    if plan.get("target_1") is not None and target_debug.get("rejection_reason") is None:
        breakdown["target_valid"] = 10
        score += 10
    elif plan.get("target_1") is None:
        breakdown["no_valid_target"] = -20
        score -= 20

    # We clamp - negative or >100 scores break the Telegram digest
    # visuals, and they add no information beyond "really bad".
    score = max(0, min(100, score))
    return score, breakdown


def build_scan_row(
    evaluation: StrategyEvaluation,
    *,
    cfg_strategy_block: dict[str, Any] | None = None,
    candle_source: str = "",
    chart_path: str | None = None,
) -> ScanRow:
    """Turn one evaluation into a ScanRow."""
    stop_max = 5.0
    ext_max = 3.0
    if cfg_strategy_block:
        stop_max = float(
            (cfg_strategy_block.get("stop") or {}).get("max_allowed_stop_pct", 5.0)
        )
        ext_max = float(
            (cfg_strategy_block.get("entry") or {}).get(
                "reject_if_price_extended_from_entry_pct", 3.0
            )
        )
    score, breakdown = score_setup(
        evaluation,
        stop_cfg_max_pct=stop_max,
        extension_cfg_max_pct=ext_max,
    )
    return ScanRow(
        symbol=evaluation.symbol,
        evaluation=evaluation,
        bucket=classify_bucket(evaluation),
        smc_quality_score=score,
        score_breakdown=breakdown,
        candle_source=candle_source,
        chart_path=chart_path or evaluation.chart_path,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def batch_summary_filename(date: str, timeframe: str) -> str:
    """Return the on-disk filename for a batch summary.

    Prompt 10A: the timeframe is part of the filename so the daily and
    30min research scans live side-by-side without clobbering each
    other. ``daily`` also gets the suffix — the legacy
    ``{date}-watchlist-summary.json`` is still recognised by
    :func:`load_batch_summary_for_timeframe` for backward compatibility.
    """
    from .smc_timeframes import normalise_timeframe

    return f"{date}-{normalise_timeframe(timeframe)}-watchlist-summary.json"


def save_batch_summary(
    cfg: AppConfig, batch: ScanBatch, *, directory: str = "data/smc_setups"
) -> Path:
    """Write the batch summary JSON.

    Filename follows ``YYYY-MM-DD-{timeframe}-watchlist-summary.json``
    so a human can tell at a glance which day *and* which timeframe
    the scan was produced for.
    """
    out_dir = cfg.absolute(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / batch_summary_filename(batch.date, batch.timeframe)
    path.write_text(
        json.dumps(batch.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )
    return path


class BatchSummaryNotFoundError(FileNotFoundError):
    """Raised when no scan summary matches ``(date, timeframe)``."""


def load_batch_summary_for_timeframe(
    cfg: AppConfig,
    *,
    timeframe: str,
    date: str | None = None,
    directory: str = "data/smc_setups",
) -> tuple[dict[str, Any], Path]:
    """Return ``(summary_dict, path)`` for ``timeframe`` on ``date``.

    * ``date=None`` → latest matching file for the timeframe.
    * For ``daily`` we also accept the legacy
      ``{date}-watchlist-summary.json`` filename so older scans keep
      working.
    """
    from .smc_timeframes import normalise_timeframe

    tf = normalise_timeframe(timeframe)
    dir_ = cfg.absolute(directory)
    if not dir_.is_dir():
        raise BatchSummaryNotFoundError(
            f"{directory} directory does not exist: {dir_}"
        )
    candidates: list[Path] = []
    if date:
        candidates.append(dir_ / f"{date}-{tf}-watchlist-summary.json")
        if tf == "daily":
            candidates.append(dir_ / f"{date}-watchlist-summary.json")
    else:
        candidates.extend(
            sorted(dir_.glob(f"*-{tf}-watchlist-summary.json"))
        )
        if tf == "daily":
            # Legacy filenames without a timeframe component.
            legacy = [
                p for p in sorted(dir_.glob("*-watchlist-summary.json"))
                if not any(p.name.endswith(f"-{ttf}-watchlist-summary.json")
                           for ttf in ("daily", "30min"))
            ]
            candidates.extend(legacy)
    candidates = [p for p in candidates if p.exists()]
    if not candidates:
        raise BatchSummaryNotFoundError(
            f"no scan summary for timeframe={tf!r} "
            f"{('date=' + date) if date else ''} under {dir_}"
        )
    path = candidates[-1]
    with path.open("r", encoding="utf-8") as f:
        return json.load(f), path


# ---------------------------------------------------------------------------
# Telegram digest
# ---------------------------------------------------------------------------
def format_telegram_digest(
    batch: ScanBatch,
    *,
    parse_mode: str | None = "HTML",
    source_label: str | None = None,
) -> str:
    """Return the digest text to hand to :func:`send_telegram_message`.

    The digest intentionally omits account values, position size, and
    actual dollar risk — redaction already strips them, but building
    the message without them in the first place is the safer default.
    """
    counts = batch.bucket_counts()
    top = batch.top_by_score(5)
    closest = batch.closest_to_entry(5)

    regime_line = (
        f"Market regime: {batch.market_regime} "
        f"(confidence={batch.regime_confidence})"
    )
    missing_line = (
        "Missing: " + ", ".join(batch.regime_missing_fields)
        if batch.regime_missing_fields
        else None
    )
    new_pos_line = (
        f"New positions allowed: "
        f"{'yes' if batch.new_positions_allowed else 'no'}"
    )

    if parse_mode == "HTML":
        out: list[str] = [
            f"<b>SMC Watchlist Research Digest — {batch.date} "
            f"({batch.timeframe})</b>",
        ]
        if source_label:
            out.append(f"Watchlist source: {source_label}")
        out.append(f"Timeframe: {batch.timeframe}")
        out.append(regime_line)
        if missing_line:
            out.append(missing_line)
        out.append(new_pos_line)
        out.append("Research only: yes")
        out += [
            "",
            f"Scanned: {len(batch.rows)}",
            f"WATCH_NOW: {counts.get('WATCH_NOW', 0)}  "
            f"NEAR_ENTRY: {counts.get('NEAR_ENTRY', 0)}  "
            f"TOO_EXTENDED: {counts.get('TOO_EXTENDED', 0)}",
            f"STRUCTURE_INCOMPLETE: {counts.get('STRUCTURE_INCOMPLETE', 0)}  "
            f"INVALID_RISK: {counts.get('INVALID_RISK', 0)}  "
            f"BLOCKED: {counts.get('BLOCKED', 0)}",
        ]
        if top:
            out.append("")
            out.append("<b>Top by score</b>")
            for r in top:
                out.append(_line_for(r))
        if closest:
            out.append("")
            out.append("<b>Closest to entry</b>")
            for r in closest:
                out.append(_line_for(r, mode="near"))
        out.append("")
        out.append(
            "<i>Research only. execution_allowed=false. "
            "Digest is not a trade signal.</i>"
        )
        return "\n".join(out)

    # Plain-text fallback.
    lines: list[str] = [
        f"SMC Watchlist Research Digest — {batch.date} ({batch.timeframe})",
    ]
    if source_label:
        lines.append(f"Watchlist source: {source_label}")
    lines.append(f"Timeframe: {batch.timeframe}")
    lines.append(regime_line)
    if missing_line:
        lines.append(missing_line)
    lines.append(new_pos_line)
    lines.append("Research only: yes")
    lines += [
        f"Scanned: {len(batch.rows)}",
        f"WATCH_NOW={counts.get('WATCH_NOW', 0)} "
        f"NEAR_ENTRY={counts.get('NEAR_ENTRY', 0)} "
        f"TOO_EXTENDED={counts.get('TOO_EXTENDED', 0)} "
        f"STRUCTURE_INCOMPLETE={counts.get('STRUCTURE_INCOMPLETE', 0)} "
        f"INVALID_RISK={counts.get('INVALID_RISK', 0)} "
        f"BLOCKED={counts.get('BLOCKED', 0)}",
    ]
    if top:
        lines.append("")
        lines.append("Top by score:")
        for r in top:
            lines.append(_line_for(r, plain=True))
    if closest:
        lines.append("")
        lines.append("Closest to entry:")
        for r in closest:
            lines.append(_line_for(r, mode="near", plain=True))
    lines.append("")
    lines.append("Research only. execution_allowed=false.")
    return "\n".join(lines)


def _line_for(row: ScanRow, *, mode: str = "score", plain: bool = False) -> str:
    plan = row.evaluation.trade_plan or {}
    entry = plan.get("entry_price")
    rr = plan.get("risk_reward_to_target_1")
    ext = plan.get("extension_pct_vs_latest_close")

    pieces = [row.symbol, row.bucket]
    if mode == "score":
        pieces.append(f"score={row.smc_quality_score}")
    if isinstance(entry, (int, float)):
        pieces.append(f"entry={entry:.2f}")
    if isinstance(rr, (int, float)):
        pieces.append(f"R/R={rr:.2f}")
    if mode == "near" and isinstance(ext, (int, float)):
        pieces.append(f"ext={ext:+.2f}%")
    line = " | ".join(pieces)
    if plain:
        return line
    # HTML-safe (no free-form user input - symbol is A-Z only).
    return f"• {line}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def today_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


__all__ = [
    "BUCKETS",
    "BatchSummaryNotFoundError",
    "NEAR_ENTRY_THRESHOLD_PCT",
    "TOO_EXTENDED_THRESHOLD_PCT",
    "ScanRow",
    "ScanBatch",
    "batch_summary_filename",
    "build_scan_row",
    "classify_bucket",
    "format_telegram_digest",
    "load_batch_summary_for_timeframe",
    "save_batch_summary",
    "score_setup",
    "today_utc_iso",
]
