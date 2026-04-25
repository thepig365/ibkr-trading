"""Manual macro calendar (YAML-driven).

The bot intentionally does **not** scrape an external macro calendar in
v2 — operators curate ``config/macro_calendar.yaml`` and we apply it
deterministically. Most events become ``soft_flag``; ``hard_block`` is
opt-in per row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import yaml

from ..research_intelligence import (
    Action,
    ImpactLevel,
    MacroEvent,
)

if TYPE_CHECKING:
    from ..config import AppConfig

logger = logging.getLogger(__name__)


DEFAULT_MACRO_CATEGORIES: tuple[str, ...] = (
    "CPI",
    "PPI",
    "PCE",
    "NFP",
    "unemployment",
    "jobless_claims",
    "FOMC",
    "Fed_speech",
    "GDP",
    "retail_sales",
    "ISM",
    "bond_auction",
    "oil_OPEC",
    "geopolitical",
)


@dataclass(frozen=True)
class MacroCalendar:
    """In-memory representation of ``config/macro_calendar.yaml``."""

    events: list[MacroEvent] = field(default_factory=list)
    source_path: str | None = None
    notes: list[str] = field(default_factory=list)

    def for_date(self, day: str | date | datetime) -> list[MacroEvent]:
        """Return events scheduled on ``day`` (YYYY-MM-DD or date-like)."""
        target = _to_iso_date(day)
        if not target:
            return []
        return [e for e in self.events if e.date == target]

    def for_today_et(self) -> list[MacroEvent]:
        """Return events scheduled for *today in US/Eastern* time.

        Macro events are quoted in ET in the YAML, so anchor the "today"
        question to ET — not UTC, not local — to avoid off-by-one bugs.
        """
        try:
            from zoneinfo import ZoneInfo  # noqa: PLC0415

            today = datetime.now(ZoneInfo("America/New_York")).date()
        except Exception:  # noqa: BLE001
            today = datetime.now(timezone.utc).date()
        return self.for_date(today)


def load_macro_calendar(
    cfg: "AppConfig | None" = None,
    *,
    path: Path | None = None,
) -> MacroCalendar:
    """Load events from YAML.

    Returns an empty :class:`MacroCalendar` (with a note) when the file
    is missing — never raises so the research report can still proceed.
    """
    if path is None:
        if cfg is None:
            raise ValueError("either cfg or path must be provided")
        path = cfg.absolute("config/macro_calendar.yaml")

    notes: list[str] = []
    if not path.exists():
        notes.append(f"macro_calendar.yaml not found at {path}; using empty calendar.")
        return MacroCalendar(events=[], source_path=str(path), notes=notes)

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        notes.append(f"failed to parse macro_calendar.yaml ({exc!r}); using empty calendar.")
        return MacroCalendar(events=[], source_path=str(path), notes=notes)

    if not isinstance(raw, dict):
        notes.append("macro_calendar.yaml root must be a mapping; using empty calendar.")
        return MacroCalendar(events=[], source_path=str(path), notes=notes)

    rows = raw.get("events") or []
    if not isinstance(rows, list):
        notes.append("`events:` must be a list; using empty calendar.")
        return MacroCalendar(events=[], source_path=str(path), notes=notes)

    events: list[MacroEvent] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            notes.append(f"event #{i} is not a mapping; skipped")
            continue
        d = _to_iso_date(row.get("date"))
        if not d:
            notes.append(f"event #{i} missing/invalid `date`; skipped")
            continue
        time_et = str(row.get("time_et") or "").strip()
        category = str(row.get("category") or "").strip() or "geopolitical"
        impact_raw = str(row.get("impact_level") or "medium").strip().lower()
        impact_level: ImpactLevel = (
            "high" if impact_raw == "high" else "low" if impact_raw == "low" else "medium"
        )
        handling_raw = str(row.get("handling") or "soft_flag").strip().lower()
        handling: Action = _coerce_action(handling_raw)
        events.append(
            MacroEvent(
                date=d,
                time_et=time_et,
                event=str(row.get("event") or "").strip(),
                category=category,
                impact_level=impact_level,
                handling=handling,
                notes=str(row.get("notes") or "").strip(),
            )
        )

    return MacroCalendar(events=events, source_path=str(path), notes=notes)


def render_calendar_zh(
    events: Iterable[MacroEvent],
    *,
    target_label: str,
) -> str:
    """Pretty-print a list of macro events in Chinese for CLI output."""
    rows = list(events)
    lines: list[str] = []
    lines.append(f"📅 宏观日历 — {target_label}")
    if not rows:
        lines.append("- 暂无安排（或 macro_calendar.yaml 为空 / 缺失）。")
        return "\n".join(lines)
    for ev in rows:
        when = f"{ev.time_et} ET" if ev.time_et else "全天"
        lines.append(
            f"- {when} · {ev.event} ({ev.category}) "
            f"[影响: {ev.impact_level} · 处理: {ev.handling}]"
        )
        if ev.notes:
            lines.append(f"    备注: {ev.notes}")
    return "\n".join(lines)


def _coerce_action(raw: str) -> Action:
    valid: dict[str, Action] = {
        "add_to_watchlist": "add_to_watchlist",
        "boost_priority": "boost_priority",
        "manual_review": "manual_review",
        "soft_flag": "soft_flag",
        "hard_block": "hard_block",
        "ignore": "ignore",
    }
    return valid.get(raw, "soft_flag")


def _to_iso_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
    except ValueError:
        try:
            return datetime.strptime(s, "%Y/%m/%d").date().isoformat()
        except ValueError:
            return ""


__all__ = [
    "DEFAULT_MACRO_CATEGORIES",
    "MacroCalendar",
    "load_macro_calendar",
    "render_calendar_zh",
]
