"""Forex ICT 1m auto paper supervisor — single iteration."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import AppConfig, load_config
from bot.execution.intraday_paper_execution import is_kill_switch_active
from bot.journal import Journal

from . import FOREX_ORDER_REF_PREFIX_DEFAULT
from .auto_paper_readiness import build_forex_auto_paper_readiness
from .config import load_forex_ict_config, melbourne_session_open
from .daily_notional import can_add_notional, load_notional_day, record_notional_trade
from .fetch_bridge import fetch_forex_1m_duration
from .orders_log import append_forex_order_event
from .pairs import parse_pair, pip_size_for_pair
from .preflight import validate_bracket
from .runner import _account_equity_usd  # noqa: PLC0415
from .runtime_auto import read_runtime_auto_enabled
from .signals import simple_fx_ict_scan
from .sizing import (
    estimate_notional_usd_approx,
    estimate_units_for_risk,
    shrink_units_for_notional_caps,
)
from .telegram_fx import send_fx_telegram
from .yaml_utils import effective_session_dict, pairs_list_from_forex_yaml

LOG = logging.getLogger(__name__)

LOOP_STATE_RELPATH = "data/runtime/forex_auto_paper_loop_state.json"


def _loop_state_path(root: Path) -> Path:
    return root.resolve() / LOOP_STATE_RELPATH


def write_loop_state(root: Path, payload: dict[str, Any]) -> None:
    p = _loop_state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_forex_auto_paper_supervisor(
    project_root: Path | str,
    *,
    dry_run: bool = False,
    cfg: AppConfig | None = None,
    journal: Journal | None = None,
) -> dict[str, Any]:
    """Fetch → ICT scan → optionally submit ONE bracket paper order."""

    root = Path(project_root).resolve()
    cfg = cfg or load_config(project_root=root)
    journal = journal or Journal(cfg)

    fx = load_forex_ict_config(root)
    ap_cfg = fx.get("auto_paper") if isinstance(fx.get("auto_paper"), dict) else {}
    ex_cfg = fx.get("execution") if isinstance(fx.get("execution"), dict) else {}
    rk = fx.get("risk") if isinstance(fx.get("risk"), dict) else {}

    fx_eff = {**fx, "session": effective_session_dict(fx)}
    tz_name = str((fx_eff.get("session") or {}).get("timezone") or "Australia/Melbourne")
    now = datetime.now(timezone.utc)
    sess_open, lh = melbourne_session_open(fx_eff, now_tz=now)

    strategy_id = str(fx.get("strategy_id") or "ict_fx_1m_test")
    order_ref = str(ex_cfg.get("order_ref_prefix") or FOREX_ORDER_REF_PREFIX_DEFAULT)
    tif = str(ex_cfg.get("tif") or "DAY").upper()
    mx_daily = float(rk.get("max_daily_notional_usd") or 100_000)
    mx_trade = float(rk.get("max_notional_per_trade_usd") or 10_000)
    mx_pair_cap = float(rk.get("per_pair_notional_cap_usd") or 30_000)
    rk_pct = float(rk.get("risk_per_trade_pct") or 0.05)
    max_units_r = float(rk.get("max_units_per_trade") or 100_000)
    mx_td = int(ap_cfg.get("max_trades_per_day") or rk.get("max_trades_per_day") or 10)
    mx_tp = int(ap_cfg.get("max_trades_per_pair") or rk.get("max_trades_per_pair") or 3)

    raw_uj = rk.get("usd_jpy_for_conversion")
    inv_uj = (1.0 / float(raw_uj)) if raw_uj else None

    nt = load_notional_day(root, timezone_name=tz_name)

    tg_sum = (
        sum(int(v) for v in (nt.get("trade_count_pairs") or {}).values())
        if isinstance(nt.get("trade_count_pairs"), dict)
        else 0
    )

    base_state: dict[str, Any] = {
        "running": False,
        "last_loop_at": now.isoformat(),
        "session_status": ("open" if sess_open else "closed"),
        "melbourne_local_time": lh,
        "active_pairs": pairs_list_from_forex_yaml(fx),
        "trades_today": tg_sum,
        "daily_notional_used_usd": float(nt.get("total_usd") or 0),
        "daily_notional_remaining_usd": max(
            0.0, mx_daily - float(nt.get("total_usd") or 0)
        ),
        "last_signal": None,
        "last_order_status": None,
        "last_reject_reason": None,
        "blockers": [],
        "next_action": "",
        "dry_run": dry_run,
        "strategy_id": strategy_id,
    }

    def _finish(extra: dict[str, Any]) -> dict[str, Any]:
        out = {**base_state, **extra}
        write_loop_state(root, out)
        return out

    read = build_forex_auto_paper_readiness(root, cfg, probe_ibkr=dry_run)
    equity = _account_equity_usd(cfg)
    pairs = pairs_list_from_forex_yaml(fx)

    if dry_run:
        scan_rows: list[dict[str, Any]] = []
        for pd in pairs[:4]:
            try:
                spec = parse_pair(pd)
            except ValueError:
                continue
            try:
                fetch_forex_1m_duration(
                    project_root=root,
                    pair_display=spec.display,
                    duration="1 D",
                    bar_size="1 min",
                    cfg=cfg,
                )
            except Exception as exc:
                scan_rows.append({"pair": pd, "fetch_error": str(exc)})
                continue
            from .candle_store import load_forex_candles

            bars = load_forex_candles(root, spec.slug, "1min")
            sig = simple_fx_ict_scan(spec.display, bars)
            row: dict[str, Any] = {
                "pair": pd,
                "bars": len(bars),
                "signal_direction": getattr(sig, "direction", None) if sig else None,
            }
            scan_rows.append(row)

        out = _finish(
            {
                "running": False,
                "readiness": read,
                "scan_preview": scan_rows,
                "next_action": "dry_run_completed_no_orders",
                "dry_run_equity_probe": equity,
            }
        )
        return out

    # ---- live-ish paper iteration (still paper account only) ----------------

    def _broker_probe_ok() -> bool:
        from bot.ibkr_connection import connect_readonly_roster_retry

        oc = connect_readonly_roster_retry(cfg, "broker_readonly")
        if oc.client is None:
            return False
        try:
            oc.client.get_account_summary()
            return True
        except Exception:
            return False
        finally:
            try:
                oc.client.disconnect()
            except Exception:
                pass

    def _gates() -> tuple[bool, list[str]]:
        b: list[str] = []
        if not read_runtime_auto_enabled(root):
            b.append("runtime_forex_auto_disabled")
        if not bool(ap_cfg.get("enabled", False)):
            b.append("auto_paper_yaml_disabled")
        if not bool(ex_cfg.get("submit_to_broker", False)):
            b.append("submit_to_broker_yaml_false")
        if is_kill_switch_active(cfg):
            b.append("kill_switch")
        if cfg.settings.account.mode != "paper":
            b.append("not_paper")
        if not sess_open:
            b.append("outside_melbourne_session")
        rb = _broker_probe_ok()
        if not rb:
            b.append("tws_unreachable")
            send_fx_telegram(
                project_root=root,
                cfg=cfg,
                journal=journal,
                body="[Forex Auto] TWS unreachable during session",
                throttle_key="fx_tws_down",
            )
        tg_ct = nt.get("trade_count_pairs") or {}
        tsum = sum(int(v) for v in tg_ct.values()) if isinstance(tg_ct, dict) else 0
        if tsum >= mx_td:
            b.append("max_trades_per_day")
            send_fx_telegram(
                project_root=root,
                cfg=cfg,
                journal=journal,
                throttle_key="fx_cap_day_notice",
                body=f"[Forex Auto] max_trades/day {mx_td} reached",
            )
        ok = len(b) == 0
        return ok, b

    gates_ok, reasons = _gates()
    if not gates_ok:
        return _finish({"running": False, "blockers": reasons, "next_action": "fix_blockers"})

    if equity is None:
        append_forex_order_event(
            root, {"phase": "supervisor", "skipped": "equity_unknown"}
        )
        return _finish(
            {
                "blockers": ["equity_probe_failed"],
                "next_action": "check_tws_paper_balance",
                "running": False,
            }
        )

    last_signal_desc: dict[str, Any] | None = None
    rej: str | None = None

    for pd in pairs:
        try:
            spec = parse_pair(pd)
        except ValueError:
            continue

        slug_u = spec.slug.upper()
        tpair = nt.get("trade_count_pairs") or {}
        tc_pair = (
            int(tpair.get(slug_u, 0)) if isinstance(tpair, dict) else 0
        )
        if tc_pair >= mx_tp:
            continue

        pip = pip_size_for_pair(spec)
        pd_spent = 0.0
        bp = nt.get("by_pair")
        if isinstance(bp, dict) and slug_u in bp:
            pd_spent = float(bp.get(slug_u, 0))

        try:
            fetch_forex_1m_duration(
                project_root=root,
                pair_display=spec.display,
                duration="2 D",
                bar_size="1 min",
                cfg=cfg,
            )
        except Exception as exc:
            LOG.debug("forex_fetch %s: %s", spec.display, exc)
            append_forex_order_event(
                root,
                {"phase": "fetch_error", "pair": spec.display, "err": str(exc)},
            )
            continue

        from .candle_store import load_forex_candles

        bars = load_forex_candles(root, spec.slug, "1min")
        sig = simple_fx_ict_scan(spec.display, bars)
        if sig:
            last_signal_desc = sig.to_dict()

        if sig is None or sig.direction == "flat":
            continue

        pf = validate_bracket(
            direction=sig.direction,
            entry=float(sig.entry or 0),
            stop=float(sig.stop or 0),
            target=float(sig.target or 0),
            min_tick=pip,
            order_type=str(ex_cfg.get("order_type") or "LMT"),
        )
        if not pf.ok:
            rej = ";".join(pf.reasons)
            append_forex_order_event(
                root,
                {
                    "phase": "preflight",
                    "pair": spec.display,
                    "reasons": pf.reasons,
                },
            )
            continue

        size = estimate_units_for_risk(
            spec,
            equity_usd=float(equity),
            risk_pct=rk_pct,
            entry=float(sig.entry or 0),
            stop=float(sig.stop or 0),
            max_units=max_units_r,
            pip_size=pip,
        )
        if not size.sizing_available or size.units <= 0:
            continue

        day_rem = max(0.0, mx_daily - float(nt.get("total_usd") or 0))
        pair_remaining = max(0.0, mx_pair_cap - pd_spent)

        u_scaled, rn = shrink_units_for_notional_caps(
            spec,
            units_in=size.units,
            mid_price=float(sig.entry or 0),
            max_trade_usd=mx_trade,
            pair_remaining_usd=pair_remaining,
            daily_remaining_usd=day_rem,
            usd_per_jpy=inv_uj,
        )
        if u_scaled <= 0:
            continue

        n_usd_est = estimate_notional_usd_approx(
            spec,
            units=u_scaled,
            mid_price=float(sig.entry or 0),
            usd_per_jpy=inv_uj,
        )

        can_n, crs = can_add_notional(
            root,
            pair_slug=slug_u,
            usd_estimate=n_usd_est,
            max_daily_usd=mx_daily,
            max_pair_usd=mx_pair_cap,
            timezone_name=tz_name,
        )
        if not can_n:
            rej = crs or "notional_blocked"
            if crs == "max_daily_notional_usd":
                send_fx_telegram(
                    project_root=root,
                    cfg=cfg,
                    journal=journal,
                    body="[Forex Auto] USD 100k daily notional cap hit",
                    throttle_key="fx_daily_notional_cap",
                )
            return _finish(
                {
                    "last_signal": last_signal_desc,
                    "last_reject_reason": rej,
                    "running": False,
                    "next_action": "cap_block",
                    "blockers": [rej],
                },
            )

        from .paper_submit import submit_forex_paper_bracket

        sr = submit_forex_paper_bracket(
            project_root=root,
            cfg=cfg,
            spec=spec,
            direction=sig.direction,
            units=u_scaled,
            entry=float(sig.entry or 0),
            stop=float(sig.stop or 0),
            target=float(sig.target or 0),
            order_ref_prefix=order_ref,
            tif=tif,
        )
        append_forex_order_event(
            root,
            {
                "phase": "auto_paper",
                "pair": spec.display,
                "notional_usd_est": n_usd_est,
                "broker": sr,
            },
        )

        if sr.get("ok"):
            record_notional_trade(
                root,
                pair_slug=slug_u,
                usd_estimate=n_usd_est,
                timezone_name=tz_name,
            )
            send_fx_telegram(
                project_root=root,
                cfg=cfg,
                journal=journal,
                body=(
                    "[Forex Auto] Submitted bracket "
                    f"{spec.display} notion≈${n_usd_est:,.0f} ok={sr.get('ok')}"
                ),
            )
        else:
            send_fx_telegram(
                project_root=root,
                cfg=cfg,
                journal=journal,
                body=f"[Forex Auto] Order issue {spec.display}: {json.dumps(sr)[:700]}",
            )

        ot = (
            "submitted"
            if sr.get("ok")
            else (sr.get("error") or "error")
        )
        return _finish(
            {
                "running": True,
                "last_signal": last_signal_desc,
                "last_order_status": ot,
                "broker_result": sr,
                "planned_notional_usd": n_usd_est,
                "units_sent": u_scaled,
                "sizing_reason": rn,
                "next_action": "submitted_attempt",
                "blockers": [],
                "kill_switch_checked": True,
                "gates": [],
            },
        )

    return _finish(
        {
            "running": False,
            "last_signal": last_signal_desc,
            "last_order_status": None,
            "last_reject_reason": rej,
            "next_action": "no_tradeable_signal",
            "blockers": ["no_tradeable_signal"],
            "readiness": read,
        },
    )


__all__ = [
    "run_forex_auto_paper_supervisor",
    "LOOP_STATE_RELPATH",
    "write_loop_state",
]
