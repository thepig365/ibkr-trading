"""Run Forex ICT 1m scan + optional paper submit (gates in YAML)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import AppConfig, load_config
from bot.execution.intraday_paper_execution import is_kill_switch_active

from . import FOREX_ORDER_REF_PREFIX_DEFAULT, FOREX_RUNTIME_LAST
from .candle_store import load_forex_candles
from .config import load_forex_ict_config, melbourne_session_open
from .orders_log import append_forex_order_event, forex_orders_path
from .pairs import parse_pair, pip_size_for_pair
from .preflight import validate_bracket
from .signals import FxIctSignal, simple_fx_ict_scan  # noqa: F401 — re-export compat
from .sizing import estimate_units_for_risk

LOG = logging.getLogger(__name__)


def _forex_runtime_path(root: Path) -> Path:
    return root / FOREX_RUNTIME_LAST


def _read_today_jsonl_counts(project_root: Path) -> dict[str, int]:
    p = forex_orders_path(project_root)
    acc = 0
    rej = 0
    fil = 0
    if not p.is_file():
        return {"submitted": acc, "rejected": rej, "filled_hint": fil}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        st = str(rec.get("status") or "").lower()
        if "submit" in st or st in ("submitted", "pending_ack"):
            acc += 1
        if "reject" in st or st == "rejected" or st == "api_error":
            rej += 1
        if "fill" in st or st == "filled":
            fil += 1
    return {"submitted": acc, "rejected": rej, "filled_hint": fil}


def _account_equity_usd(cfg: AppConfig) -> float | None:
    from bot.ibkr_connection import connect_readonly_roster_retry

    out = connect_readonly_roster_retry(cfg, "broker_readonly")
    if out.client is None:
        return None
    try:
        summ = out.client.get_account_summary()
        for a in summ:
            if a.net_liquidation and float(a.net_liquidation) > 0:
                return float(a.net_liquidation)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("equity probe: %s", exc)
    finally:
        try:
            out.client.disconnect()
        except Exception:
            pass
    return None


def run_forex_ict_1m(
    project_root: Path | str,
    *,
    dry_run: bool = True,
    paper: bool = False,
) -> dict[str, Any]:
    """Scan configured pairs; dry_run never submits. Paper requires YAML gates."""

    root = Path(project_root).resolve()
    cfg = load_config(project_root=root)
    fx = load_forex_ict_config(root)
    risk = fx.get("risk") if isinstance(fx.get("risk"), dict) else {}
    execution = fx.get("execution") if isinstance(fx.get("execution"), dict) else {}
    pairs_raw = fx.get("pairs") if isinstance(fx.get("pairs"), list) else []
    pairs = [str(x) for x in pairs_raw]

    strategy_id = str(fx.get("strategy_id") or "ict_fx_1m_test")
    enabled = bool(fx.get("enabled", False))
    submit_to_broker = bool(execution.get("submit_to_broker", False))
    order_ref = str(execution.get("order_ref_prefix") or FOREX_ORDER_REF_PREFIX_DEFAULT)
    tif = str(execution.get("tif") or "DAY").upper()
    max_units = float(risk.get("max_units_per_trade") or 100_000)
    max_trades_day = int(risk.get("max_trades_per_day") or 10)
    risk_pct = float(risk.get("risk_per_trade_pct") or 0.05)
    paper_only_yaml = bool(risk.get("paper_only", True))

    now = datetime.now(timezone.utc)
    in_sess, mel_hhmm = melbourne_session_open(fx, now_tz=now)

    out: dict[str, Any] = {
        "strategy_id": strategy_id,
        "dry_run": dry_run,
        "paper_requested": paper,
        "melbourne_session_open": in_sess,
        "melbourne_local_time": mel_hhmm,
        "pairs": pairs,
        "enabled_config": enabled,
        "submit_to_broker_config": submit_to_broker,
        "signals": [],
        "blockers": [],
        "counts_today_forex_journal": _read_today_jsonl_counts(root),
    }

    kill = is_kill_switch_active(cfg)
    if kill:
        out["blockers"].append("kill_switch")
    acct_ok = cfg.settings.account.mode == "paper"
    if not acct_ok:
        out["blockers"].append("account_not_paper")
    if not bool(risk.get("paper_only", True)) or not paper_only_yaml:
        out["blockers"].append("forex_yaml_paper_only_invariant")
    if not bool(risk.get("no_market_orders", True)):
        out["blockers"].append("forex_must_disallow_market_orders")

    if paper:
        if not enabled:
            out["blockers"].append("forex_ict_yaml_enabled_false")

    equity: float | None = None
    if paper and not dry_run and enabled and submit_to_broker:
        equity = _account_equity_usd(cfg)
        out["equity_usd_probe"] = equity

    results: list[dict[str, Any]] = []
    for pd in pairs:
        try:
            spec = parse_pair(pd)
        except ValueError as e:
            results.append({"pair": pd, "error": str(e)})
            continue
        bars = load_forex_candles(root, spec.slug, "1min")
        sig = simple_fx_ict_scan(spec.display, bars)
        pip = pip_size_for_pair(spec)
        row: dict[str, Any] = {
            "pair": spec.display,
            "bars": len(bars),
            "pip_size": pip,
        }
        if sig is None:
            row["signal"] = None
            row["reason"] = "insufficient_bars_or_no_signal_fn"
            results.append(row)
            continue

        sdict = sig.to_dict()
        row["signal"] = sdict

        if sig.direction == "flat":
            append_forex_order_event(
                root,
                {
                    "strategy_id": strategy_id,
                    "pair": spec.display,
                    "phase": "proposal",
                    "note": "flat_signal",
                    "signal": sdict,
                },
            )
            results.append(row)
            continue

        if dry_run or not paper:
            append_forex_order_event(
                root,
                {
                    "strategy_id": strategy_id,
                    "pair": spec.display,
                    "phase": (
                        "dry_run_signal" if dry_run else "scan_only_signal_no_paper_flag"
                    ),
                    "signal": sdict,
                },
            )
            results.append(row)
            continue

        pf = validate_bracket(
            direction=sig.direction,
            entry=float(sig.entry or 0),
            stop=float(sig.stop or 0),
            target=float(sig.target or 0),
            min_tick=pip,
            order_type=str(execution.get("order_type") or "LMT"),
        )
        row["preflight"] = {"ok": pf.ok, "reasons": pf.reasons}
        if not pf.ok:
            append_forex_order_event(
                root,
                {
                    "strategy_id": strategy_id,
                    "pair": spec.display,
                    "phase": "preflight_blocked",
                    "signal": sdict,
                    "reasons": pf.reasons,
                },
            )
            row["skipped"] = "preflight"
            results.append(row)
            continue

        size = estimate_units_for_risk(
            spec,
            equity_usd=float(equity or 100_000),
            risk_pct=risk_pct,
            entry=float(sig.entry or 0),
            stop=float(sig.stop or 0),
            max_units=max_units,
            pip_size=pip,
        )
        row["sizing"] = {
            "units": size.units,
            "pip_distance": size.pip_distance,
            "sizing_available": size.sizing_available,
            "reason_if_unavailable": size.reason_if_unavailable,
            "risk_quote_ccy_approx": size.risk_quote_ccy_approx,
        }
        if equity is None and submit_to_broker:
            row["skipped"] = "sizing_unavailable_equity_unknown"
            append_forex_order_event(
                root,
                {
                    "strategy_id": strategy_id,
                    "pair": spec.display,
                    "phase": "sizing_blocked",
                    "reason": "equity_unknown",
                    "signal": sdict,
                },
            )
            results.append(row)
            continue
        if not size.sizing_available or size.units <= 0:
            row["skipped"] = "sizing_unavailable"
            append_forex_order_event(
                root,
                {
                    "strategy_id": strategy_id,
                    "pair": spec.display,
                    "phase": "sizing_blocked",
                    "reason": size.reason_if_unavailable or "sizing_failed",
                    "signal": sdict,
                },
            )
            results.append(row)
            continue

        jc = _read_today_jsonl_counts(root)
        if jc["submitted"] >= max_trades_day:
            row["skipped"] = "max_trades_per_day"
            out["blockers"].append("daily_cap_forex_signals")
            results.append(row)
            continue

        if out["blockers"]:
            append_forex_order_event(
                root,
                {
                    "strategy_id": strategy_id,
                    "pair": spec.display,
                    "phase": "blocked_globals",
                    "blockers": list(out["blockers"]),
                    "signal": sdict,
                },
            )
            row["skipped"] = "global_blockers"
            results.append(row)
            continue

        if not submit_to_broker:
            append_forex_order_event(
                root,
                {
                    "strategy_id": strategy_id,
                    "pair": spec.display,
                    "phase": "paper_signal_only",
                    "reason": "execution.submit_to_broker_false",
                    "units": size.units,
                    "signal": sdict,
                },
            )
            row["skipped"] = "submit_to_broker_false"
            results.append(row)
            continue

        if not in_sess:
            append_forex_order_event(
                root,
                {
                    "strategy_id": strategy_id,
                    "pair": spec.display,
                    "phase": "session_skipped",
                    "melbourne_ok": False,
                },
            )
            row["skipped"] = "outside_melbourne_window"
            results.append(row)
            continue

        from .paper_submit import submit_forex_paper_bracket

        sr = submit_forex_paper_bracket(
            project_root=root,
            cfg=cfg,
            spec=spec,
            direction=sig.direction,
            units=size.units,
            entry=float(sig.entry or 0),
            stop=float(sig.stop or 0),
            target=float(sig.target or 0),
            order_ref_prefix=order_ref,
            tif=tif,
        )
        row["broker"] = sr
        results.append(row)

    out["signals"] = results
    outp = root / FOREX_RUNTIME_LAST
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return out


def build_forex_test_ui_context(
    project_root: Path | str, *, cfg: AppConfig | None = None
) -> dict[str, Any]:
    """File-based summary for Dashboard / Paper (no broker connection)."""

    root = Path(project_root).resolve()
    fx = load_forex_ict_config(root)
    cfg = cfg or load_config(project_root=root)
    rp = _forex_runtime_path(root)

    pairs = fx.get("pairs") if isinstance(fx.get("pairs"), list) else []
    counts = _read_today_jsonl_counts(root)

    runtime = None
    if rp.is_file():
        try:
            runtime = json.loads(rp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            runtime = None

    now = datetime.now(timezone.utc)
    in_sess, mel_hhmm = melbourne_session_open(fx, now_tz=now)

    mode = "off"
    exec_block = fx.get("execution") if isinstance(fx.get("execution"), dict) else {}
    if bool(fx.get("enabled")):
        mode = (
            "paper"
            if bool(exec_block.get("submit_to_broker"))
            else "dry-run"
        )

    latest_sig = None
    if runtime and isinstance(runtime.get("signals"), list) and runtime["signals"]:
        latest_sig = runtime["signals"][-1]

    return {
        "title_en": "Forex ICT 1M Test",
        "title_zh": "外汇 ICT 1分钟测试",
        "mode": mode,
        "pairs": [str(x) for x in pairs],
        "enabled": bool(fx.get("enabled")),
        "submit_to_broker": bool(exec_block.get("submit_to_broker")),
        "melbourne_session_open": in_sess,
        "melbourne_time": mel_hhmm,
        "kill_switch_active": is_kill_switch_active(cfg),
        "strategy_id": str(fx.get("strategy_id") or "ict_fx_1m_test"),
        "counts_today_jsonl": counts,
        "latest_runtime": runtime,
        "latest_signal_row": latest_sig,
        "next_action": (
            "Run fetch-forex-candles then CLI run-forex-ict-1m --dry-run"
            if mode == "off"
            else (
                "Optional: CLI run-forex-ict-1m --paper — broker submit stays off until YAML sets submit_to_broker"
                if mode == "dry-run"
                else "Confirm paper account + readiness;broker submit uses LMT brackets only per YAML"
            )
        ),
    }


__all__ = ["run_forex_ict_1m", "build_forex_test_ui_context", "FxIctSignal"]
