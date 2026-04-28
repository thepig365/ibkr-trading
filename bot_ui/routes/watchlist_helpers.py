"""Watchlist UI helpers — display only; no broker imports."""

from __future__ import annotations

import re

from ..services.command_queue import CommandResult

_ANSI_ESC = re.compile(r"\x1b\[[0-9;]*m")
_RICH_TAG = re.compile(r"\[[/a-zA-Z0-9=#. ]+\]")


def recent_command_hints_for_watchlist(results: list[CommandResult]) -> list[str]:
    """One-line hints below each Recent commands row for this page."""
    return [_summarize_watchlist_recent(r) for r in results]


def _strip_rich(s: str) -> str:
    s = _ANSI_ESC.sub("", s)
    s = _RICH_TAG.sub("", s)
    return " ".join(s.split()).strip()


def _summarize_watchlist_recent(r: CommandResult) -> str:
    if r.request.command != "build-watchlist" or not r.accepted:
        return ""
    txt = _ANSI_ESC.sub("", (r.stdout or "") + "\n" + (r.stderr or ""))
    if "IBKR connect failed" in txt or "LiveTradingBlocked" in txt:
        return (
            "Rebuild: IBKR did not connect — no daily bars; Latest price / Rel vol "
            "stay empty until TWS is up and `build-watchlist --ibkr` succeeds."
        )
    has_ibkr = "--ibkr" in r.request.args
    price_note = (
        "Prices in saved JSON: expected from IBKR daily bars (read-only)."
        if has_ibkr
        else "Prices in saved JSON: not filled — this run had no `--ibkr` (offline core only)."
    )
    saved = ""
    for line in txt.splitlines():
        line = line.strip()
        if "Saved" in line and "dynamic-watchlist" in line:
            saved = _strip_rich(line)[:360]
            break
    if saved:
        return f"{price_note} · {saved}"
    return price_note
