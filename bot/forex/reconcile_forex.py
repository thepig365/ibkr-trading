"""Reconcile Forex bracket fills vs IBKR executions — read-only fills(); exit Telegram once."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.config import AppConfig
from bot.ibkr_connection import connect_readonly_roster_retry

from .orders_log import append_forex_order_event, forex_orders_path
from .pairs import parse_pair, pip_size_for_pair
from .telegram_fx import send_fx_telegram
from .trade_lifecycle import format_exit_telegram, try_mark_alert_sent


def _all_jsonl_rows(project_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    od = project_root / "data" / "forex_orders"
    if not od.is_dir():
        return rows
    for fp in sorted(od.glob("*-forex-paper-orders.jsonl")):
        try:
            tx = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        for ln in tx.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                row = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _submitted_by_trade_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    last: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("phase") != "submitted":
            continue
        tid = row.get("trade_id")
        oids = row.get("order_ids")
        if isinstance(tid, str) and isinstance(oids, list) and len(oids) >= 3:
            last[tid] = row
    return last


def _by_order_id(execs: list[Any]) -> dict[int, list[Any]]:
    by: dict[int, list[Any]] = {}
    for e in execs:
        oid = getattr(e, "order_id", None)
        if oid is None:
            continue
        oid_i = int(oid)
        by.setdefault(oid_i, []).append(e)
    return by


def _pip_r(
    *,
    pair_display: str,
    direction: str,
    entry_fill: float,
    exit_fill: float,
    entry_lim: float,
    stop_px: float,
) -> tuple[float | None, float | None]:
    try:
        spec = parse_pair(pair_display)
    except ValueError:
        return None, None
    pip = pip_size_for_pair(spec)
    risk = abs(float(entry_lim) - float(stop_px))
    if risk <= 0:
        risk = pip
    d = direction.lower()
    if d == "long":
        profit = float(exit_fill) - float(entry_fill)
    else:
        profit = float(entry_fill) - float(exit_fill)
    move_pips = profit / pip if pip else None
    r_mult = profit / risk if risk else None
    return move_pips, r_mult


def reconcile_forex_fills(
    project_root: Path | str,
    cfg: AppConfig,
    *,
    journal: Any = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()

    rows = _all_jsonl_rows(root)
    submitted = _submitted_by_trade_id(rows)

    oc = connect_readonly_roster_retry(cfg, "broker_readonly")
    if oc.client is None:
        return {
            "ok": False,
            "error": str(oc.fatal_message or "broker_readonly_connect_failed"),
            "trades_tracked": len(submitted),
            "exit_alerts_sent": [],
        }
    cli = oc.client

    try:
        execs = cli.get_executions()
    finally:
        try:
            cli.disconnect()
        except Exception:
            pass

    bx = _by_order_id(execs)
    exits_found: list[str] = []

    for tid, row in submitted.items():
        oids_raw = row.get("order_ids") or []
        oids = [int(x) for x in oids_raw[:3] if x is not None]
        if len(oids) < 3:
            continue
        parent_id, tp_id, sl_id = oids[0], oids[1], oids[2]
        entry_fills = bx.get(parent_id, [])
        exit_fills = [*bx.get(tp_id, []), *bx.get(sl_id, [])]
        if not entry_fills or not exit_fills:
            continue
        if not try_mark_alert_sent(root, kind="exit", trade_id=tid):
            continue

        pair = str(row.get("pair") or "")
        direction = str(row.get("direction") or "long")
        ep = float(getattr(entry_fills[0], "price", 0) or 0)
        xp = float(getattr(exit_fills[0], "price", 0) or 0)
        entry_limit = float(row.get("entry") or ep)
        stop_px = float(row.get("stop") or 0)
        move_pips, r_mult = _pip_r(
            pair_display=pair,
            direction=direction,
            entry_fill=ep,
            exit_fill=xp,
            entry_lim=entry_limit,
            stop_px=stop_px,
        )

        pnl_label = "unavailable"
        append_forex_order_event(
            root,
            {
                "phase": "exit_reconciled",
                "trade_id": tid,
                "pair": pair,
                "entry_fill": ep,
                "exit_fill": xp,
                "pips": move_pips,
                "r_multiple": r_mult,
                "pnl_usd": pnl_label,
            },
        )
        send_fx_telegram(
            project_root=root,
            cfg=cfg,
            journal=journal,
            throttle_key=None,
            body=format_exit_telegram(
                trade_id=tid,
                pair=pair,
                direction=direction,
                entry_fill=ep,
                exit_fill=xp,
                pip_move=move_pips,
                r_multiple=r_mult,
                pnl_label=pnl_label,
            ),
        )
        exits_found.append(tid)

    return {
        "ok": True,
        "forex_orders_file": str(forex_orders_path(root)),
        "trades_tracked": len(submitted),
        "exit_alerts_sent": exits_found,
    }


__all__ = ["reconcile_forex_fills"]
