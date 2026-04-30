"""Forex paper trade_id, Telegram lifecycle alerts, alert de-duplication."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FOREX_ALERT_DEDUPE_REL = "data/runtime/forex_alert_dedupe.json"
_FOREX_CHART_DIR_REL = "data/reports/forex_trade_charts"


def forex_chart_dir(project_root: Path) -> Path:
    p = Path(project_root).resolve() / _FOREX_CHART_DIR_REL
    p.mkdir(parents=True, exist_ok=True)
    return p


def _dedupe_path(root: Path) -> Path:
    return Path(root).resolve() / _FOREX_ALERT_DEDUPE_REL


def _load_dedupe(root: Path) -> dict[str, set[str]]:
    p = _dedupe_path(root)
    data: dict[str, set[str]] = {"entry": set(), "exit": set()}
    if not p.is_file():
        return data
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return data
    if not isinstance(raw, dict):
        return data
    for k in ("entry", "exit"):
        v = raw.get(k)
        if isinstance(v, list):
            data[k] = {str(x) for x in v if x}
    return data


def _save_dedupe(root: Path, data: dict[str, set[str]]) -> None:
    p = _dedupe_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = {k: sorted(v) for k, v in data.items()}
    p.write_text(json.dumps(out, indent=0) + "\n", encoding="utf-8")


def try_mark_alert_sent(project_root: Path, *, kind: str, trade_id: str) -> bool:
    """Return True exactly once per (kind, trade_id) — persists to disk."""

    tid = str(trade_id).strip()
    if not tid:
        return False
    root = Path(project_root).resolve()
    d = _load_dedupe(root)
    bucket = d.setdefault(kind, set())
    if tid in bucket:
        return False
    bucket.add(tid)
    _save_dedupe(root, d)
    return True


def already_sent(project_root: Path, *, kind: str, trade_id: str) -> bool:
    tid = str(trade_id).strip()
    if not tid:
        return False
    d = _load_dedupe(Path(project_root).resolve())
    return tid in d.get(kind, set())


def format_entry_telegram(
    *,
    trade_id: str,
    pair: str,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    units: float,
    order_ids: list[Any],
) -> str:
    oids = [x for x in order_ids if x is not None]
    return (
        "<pre>[Forex ICT 1m] ENTRY (paper)</pre>\n"
        f"<b>trade_id</b>: <code>{trade_id}</code>\n"
        f"<b>pair</b>: {pair} · <b>dir</b>: {direction}\n"
        f"entry={entry:g} · stop={stop:g} · target={target:g} · units={units:g}\n"
        f"order_ids={oids[:12]}"
    )


def format_exit_telegram(
    *,
    trade_id: str,
    pair: str,
    direction: str,
    entry_fill: float | None,
    exit_fill: float | None,
    pip_move: float | None,
    r_multiple: float | None,
    pnl_label: str,
) -> str:
    pips_s = f"{pip_move:g}" if pip_move is not None else "unavailable"
    r_s = f"{r_multiple:g}" if r_multiple is not None else "unavailable"
    ef = f"{entry_fill:g}" if entry_fill is not None else "unavailable"
    xf = f"{exit_fill:g}" if exit_fill is not None else "unavailable"
    return (
        "<pre>[Forex ICT 1m] EXIT (paper)</pre>\n"
        f"<b>trade_id</b>: <code>{trade_id}</code>\n"
        f"<b>pair</b>: {pair} · <b>dir</b>: {direction}\n"
        f"entry_fill={ef} · exit_fill={xf}\n"
        f"<b>pips</b>: {pips_s} · <b>R</b>: {r_s}\n"
        f"<b>P/L USD</b>: {pnl_label}"
    )


__all__ = [
    "forex_chart_dir",
    "try_mark_alert_sent",
    "already_sent",
    "format_entry_telegram",
    "format_exit_telegram",
]
