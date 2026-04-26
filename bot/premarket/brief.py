"""Assemble pre-market brief from manual macro + optional news providers."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot.config import AppConfig
from bot.news.providers.base import NewsHeadline, ProviderCallResult
from bot.news.providers.registry import all_providers, dedupe_headlines
from bot.research_providers.manual_macro_calendar import load_macro_calendar
from bot.reports.report_email import send_report_email
from bot.reports.report_email_status import record_email_outcome

from .storage import brief_paths_for_day, premarket_briefs_dir

NY = ZoneInfo("America/New_York")


def _watchlist_symbols(root: Path) -> list[str]:
    d = root / "data" / "watchlists"
    if not d.is_dir():
        return []
    files = sorted(d.glob("*.json"))
    if not files:
        return []
    best = files[-1]
    try:
        data = json.loads(best.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[str] = []
    for item in data.get("symbols") or []:
        if isinstance(item, str) and item.strip():
            out.append(item.strip().upper())
        elif isinstance(item, dict) and item.get("symbol"):
            s = str(item.get("symbol") or "").upper().strip()
            if s:
                out.append(s)
    return out[:50]


@dataclass
class PremarketBriefData:
    date_ny: str
    generated_at_utc: str
    market_tone: str
    summary_lines: list[str] = field(default_factory=list)
    macro_events: list[dict[str, Any]] = field(default_factory=list)
    headlines: list[dict[str, Any]] = field(default_factory=list)
    provider_status: dict[str, str] = field(default_factory=dict)
    watchlist_symbols: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    symbols_to_watch: list[str] = field(default_factory=list)
    reminder: str = (
        "Trades still require a valid ICT/SMC setup and a 1-minute trigger, "
        "plus all paper safety gates. News and edge scores do not place orders."
    )


def _macro_rows_for_day(cfg: AppConfig, day: date) -> list[dict[str, Any]]:
    cal = load_macro_calendar(cfg)
    out: list[dict[str, Any]] = []
    for e in cal.for_date(day.isoformat()):
        il = getattr(e, "impact_level", None)
        impact = str(il.value if il is not None and hasattr(il, "value") else il or "")
        out.append(
            {
                "date": str(getattr(e, "date", str(day))),
                "time_et": str(getattr(e, "time_et", "") or ""),
                "title": str(getattr(e, "event", "") or "event"),
                "category": str(getattr(e, "category", "") or ""),
                "impact": impact,
                "notes": str(getattr(e, "notes", "") or ""),
            }
        )
    return out


def _collect_headlines(symbols: list[str]) -> tuple[list[NewsHeadline], list[ProviderCallResult]]:
    all_results: list[ProviderCallResult] = []
    raw: list[NewsHeadline] = []
    for p in all_providers():
        r0 = p.fetch_market_news()
        all_results.append(r0)
        raw.extend(r0.items)
        r1 = p.fetch_symbol_news(symbols)
        all_results.append(r1)
        raw.extend(r1.items)
        r2 = p.fetch_earnings_calendar(symbols)
        all_results.append(r2)
        raw.extend(r2.items)
    return dedupe_headlines(raw), all_results


def _risk_from_macro(macro: list[dict[str, Any]], headlines: list[NewsHeadline]) -> list[str]:
    flags: list[str] = []
    for m in macro:
        cat = (m.get("category") or "").upper()
        imp = (m.get("impact") or "").lower()
        if any(x in cat for x in ("CPI", "FOMC", "NFP", "FED")) or "high" in imp:
            flags.append("High-impact macro on today’s calendar (check release times in ET).")
            break
    htxt = " ".join(h.title.lower() for h in headlines[:20])
    if "earnings" in htxt and ("beat" in htxt or "miss" in htxt):
        flags.append("Earnings-related headlines in the feed — be selective before open.")
    if not flags and len(headlines) > 25:
        flags.append("Unusually high headline volume — wait for your signal rules.")
    return flags[:5]


def build_premarket_brief(
    cfg: AppConfig,
    *,
    trading_day: date | None = None,
    email: bool = False,
    email_to: str = "",
) -> PremarketBriefData:
    root = cfg.project_root
    today_ny = datetime.now(NY).date() if trading_day is None else trading_day
    syms = _watchlist_symbols(root)
    macro = _macro_rows_for_day(cfg, today_ny)
    headlines, prov_results = _collect_headlines(syms)
    status: dict[str, str] = {}
    for r in prov_results:
        # last status wins per name (same provider may return multiple blocks)
        status[r.name] = r.status

    tone = "Mixed / data-driven"
    if any("FOMC" in str(m.get("title", "")) for m in macro):
        tone = "Headline-sensitive (FOMC / Fed on calendar)"
    elif any("CPI" in str(m.get("title", "")) for m in macro) or "cpi" in " ".join(
        h.title.lower() for h in headlines[:5]
    ):
        tone = "CPI / inflation watch"

    sm_lines = [
        f"US session date (New York): {today_ny.isoformat()}",
        f"Watchlist size for context: {len(syms)} symbols" if syms else "No watchlist JSON found yet (optional).",
    ]
    if macro:
        sm_lines.append(
            f"Macro items today (manual calendar): {len(macro)} — see list below."
        )
    rflags = _risk_from_macro(macro, headlines)
    for rf in rflags:
        sm_lines.append(f"Risk: {rf}")

    data = PremarketBriefData(
        date_ny=today_ny.isoformat(),
        generated_at_utc=datetime.now(tz=ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        market_tone=tone,
        summary_lines=sm_lines,
        macro_events=macro,
        headlines=[_headline_to_json(h) for h in headlines[:40]],
        provider_status=status,
        watchlist_symbols=syms,
        risk_flags=rflags,
        symbols_to_watch=syms[:8],
    )

    premarket_briefs_dir(root)
    jp, mp = brief_paths_for_day(root, today_ny)
    raw_obj = {**asdict(data), "provider_blocks": [asdict_r(r) for r in prov_results]}
    jp.write_text(json.dumps(raw_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mp.write_text(_render_markdown(data), encoding="utf-8")

    if email:
        to = (email_to or cfg.settings.reports.email_to or "ileonzh@gmail.com").strip()
        subj = f"Strategy Lab Pre-Market Brief — {today_ny.isoformat()}"
        body = _email_body(data)
        out = send_report_email(
            to_cfg=to, subject=subj, text_body=body, project_root=root
        )
        record_email_outcome(
            root,
            "premarket_brief",
            status=out.status,
            to_addr=to,
            report_key=str(jp),
            detail=(out.detail or "")[:500],
        )

    return data


def asdict_r(r: ProviderCallResult) -> dict[str, Any]:
    return {
        "name": r.name,
        "status": r.status,
        "detail": r.detail,
        "count": len(r.items),
    }


def _headline_to_json(h: NewsHeadline) -> dict[str, Any]:
    return {
        "title": h.title,
        "url": h.url,
        "symbol": h.symbol,
        "source": h.source,
        "tags": h.tags,
    }


def _render_markdown(d: PremarketBriefData) -> str:
    lines = [
        f"# Pre-Market Brief — {d.date_ny}",
        "",
        f"**Market tone:** {d.market_tone}",
        "",
        "## Summary",
    ]
    for s in d.summary_lines:
        lines.append(f"- {s}")
    lines += ["", "## Reminder", "", d.reminder, "", "## Macro (manual calendar)"]
    for m in d.macro_events:
        te = m.get("time_et") or ""
        tpart = f" {te} ET" if te else ""
        lines.append(
            f"- {m.get('date')}{tpart} **{m.get('title', '')}** — {m.get('category', '')} ({m.get('impact', '')})"
        )
    if not d.macro_events:
        lines.append("- (none for this date, or `config/macro_calendar.yaml` empty)")
    lines += ["", "## Headlines (aggregated)"]
    for h in d.headlines[:20]:
        sym = f" [{h.get('symbol')}]" if h.get("symbol") else ""
        lines.append(f"- {h.get('title', '')}{sym} — *{h.get('source', '')}*")
    lines += ["", "## Provider status", ""]
    for k, v in sorted(d.provider_status.items()):
        lines.append(f"- {k}: {v}")
    lines += [
        "",
        "## Symbols to watch",
        ", ".join(d.symbols_to_watch) if d.symbols_to_watch else "—",
    ]
    return "\n".join(lines) + "\n"


def _email_body(d: PremarketBriefData) -> str:
    parts = [
        f"Date (NY): {d.date_ny}",
        f"Market tone: {d.market_tone}",
        "",
        "Top headlines:",
    ]
    for h in d.headlines[:5]:
        parts.append(f" - {h.get('title', '')}")
    parts += [
        "",
        "Macro (manual) events:",
    ]
    for m in d.macro_events[:6]:
        parts.append(
            f" - {m.get('date', '')} {m.get('title', '')} [{m.get('category', '')}]"
        )
    if d.risk_flags:
        parts += ["", "Risk flags:", *(f" - {x}" for x in d.risk_flags)]
    parts += [
        "",
        f"Watchlist (context): {', '.join(d.symbols_to_watch) if d.symbols_to_watch else 'n/a'}",
        "",
        d.reminder,
    ]
    return "\n".join(parts)
