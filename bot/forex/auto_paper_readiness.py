"""Structured readiness for Forex auto paper (mostly file + optional IBKR probe)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import AppConfig, load_config
from bot.execution.intraday_paper_execution import is_kill_switch_active

from .config import load_forex_ict_config, melbourne_session_open
from .daily_notional import load_notional_day
from .runtime_auto import read_runtime_auto_enabled
from .yaml_utils import effective_session_dict, pairs_list_from_forex_yaml


def build_forex_auto_paper_readiness(
    project_root: Path | str,
    cfg: AppConfig | None = None,
    *,
    probe_ibkr: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    cfg = cfg or load_config(project_root=root)
    fx = load_forex_ict_config(root)
    ap = fx.get("auto_paper") if isinstance(fx.get("auto_paper"), dict) else {}
    ex = fx.get("execution") if isinstance(fx.get("execution"), dict) else {}
    rk = fx.get("risk") if isinstance(fx.get("risk"), dict) else {}

    fx_eff = {**fx, "session": effective_session_dict(fx)}
    tz_name = str((fx_eff.get("session") or {}).get("timezone") or "Australia/Melbourne")
    pairs = pairs_list_from_forex_yaml(fx)
    sess_ok, lh = melbourne_session_open(fx_eff, now_tz=datetime.now(timezone.utc))
    kill = is_kill_switch_active(cfg)
    rt_auto = read_runtime_auto_enabled(root)
    ap_ok = bool(ap.get("enabled", False))
    sub_ok = bool(ex.get("submit_to_broker", False))
    paper_ok = cfg.settings.account.mode == "paper" and bool(
        getattr(cfg.settings.account, "block_live_trading", True)
    )
    no_mkt = bool(ex.get("no_market_orders", True))
    bracket = bool(ex.get("bracket_required", True))

    mx_d = float(rk.get("max_daily_notional_usd") or 100_000)
    mx_o = float(rk.get("max_notional_per_trade_usd") or 10_000)

    nt = load_notional_day(root, timezone_name=tz_name)

    ibkr_connected: bool | None = None if not probe_ibkr else False
    if probe_ibkr:
        from bot.ibkr_connection import connect_readonly_roster_retry

        out = connect_readonly_roster_retry(cfg, "broker_readonly")
        if out.client:
            ibkr_connected = True
            try:
                out.client.get_account_summary()
            except Exception:
                ibkr_connected = False
            try:
                out.client.disconnect()
            except Exception:
                pass
        else:
            ibkr_connected = False

    blockers: list[str] = []
    if not paper_ok:
        blockers.append("account_not_paper_or_live_block_missing")
    if kill:
        blockers.append("kill_switch")
    if not ap_ok:
        blockers.append("auto_paper.enabled_false_yaml")
    if not sub_ok:
        blockers.append("execution.submit_to_broker_false")
    if not rt_auto:
        blockers.append("runtime_forex_auto_paper_enabled_false")
    if not pairs:
        blockers.append("no_pairs")
    if not sess_ok:
        blockers.append("outside_melbourne_session")

    return {
        "ok": len(blockers) == 0,
        "paper_account": paper_ok,
        "kill_switch": kill,
        "auto_paper_enabled_yaml": ap_ok,
        "runtime_auto_enabled": rt_auto,
        "submit_to_broker_yaml": sub_ok,
        "no_market_orders_config": no_mkt,
        "bracket_required_config": bracket,
        "pairs": pairs,
        "melbourne_session_open": sess_ok,
        "melbourne_local_time": lh,
        "melbourne_tz": tz_name,
        "max_daily_notional_usd": mx_d,
        "max_notional_per_trade_usd": mx_o,
        "daily_notional_used_usd": float(nt.get("total_usd") or 0),
        "daily_notional_remaining_usd": max(0.0, mx_d - float(nt.get("total_usd") or 0)),
        "session_status": ("open" if sess_ok else "closed"),
        "ibkr_connected": ibkr_connected,
        "probe_ibkr_requested": probe_ibkr,
        "blockers": blockers,
        "risk_snapshot": rk,
        "strategy_id": fx.get("strategy_id"),
    }


__all__ = ["build_forex_auto_paper_readiness"]
