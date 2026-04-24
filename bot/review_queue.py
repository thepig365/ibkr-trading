"""SMC review queue — Prompt 9 Part A.

Turns a :mod:`bot.smc_scanner` batch-summary JSON into a human review
queue. The queue is **research only** — it does not approve trades,
does not place orders, and keeps ``execution_allowed`` hard-coded to
``False`` on every item and on the envelope.

The module lives below the CLI layer on purpose so it can be unit
tested without spinning up Typer or IBKR. See
``docs/smc-review-queue.md`` for the design rationale and usage.
"""

from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from .config import AppConfig

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
ReviewCategory = Literal[
    "READY_FOR_MANUAL_CHART_REVIEW",
    "PULLBACK_WATCH",
    "INVALID_RISK_REJECT",
    "STRUCTURE_WATCH",
    "BLOCKED_BY_REGIME_OR_NEWS",
    "IGNORE_FOR_NOW",
]

REVIEW_CATEGORIES: tuple[ReviewCategory, ...] = (
    "READY_FOR_MANUAL_CHART_REVIEW",
    "PULLBACK_WATCH",
    "INVALID_RISK_REJECT",
    "STRUCTURE_WATCH",
    "BLOCKED_BY_REGIME_OR_NEWS",
    "IGNORE_FOR_NOW",
)

# Matches the thresholds described in cursor_smc_liquidity_reversal_research_module_v2
# and ``config/strategy.yaml``. They are mirrored here as defaults so the
# review queue can be built from an on-disk summary without re-loading
# the strategy block.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "max_stop_distance_pct": 5.0,
    "max_extension_pct": 3.0,
    "near_entry_pct": 1.5,
    "min_risk_reward": 2.0,
}

WHAT_TO_WATCH_NEXT: dict[str, str] = {
    "READY_FOR_MANUAL_CHART_REVIEW": (
        "Manually inspect chart. Confirm FVG/OB placement, entry "
        "proximity, news context, and market regime before any future "
        "paper execution."
    ),
    "PULLBACK_WATCH": (
        "Set alert near entry zone. Do not chase current price."
    ),
    "INVALID_RISK_REJECT": (
        "Reject for now. Reconsider only if a tighter structure forms."
    ),
    "STRUCTURE_WATCH": (
        "Watch for ChoCH and FVG formation."
    ),
    "BLOCKED_BY_REGIME_OR_NEWS": (
        "Do not review for entry until block is cleared."
    ),
    "IGNORE_FOR_NOW": "No action.",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class ReviewItem:
    """A single symbol's place in the review queue.

    ``review_priority_score`` is strictly a human-sort hint. Approving
    a trade requires a human chart inspection *plus* the other
    research gates — never this score alone.
    """

    symbol: str
    review_category: ReviewCategory
    review_priority_score: int
    scanner_bucket: str
    smc_quality_score: int
    market_regime: str
    regime_confidence: str
    new_positions_allowed: bool
    execution_allowed: bool = False
    research_only: bool = True
    structure: dict[str, bool] = field(default_factory=dict)
    trade_plan: dict[str, Any] = field(default_factory=dict)
    rejection_reasons: list[str] = field(default_factory=list)
    human_review_reason: str = ""
    what_to_watch_next: str = ""
    chart_path: str = ""
    review_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        # Safety invariants enforced at serialization time so a buggy
        # caller cannot flip them upstream by mutating the dataclass.
        d = asdict(self)
        d["execution_allowed"] = False
        d["research_only"] = True
        return d


@dataclass
class ReviewQueue:
    """Envelope matching the schema in ``docs/smc-review-queue.md``."""

    date: str
    source_summary: str
    market_regime: str
    regime_confidence: str
    regime_missing_fields: list[str]
    new_positions_allowed: bool
    research_scans_allowed: bool
    items: list[ReviewItem] = field(default_factory=list)

    # Always false / true; these are included on the envelope so any
    # downstream consumer can see the safety flags without having to
    # look at each item.
    execution_allowed: bool = False
    research_only: bool = True

    def counts(self) -> dict[str, int]:
        counts = {cat: 0 for cat in REVIEW_CATEGORIES}
        for item in self.items:
            counts[item.review_category] = counts.get(item.review_category, 0) + 1
        return counts

    def items_by_category(self, category: ReviewCategory) -> list[ReviewItem]:
        return [i for i in self.items if i.review_category == category]

    def top_items(self, n: int) -> list[ReviewItem]:
        return sorted(
            self.items, key=lambda i: i.review_priority_score, reverse=True
        )[:n]

    def tradeable_candidates(self) -> list[ReviewItem]:
        """Items explicitly labelled tradeable for the 09:45 digest.

        Requirement (Prompt 9): the opening report must list ICT/SMC
        *tradeable candidates* — those in ``READY_FOR_MANUAL_CHART_REVIEW``
        (or a future ``NEAR_ENTRY``-equivalent). We intentionally do
        **not** call any of these a "trade signal"; they are
        candidates for manual chart review only.
        """
        return [
            i for i in self.items
            if i.review_category == "READY_FOR_MANUAL_CHART_REVIEW"
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "source_summary": self.source_summary,
            "market_regime": self.market_regime,
            "regime_confidence": self.regime_confidence,
            "regime_missing_fields": list(self.regime_missing_fields),
            "new_positions_allowed": self.new_positions_allowed,
            "research_scans_allowed": self.research_scans_allowed,
            "execution_allowed": False,
            "research_only": True,
            "counts": self.counts(),
            "items": [i.to_dict() for i in self.items],
        }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def _structure_complete(row: dict[str, Any]) -> bool:
    return all(bool(row.get(k)) for k in ("sweep", "choch", "fvg", "order_block"))


def _rejection_mentions(reasons: Iterable[str], needle: str) -> bool:
    return any(needle in r for r in reasons)


def classify_review_category(
    row: dict[str, Any], thresholds: dict[str, float]
) -> ReviewCategory:
    """Convert a scanner row into a review category.

    This is intentionally a *pure* function over the row dict so tests
    can feed in hand-crafted rows without running the full SMC
    evaluator.
    """

    scanner_bucket = str(row.get("bucket") or "")
    reasons = list(row.get("rejection_reasons") or [])
    structure_ok = _structure_complete(row)
    stop_pct = row.get("stop_distance_pct")
    rr = row.get("risk_reward_to_target_1")
    ext = row.get("extension_pct_vs_latest_close")

    max_stop = thresholds["max_stop_distance_pct"]
    max_ext = thresholds["max_extension_pct"]
    min_rr = thresholds["min_risk_reward"]

    # Hard-blocks go first so they cannot be "rescued" by other rules.
    if scanner_bucket == "BLOCKED" or _rejection_mentions(
        reasons, "market_regime"
    ) or _rejection_mentions(reasons, "halt") or _rejection_mentions(
        reasons, "blocked_by_news"
    ):
        return "BLOCKED_BY_REGIME_OR_NEWS"

    if structure_ok:
        stop_ok = isinstance(stop_pct, (int, float)) and float(stop_pct) <= max_stop
        rr_ok = isinstance(rr, (int, float)) and float(rr) >= min_rr
        ext_ok = isinstance(ext, (int, float)) and float(ext) <= max_ext
        target_ok = bool(row.get("target_1")) and isinstance(rr, (int, float)) and rr > 0

        if stop_ok and rr_ok and ext_ok and target_ok:
            return "READY_FOR_MANUAL_CHART_REVIEW"

        # Stop too wide / RR too low / no target ⇒ risk geometry bad.
        if not stop_ok or not rr_ok or not target_ok:
            return "INVALID_RISK_REJECT"

        # Everything except extension is fine ⇒ pullback watch.
        if not ext_ok:
            return "PULLBACK_WATCH"

        return "INVALID_RISK_REJECT"

    # Partial structure: a sweep exists but ChoCH/FVG/OB is missing.
    if bool(row.get("sweep")):
        return "STRUCTURE_WATCH"

    # Low-value, nothing interesting.
    return "IGNORE_FOR_NOW"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def review_priority_score(
    row: dict[str, Any], thresholds: dict[str, float]
) -> int:
    """Compute 0..100 human-sorting score per the Prompt 9 spec."""

    score = 0
    reasons = list(row.get("rejection_reasons") or [])
    structure_ok = _structure_complete(row)
    stop_pct = row.get("stop_distance_pct")
    rr = row.get("risk_reward_to_target_1")
    ext = row.get("extension_pct_vs_latest_close")

    max_stop = thresholds["max_stop_distance_pct"]
    max_ext = thresholds["max_extension_pct"]
    min_rr = thresholds["min_risk_reward"]
    near_entry = thresholds["near_entry_pct"]

    if structure_ok:
        score += 30
    else:
        score -= 30

    if isinstance(rr, (int, float)) and float(rr) >= min_rr:
        score += 15

    if isinstance(stop_pct, (int, float)):
        if float(stop_pct) <= max_stop:
            score += 15
        else:
            score -= 25

    if isinstance(ext, (int, float)):
        if float(ext) <= max_ext:
            score += 15
            if float(ext) <= near_entry:
                score += 10
        else:
            score -= 20

    if bool(row.get("target_1")):
        score += 10
    else:
        score -= 25

    if row.get("chart_path"):
        score += 5
    else:
        score -= 10

    # Dynamic-watchlist signals (if present in the item). Scanner rows
    # do not carry the full candidate metadata, but the batch-summary
    # may stash hints in ``review_notes`` / ``reasons``.
    notes = [str(n).lower() for n in (row.get("review_notes") or [])]
    if any("high_relative_volume" in n or "high_current_dollar_volume" in n
           for n in notes):
        score += 5

    # Regime / news blocks already dominate the classifier; keep the
    # signal in the score too so sorted output matches the label.
    if _rejection_mentions(reasons, "market_regime"):
        score -= 50
    if _rejection_mentions(reasons, "blocked_by_news"):
        score -= 50

    return max(0, min(100, score))


# ---------------------------------------------------------------------------
# Human reason
# ---------------------------------------------------------------------------
def _fmt(x: object, pct: bool = False) -> str:
    if isinstance(x, (int, float)):
        return f"{float(x):.2f}{'%' if pct else ''}"
    return "-"


def human_review_reason(
    row: dict[str, Any],
    category: ReviewCategory,
    thresholds: dict[str, float],
) -> str:
    """A concise English sentence for the reviewer."""

    sym = str(row.get("symbol") or "").upper() or "-"
    entry = row.get("entry_price")
    stop_pct = row.get("stop_distance_pct")
    ext = row.get("extension_pct_vs_latest_close")
    rr = row.get("risk_reward_to_target_1")
    max_stop = thresholds["max_stop_distance_pct"]
    max_ext = thresholds["max_extension_pct"]
    min_rr = thresholds["min_risk_reward"]

    if category == "READY_FOR_MANUAL_CHART_REVIEW":
        return (
            f"{sym}: full SMC structure exists, R/R={_fmt(rr)}, "
            f"stop={_fmt(stop_pct, pct=True)}, ext={_fmt(ext, pct=True)}. "
            "Candidate for manual chart review only; not an execution approval."
        )
    if category == "PULLBACK_WATCH":
        return (
            f"{sym}: full SMC structure exists, R/R={_fmt(rr)}, "
            f"stop={_fmt(stop_pct, pct=True)} is within {max_stop:.2f}%, "
            f"but price is {_fmt(ext, pct=True)} above entry "
            f"({_fmt(entry)}). Do not chase. Review only if price "
            f"pulls back toward {_fmt(entry)}."
        )
    if category == "INVALID_RISK_REJECT":
        bits: list[str] = []
        if isinstance(stop_pct, (int, float)) and float(stop_pct) > max_stop:
            bits.append(
                f"structural stop is {_fmt(stop_pct, pct=True)}, wider "
                f"than the {max_stop:.2f}% limit"
            )
        if not bool(row.get("target_1")) or not isinstance(rr, (int, float)) or float(rr) < min_rr:
            bits.append(
                f"R/R={_fmt(rr)} below min {min_rr:.2f} or no valid target"
            )
        detail = "; ".join(bits) or "risk geometry not acceptable"
        return (
            f"{sym}: full SMC structure exists, but {detail}. Reject "
            "unless structure tightens or a better entry forms."
        )
    if category == "STRUCTURE_WATCH":
        missing: list[str] = []
        if not row.get("choch"):
            missing.append("ChoCH has not confirmed")
        if not row.get("fvg"):
            missing.append("no FVG")
        if not row.get("order_block"):
            missing.append("no order block")
        core = (
            f"{sym}: liquidity sweep exists, but " + ", ".join(missing)
            if missing
            else f"{sym}: structure is incomplete"
        )
        return core + ". Watch for structure completion; do not treat as a setup."
    if category == "BLOCKED_BY_REGIME_OR_NEWS":
        return (
            f"{sym}: blocked by market regime or news/halt. "
            "Do not review for entry until block is cleared."
        )
    return f"{sym}: low value candidate; no action."


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------
class SummaryNotFoundError(FileNotFoundError):
    """Raised when no scan summary is available to build the queue from."""


def _iter_summary_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the bucketed summary into one list (deduplicated)."""
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for bucket_rows in (summary.get("buckets") or {}).values():
        for r in bucket_rows or []:
            sym = str(r.get("symbol") or "").upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            rows.append(r)
    return rows


def load_latest_summary(cfg: AppConfig, date: str | None = None) -> tuple[dict[str, Any], Path]:
    """Return (summary, path) for ``date`` or the latest on disk.

    Raises :class:`SummaryNotFoundError` if nothing matches. The
    caller is responsible for presenting a friendly CLI error.
    """
    dir_ = cfg.absolute("data/smc_setups")
    if not dir_.is_dir():
        raise SummaryNotFoundError(
            f"data/smc_setups directory does not exist: {dir_}"
        )
    if date:
        path = dir_ / f"{date}-watchlist-summary.json"
        if not path.exists():
            raise SummaryNotFoundError(
                f"no scan summary for {date}: {path}"
            )
    else:
        summaries = sorted(dir_.glob("*-watchlist-summary.json"))
        if not summaries:
            raise SummaryNotFoundError(
                f"no *-watchlist-summary.json under {dir_}"
            )
        path = summaries[-1]
    with path.open("r", encoding="utf-8") as f:
        return json.load(f), path


# ---------------------------------------------------------------------------
# Queue builder
# ---------------------------------------------------------------------------
def build_review_queue(
    summary: dict[str, Any],
    *,
    source_path: Path | str = "",
    thresholds: dict[str, float] | None = None,
    max_items: int = 50,
    min_review_priority_score: int = 0,
    include_categories: Iterable[str] | None = None,
) -> ReviewQueue:
    """Turn a scanner batch-summary dict into a :class:`ReviewQueue`."""

    thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    rows = _iter_summary_rows(summary)

    items: list[ReviewItem] = []
    for row in rows:
        category = classify_review_category(row, thresholds)
        score = review_priority_score(row, thresholds)
        reason = human_review_reason(row, category, thresholds)
        item = ReviewItem(
            symbol=str(row.get("symbol") or "").upper(),
            review_category=category,
            review_priority_score=score,
            scanner_bucket=str(row.get("bucket") or ""),
            smc_quality_score=int(row.get("smc_quality_score") or 0),
            market_regime=str(
                summary.get("market_regime") or row.get("market_regime") or "unknown"
            ),
            regime_confidence=str(summary.get("regime_confidence") or "low"),
            new_positions_allowed=bool(summary.get("new_positions_allowed", False)),
            structure={
                "sweep": bool(row.get("sweep")),
                "choch": bool(row.get("choch")),
                "fvg": bool(row.get("fvg")),
                "order_block": bool(row.get("order_block")),
            },
            trade_plan={
                "entry_price": row.get("entry_price"),
                "structural_stop": row.get("structural_stop"),
                "target_1": row.get("target_1"),
                "risk_reward_to_target_1": row.get("risk_reward_to_target_1"),
                "stop_distance_pct": row.get("stop_distance_pct"),
                "extension_pct_vs_latest_close": row.get(
                    "extension_pct_vs_latest_close"
                ),
            },
            rejection_reasons=list(row.get("rejection_reasons") or []),
            human_review_reason=reason,
            what_to_watch_next=WHAT_TO_WATCH_NEXT.get(category, ""),
            chart_path=str(row.get("chart_path") or ""),
            review_notes=[],
        )
        items.append(item)

    if include_categories:
        allowed = set(include_categories)
        items = [i for i in items if i.review_category in allowed]
    if min_review_priority_score > 0:
        items = [
            i for i in items
            if i.review_priority_score >= min_review_priority_score
            or i.review_category
            in {"READY_FOR_MANUAL_CHART_REVIEW", "PULLBACK_WATCH",
                "INVALID_RISK_REJECT", "BLOCKED_BY_REGIME_OR_NEWS"}
        ]

    # Sort: higher priority first, then by symbol for stable ordering.
    items.sort(key=lambda i: (-i.review_priority_score, i.symbol))
    if max_items and len(items) > max_items:
        items = items[:max_items]

    queue = ReviewQueue(
        date=str(summary.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        source_summary=str(source_path),
        market_regime=str(summary.get("market_regime") or "unknown"),
        regime_confidence=str(summary.get("regime_confidence") or "low"),
        regime_missing_fields=list(summary.get("regime_missing_fields") or []),
        new_positions_allowed=bool(summary.get("new_positions_allowed", False)),
        research_scans_allowed=bool(summary.get("research_scans_allowed", True)),
        items=items,
    )
    return queue


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def save_review_queue(cfg: AppConfig, queue: ReviewQueue) -> Path:
    out_dir = cfg.absolute("data/review_queue")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{queue.date}-smc-review-queue.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(queue.to_dict(), f, indent=2, default=str)
    return path


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def _shorten_chart_path(path: str) -> str:
    if not path:
        return "-"
    try:
        return Path(path).name
    except Exception:  # noqa: BLE001 - filename only
        return path[-40:]


def format_markdown(queue: ReviewQueue, *, top: int = 10) -> str:
    """Return a markdown block to append to memory/SMC-REVIEW-QUEUE.md."""
    counts = queue.counts()
    lines: list[str] = [
        f"# SMC Review Queue — {queue.date}",
        "",
        f"Market regime: {queue.market_regime}",
        f"Confidence: {queue.regime_confidence}",
        "Missing fields: "
        + (", ".join(queue.regime_missing_fields) if queue.regime_missing_fields else "-"),
        f"New positions allowed: {'yes' if queue.new_positions_allowed else 'no'}",
        "Execution allowed: no",
        "Research only: yes",
        "",
        "## Summary",
        "",
        "| Category | Count |",
        "|---|---:|",
    ]
    for cat in REVIEW_CATEGORIES:
        lines.append(f"| {cat} | {counts.get(cat, 0)} |")

    lines += ["", "## Top Review Items", "",
              "| Symbol | Category | Score | Entry | Stop | T1 | R/R | Reason |",
              "|---|---|---:|---:|---:|---:|---:|---|"]
    for i in queue.top_items(top):
        tp = i.trade_plan
        lines.append(
            f"| {i.symbol} | {i.review_category} | {i.review_priority_score} | "
            f"{_fmt(tp.get('entry_price'))} | {_fmt(tp.get('structural_stop'))} | "
            f"{_fmt(tp.get('target_1'))} | "
            f"{_fmt(tp.get('risk_reward_to_target_1'))} | "
            f"{i.human_review_reason.replace('|', '/').strip()} |"
        )

    for title, cat in (
        ("Pullback Watch", "PULLBACK_WATCH"),
        ("Invalid Risk Rejects", "INVALID_RISK_REJECT"),
        ("Structure Watch", "STRUCTURE_WATCH"),
        ("Blocked", "BLOCKED_BY_REGIME_OR_NEWS"),
    ):
        rows = queue.items_by_category(cat)  # type: ignore[arg-type]
        if not rows:
            continue
        lines += ["", f"## {title}", ""]
        for r in rows[:top]:
            tp = r.trade_plan
            lines.append(
                f"- {r.symbol} (score={r.review_priority_score}) — "
                f"entry {_fmt(tp.get('entry_price'))}, "
                f"stop {_fmt(tp.get('structural_stop'))} "
                f"({_fmt(tp.get('stop_distance_pct'), pct=True)}), "
                f"T1 {_fmt(tp.get('target_1'))}, "
                f"R/R {_fmt(tp.get('risk_reward_to_target_1'))}: "
                f"{r.human_review_reason}"
            )

    lines += [
        "",
        "## Reminder",
        "",
        "This is a research review queue only. It does not approve "
        "trades. No orders are placed.",
        "",
    ]
    return "\n".join(lines)


def append_markdown(cfg: AppConfig, queue: ReviewQueue, *, top: int = 10) -> Path:
    path = cfg.absolute("memory/SMC-REVIEW-QUEUE.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = format_markdown(queue, top=top)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n" + body + "\n")
    return path


# ---------------------------------------------------------------------------
# Telegram digest
# ---------------------------------------------------------------------------
def format_telegram_digest(
    queue: ReviewQueue,
    *,
    parse_mode: str | None = "HTML",
    top: int = 5,
) -> str:
    """Concise digest for Telegram.

    The message uses "candidate for manual review" wording instead of
    "trade signal" everywhere, per the Prompt 9 safety requirement.
    """
    counts = queue.counts()
    tradeable = queue.tradeable_candidates()
    pullback = queue.items_by_category("PULLBACK_WATCH")
    invalid = queue.items_by_category("INVALID_RISK_REJECT")
    structure = queue.items_by_category("STRUCTURE_WATCH")

    esc = html.escape if parse_mode == "HTML" else (lambda s: s)
    bold = (
        (lambda s: f"<b>{esc(s)}</b>") if parse_mode == "HTML"
        else (lambda s: s)
    )

    out: list[str] = [
        bold(f"SMC Review Queue — {queue.date}"),
        f"Regime: {queue.market_regime} (confidence={queue.regime_confidence})",
        f"New positions allowed: {'yes' if queue.new_positions_allowed else 'no'}",
        "Execution: disabled",
        "",
        f"READY_FOR_MANUAL_CHART_REVIEW: {counts.get('READY_FOR_MANUAL_CHART_REVIEW', 0)}",
        f"PULLBACK_WATCH: {counts.get('PULLBACK_WATCH', 0)}",
        f"INVALID_RISK_REJECT: {counts.get('INVALID_RISK_REJECT', 0)}",
        f"STRUCTURE_WATCH: {counts.get('STRUCTURE_WATCH', 0)}",
        f"BLOCKED: {counts.get('BLOCKED_BY_REGIME_OR_NEWS', 0)}",
        "",
    ]

    # Prompt 9 requirement: explicitly list tradeable candidates or say
    # none were found. We avoid the phrase "trade signal".
    if tradeable:
        out.append(bold("ICT/SMC tradeable candidates (manual review only):"))
        for i in tradeable[:top]:
            tp = i.trade_plan
            out.append(
                f"{esc(i.symbol)} — candidate for manual review, "
                f"entry {_fmt(tp.get('entry_price'))}, "
                f"stop {_fmt(tp.get('stop_distance_pct'), pct=True)}, "
                f"R/R {_fmt(tp.get('risk_reward_to_target_1'))}"
            )
    else:
        out.append(
            "No ICT/SMC tradeable candidates found. "
            "Research only. No orders placed."
        )
    out.append("")

    if pullback:
        out.append(bold("Pullback watch:"))
        for i in pullback[:top]:
            tp = i.trade_plan
            out.append(
                f"{esc(i.symbol)} — entry {_fmt(tp.get('entry_price'))}, "
                f"current extended "
                f"{_fmt(tp.get('extension_pct_vs_latest_close'), pct=True)}, "
                f"R/R {_fmt(tp.get('risk_reward_to_target_1'))}"
            )
        out.append("")

    if invalid:
        out.append(bold("Invalid risk rejects:"))
        for i in invalid[:top]:
            tp = i.trade_plan
            out.append(
                f"{esc(i.symbol)} — stop "
                f"{_fmt(tp.get('stop_distance_pct'), pct=True)}, "
                f"R/R {_fmt(tp.get('risk_reward_to_target_1'))}"
            )
        out.append("")

    if structure:
        # Names only; digest stays short.
        names = ", ".join(i.symbol for i in structure[:top])
        out.append(bold("Structure watch:") + " " + esc(names))
        out.append("")

    out.append("Research only. No orders placed.")
    return "\n".join(out).rstrip()


__all__ = [
    "REVIEW_CATEGORIES",
    "DEFAULT_THRESHOLDS",
    "WHAT_TO_WATCH_NEXT",
    "ReviewCategory",
    "ReviewItem",
    "ReviewQueue",
    "SummaryNotFoundError",
    "append_markdown",
    "build_review_queue",
    "classify_review_category",
    "format_markdown",
    "format_telegram_digest",
    "human_review_reason",
    "load_latest_summary",
    "review_priority_score",
    "save_review_queue",
]
