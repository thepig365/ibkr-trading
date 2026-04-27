"""Lightweight automatic-paper-engine gates (file + optional IBKR probe).

This module must **not** import :mod:`bot.broker`, :mod:`bot.auto_paper_intraday_loop`,
or the full :mod:`bot.execution.intraday_paper_execution` tree, so Strategy Lab
pages can render without loading ``ib_async``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import AppConfig
from .execution.intraday_paper_sizing import ledger_snapshot_for_status
from .journal import Journal
from .strategy_ui import load_strategy_selection, load_strategy_ui_catalog

# Duplicated from intraday_paper_execution to avoid importing that module (broker chain).
INTRADAY_LOOP_STATE_RELPATH = "data/runtime/intraday_auto_paper_loop_state.json"

REF_MAX_NOTIONAL_PER_ORDER_USD = 10_000.0
REF_MAX_DAILY_NOTIONAL_USD = 100_000.0
_CAP_EPS = 0.01


def _is_kill_switch_active(cfg: AppConfig) -> bool:
    p = Path(cfg.absolute("data/KILL_SWITCH"))
    return p.is_file()


def _is_intraday_paper_runtime_enabled(cfg: AppConfig) -> tuple[bool, bool]:
    p = Path(cfg.absolute("data/runtime/intraday_auto_paper_enabled"))
    if not p.is_file():
        return False, False
    try:
        content = p.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return False, False
    if content in {"0", "off", "false", "no"}:
        return False, True
    if content in {"1", "on", "true", "yes", ""}:
        return True, False
    return False, False


def _caps_match_reference(ip: Any) -> bool:
    try:
        o = float(ip.max_notional_per_order_usd)
        d = float(ip.max_daily_notional_usd)
    except (TypeError, ValueError):
        return False
    return (
        abs(o - REF_MAX_NOTIONAL_PER_ORDER_USD) < _CAP_EPS
        and abs(d - REF_MAX_DAILY_NOTIONAL_USD) < _CAP_EPS
    )


def _is_stub_strategy_entry(entry: Any) -> bool:
    st = str(getattr(entry, "status", "") or "").lower()
    return st == "stub" or "stub" in st


def build_automatic_paper_engine_preflight(
    cfg: AppConfig,
    journal: Journal | None,
    *,
    probe_ibkr: bool = False,
) -> dict[str, Any]:
    root = Path(cfg.project_root).resolve()
    blockers: list[str] = []
    ip = cfg.settings.trading.intraday_paper

    cat = load_strategy_ui_catalog(root)
    sel = load_strategy_selection(root, catalog=cat)
    active = sel.active_paper_strategy
    entry = cat.strategies.get(active)

    if active != "ict_smc_intraday_v1":
        blockers.append(f"active_paper_strategy must be ict_smc_intraday_v1 (got {active!r})")
    if not entry or not entry.paper_enabled:
        blockers.append("ICT/SMC intraday must be paper-enabled in strategy_ui catalog")
    if entry and _is_stub_strategy_entry(entry):
        blockers.append("active strategy is marked stub — automatic engine refused")

    if cfg.settings.account.mode != "paper":
        blockers.append("account.mode must be paper")
    if not bool(cfg.settings.account.block_live_trading):
        blockers.append("account.block_live_trading must be true")
    if _is_kill_switch_active(cfg):
        blockers.append("kill switch file present (data/KILL_SWITCH)")

    if not bool(ip.enabled):
        blockers.append("trading.intraday_paper.enabled must be true")

    safe_cfg = (
        bool(ip.paper_only)
        and ip.live_trading_allowed is False
        and ip.market_orders_allowed is False
        and bool(ip.bracket_required and ip.stop_required and ip.target_required)
    )
    if not safe_cfg:
        blockers.append(
            "intraday_paper invariants failed (paper_only, no live, no market, bracket/stop/target)"
        )

    if not _caps_match_reference(ip):
        blockers.append(
            f"caps must be ${REF_MAX_NOTIONAL_PER_ORDER_USD:,.0f} per order and "
            f"${REF_MAX_DAILY_NOTIONAL_USD:,.0f} daily (see config trading.intraday_paper)"
        )

    ledger: dict[str, Any] = {}
    try:
        ledger = ledger_snapshot_for_status(cfg, ip)
    except (OSError, TypeError, ValueError):
        ledger = {}
    rem: float | None = None
    try:
        rem = float(ledger.get("daily_remaining_notional_usd", 0))
    except (TypeError, ValueError):
        rem = None
    if rem is not None and rem <= 0:
        blockers.append("daily_remaining_notional_usd is 0 — no new paper risk today")

    loop_st: dict[str, Any] = {}
    try:
        p = Path(cfg.absolute(INTRADAY_LOOP_STATE_RELPATH))
        if p.is_file():
            loop_st = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        loop_st = {}
    recon_loop = str(loop_st.get("reconciliation_status") or "")
    if bool(ip.require_reconciliation_pass) and recon_loop:
        rs = recon_loop.lower()
        if "fail" in rs or "error" in rs:
            blockers.append(f"last loop reconciliation_status not clean: {recon_loop!r}")

    recon_passed_probe: bool | None = None
    tws_err: str | None = None
    if probe_ibkr and journal is not None:
        from .paper_activation import build_paper_activation_status  # noqa: PLC0415

        pa = build_paper_activation_status(cfg, probe_ibkr=True, journal=journal)
        recon_passed_probe = bool(pa.get("reconciliation_passed"))
        if pa.get("ibkr_probe_error"):
            tws_err = str(pa.get("ibkr_probe_error"))
            blockers.append(f"IBKR probe: {tws_err}")
        elif bool(ip.require_reconciliation_pass) and not recon_passed_probe:
            blockers.append("reconciliation with broker did not pass (probe_ibkr)")

    runtime_on, explicit_off = _is_intraday_paper_runtime_enabled(cfg)

    ok = len(blockers) == 0
    return {
        "ok": ok,
        "blockers": blockers,
        "active_paper_strategy": active,
        "intraday_paper_dry_run_config": bool(ip.dry_run),
        "runtime_intraday_on": bool(runtime_on),
        "runtime_explicit_off": bool(explicit_off),
        "daily_remaining_notional_usd": rem,
        "max_notional_per_order_usd": float(ip.max_notional_per_order_usd),
        "max_daily_notional_usd": float(ip.max_daily_notional_usd),
        "ledger_snapshot": ledger,
        "reconciliation_loop_state": recon_loop or None,
        "reconciliation_passed_probe": recon_passed_probe,
        "telegram_configured": bool(
            getattr(cfg, "telegram", None) and cfg.telegram.is_configured
        ),
    }


__all__ = [
    "REF_MAX_DAILY_NOTIONAL_USD",
    "REF_MAX_NOTIONAL_PER_ORDER_USD",
    "build_automatic_paper_engine_preflight",
    "INTRADAY_LOOP_STATE_RELPATH",
]
