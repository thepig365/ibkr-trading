"""Auto-generate Journal trade-review PNGs from local 1m caches only.

No broker, no web: reads ``data/paper_orders/*-intraday-paper-orders.jsonl`` +
``data/candles/<SYM>/1min/*.csv`` only.

Chart availability for UI:
  ``available`` | ``missing_candles`` | ``not_applicable`` | ``pending`` | ``error``
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .journal_trade_lookup import find_paper_order_payload_by_trade_id, iter_intraday_paper_order_jsonl_files
from .journal_trade_id import compute_stable_trade_row_id
from .trade_journal_chart import (
    candles_available_for_journal_row,
    generate_trade_journal_chart_png,
    trade_anchor_utc,
    trade_review_chart_png_path,
)

logger = logging.getLogger(__name__)

ChartAvailCode = Literal[
    "available",
    "missing_candles",
    "not_applicable",
    "pending",
    "ready_to_draw",
]


@dataclass(frozen=True)
class EnsureTradeChartResult:
    status: Literal[
        "available",
        "generated",
        "already_exists",
        "missing_candles",
        "not_applicable",
        "pending",
        "error",
    ]
    trade_id: str
    chart_path: str | None = None
    detail: str = ""

    def outcome_status_for_batch(self) -> str:
        if self.status == "generated":
            return "generated"
        if self.status in {"available", "already_exists"}:
            return "available"
        if self.status == "missing_candles":
            return "missing_candles"
        if self.status == "error":
            return "error"
        return "skipped"


AUTO_ENSURE_ON_JOURNAL_PAGE_LIMIT = 20
TRADE_CHART_BATCH_RUNTIME_RELPATH = "data/runtime/trade_chart_batch_last.json"


def _payload_exit_hint(payload: dict[str, Any]) -> bool:
    if payload.get("exit_price") is not None:
        return True
    ts = payload.get("exit_time") or payload.get("exit_ts")
    return bool(ts)


def classify_paper_trade_for_auto_chart(payload: dict[str, Any]) -> ChartAvailCode:
    """Rough lifecycle; local JSON only — no fills feed required."""

    skipped = payload.get("skipped_reasons") or []
    has_skip = isinstance(skipped, list) and any(str(s).strip() for s in skipped)
    if has_skip:
        return "not_applicable"

    sub = bool(payload.get("submitted")) or bool(payload.get("submitted_to_broker"))
    if not sub:
        return "not_applicable"

    bi = str(payload.get("bracket_integrity") or "").strip().lower()
    if bi == "incomplete":
        if _payload_exit_hint(payload):
            return "ready_to_draw"
        return "pending"

    return "ready_to_draw"


@dataclass(frozen=True)
class JournalChartCell:
    tier: Literal[
        "available",
        "ready_to_draw",
        "missing_candles",
        "pending",
        "not_applicable",
    ]


def ensure_trade_chart_if_possible(
    project_root: Path,
    trade_id: str,
    *,
    force: bool = False,
) -> EnsureTradeChartResult:
    tid = (trade_id or "").strip().lower()
    root = Path(project_root).resolve()
    outp = trade_review_chart_png_path(root, tid)
    if outp.is_file() and not force:
        return EnsureTradeChartResult(
            status="already_exists",
            trade_id=tid,
            chart_path=str(outp),
            detail="PNG already on disk.",
        )

    payload = find_paper_order_payload_by_trade_id(root, tid)
    if payload is None:
        return EnsureTradeChartResult(status="error", trade_id=tid, detail="Trade not found.")

    cls = classify_paper_trade_for_auto_chart(payload)
    if cls == "not_applicable":
        return EnsureTradeChartResult(status="not_applicable", trade_id=tid)
    if cls == "pending":
        return EnsureTradeChartResult(status="pending", trade_id=tid)

    res = generate_trade_journal_chart_png(root, tid, force=force)
    if res.ok and res.png_path is not None:
        low_msg = res.message.lower()
        st_out: Literal["generated", "already_exists"]
        if "already exists" in low_msg:
            st_out = "already_exists"
        else:
            st_out = "generated"
        return EnsureTradeChartResult(
            status=st_out,
            trade_id=tid,
            chart_path=str(res.png_path),
            detail=res.message,
        )
    low_m = res.message.lower()
    if "no local" in low_m or "expected file" in low_m:
        return EnsureTradeChartResult(
            status="missing_candles", trade_id=tid, detail=res.message
        )
    return EnsureTradeChartResult(status="error", trade_id=tid, detail=res.message)


def _iter_recent_trade_payloads(project_root: Path) -> list[tuple[str, dict[str, Any]]]:
    root = Path(project_root).resolve()
    pod = root / "data" / "paper_orders"
    if not pod.is_dir():
        return []
    out: list[tuple[str, dict[str, Any], str]] = []
    for path in iter_intraday_paper_order_jsonl_files(pod):
        try:
            sp = str(path.resolve())
            with path.open(encoding="utf-8") as f:
                for line_no, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    tid = compute_stable_trade_row_id(sp, line_no, obj).lower()
                    ts = str(obj.get("timestamp") or "")
                    out.append((tid, dict(obj), ts))
        except OSError:
            continue
    out.sort(key=lambda x: x[2], reverse=True)
    return [(tid, obj) for tid, obj, _ts in out]


def ny_date_iso_for_trade_dict(obj: dict[str, Any]) -> str | None:
    anch = trade_anchor_utc(obj)
    if anch is None:
        return None
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    return anch.astimezone(ny).date().isoformat()


def generate_trade_charts_batch(
    project_root: Path,
    *,
    mode_latest: bool = False,
    report_date=None,
    limit: int = 50,
) -> dict[str, Any]:
    """Generate missing PNGs for recent paper trades; local-only."""

    root = Path(project_root).resolve()
    want_day: str | None = None
    if report_date is not None:
        want_day = str(report_date).strip()[:10]

    rows = _iter_recent_trade_payloads(root)
    if mode_latest:
        cand = rows[: max(1, limit)]
    else:
        cand = rows
        if want_day:
            cand = [(t, o) for t, o in cand if ny_date_iso_for_trade_dict(o) == want_day]
        cand = cand[: max(1, limit)]

    generated_count = 0
    available_after = 0
    existed_before = 0
    missing_candles_count = error_count = 0
    details: list[dict[str, Any]] = []

    for tid, payload in cand:
        life = classify_paper_trade_for_auto_chart(payload)
        if life in {"not_applicable", "pending"}:
            continue
        p_png = trade_review_chart_png_path(root, tid)
        if p_png.is_file():
            existed_before += 1
            available_after += 1
            details.append({"trade_id": tid, "symbol": payload.get("symbol"), "skip": "exists"})
            continue
        er = ensure_trade_chart_if_possible(root, tid, force=False)
        if er.status == "generated":
            generated_count += 1
            available_after += 1
            details.append({"trade_id": tid, "symbol": payload.get("symbol"), "result": "generated"})
        elif er.status == "already_exists":
            existed_before += 1
            available_after += 1
        elif er.status == "missing_candles":
            missing_candles_count += 1
            details.append({"trade_id": tid, "result": "missing_candles"})
        elif er.status == "error":
            error_count += 1
            details.append({"trade_id": tid, "result": "error", "detail": (er.detail or "")[:200]})
        logger.debug(
            "trade-chart batch tid=%s result=%s",
            tid,
            er.status,
        )

    chart_dir = str((root / "data" / "reports" / "trade_charts").resolve())

    summary: dict[str, Any] = {
        "generated_count": generated_count,
        "available_after": available_after,
        "existed_before_count": existed_before,
        "missing_candles_count": missing_candles_count,
        "error_count": error_count,
        "chart_dir": chart_dir,
        "mode_latest": mode_latest,
        "report_date": want_day,
        "limit_applied": len(cand),
        "samples": details[:25],
    }
    rtp = root / TRADE_CHART_BATCH_RUNTIME_RELPATH
    try:
        rtp.parent.mkdir(parents=True, exist_ok=True)
        rtp.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        logger.warning("could not persist trade_chart_batch_last.json", exc_info=True)
    return summary


def journal_chart_cell(project_root: Path, row_like: Any) -> JournalChartCell:
    """Row-first chart column semantics (PNG + sizing cache + lifecycle)."""

    root = Path(project_root).resolve()
    tid = (getattr(row_like, "trade_id", "") or "").strip().lower()

    if not tid:
        return JournalChartCell("not_applicable")

    if trade_review_chart_png_path(root, tid).is_file():
        return JournalChartCell("available")

    skips = getattr(row_like, "skipped_reasons", None) or []
    if skips and len([str(s).strip() for s in skips if s]):
        return JournalChartCell("not_applicable")

    sub = bool(getattr(row_like, "submitted", False)) or bool(
        getattr(row_like, "submitted_to_broker", False)
    )
    if not sub:
        return JournalChartCell("not_applicable")

    bi = str(getattr(row_like, "bracket_integrity", "") or "").strip().lower()
    if bi == "incomplete":
        return JournalChartCell("pending")
    payload = find_paper_order_payload_by_trade_id(root, tid)
    if payload is None:
        return JournalChartCell("not_applicable")
    cls = classify_paper_trade_for_auto_chart(payload)
    if cls == "pending":
        return JournalChartCell("pending")
    if cls == "not_applicable":
        return JournalChartCell("not_applicable")
    # ready_to_draw at payload level — still need candles on disk for PNG
    has_candles = candles_available_for_journal_row(root, row_like)
    if has_candles:
        return JournalChartCell("ready_to_draw")
    return JournalChartCell("missing_candles")


def journal_page_auto_ensure_row_charts(project_root: Path, rows_like: list[Any]) -> int:
    """Attempt generate for newest eligible rows lacking PNG — bounded."""

    root = Path(project_root).resolve()
    n_done = 0
    seen = 0
    for row in rows_like:
        tid = getattr(row, "trade_id", "") or ""
        if not tid:
            continue
        jc = journal_chart_cell(root, row)
        if jc.tier != "ready_to_draw":
            continue
        if trade_review_chart_png_path(root, tid).is_file():
            continue
        if seen >= AUTO_ENSURE_ON_JOURNAL_PAGE_LIMIT:
            break
        seen += 1
        er = ensure_trade_chart_if_possible(root, tid, force=False)
        if er.status in {"generated", "already_exists"}:
            n_done += 1
        elif er.status == "error":
            logger.debug(
                "auto journal chart failed tid=%s detail=%s", tid, er.detail[:120]
            )
    return n_done


def read_last_trade_chart_batch_summary(project_root: Path) -> dict[str, Any] | None:
    p = Path(project_root) / TRADE_CHART_BATCH_RUNTIME_RELPATH
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None
