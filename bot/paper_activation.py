"""Local paper activation, readiness checks, and first-pass helper (Prompt 13I).

Does not enable live trading. Bracket/LIMIT paper paths only via existing
execution modules. No market orders.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .config import AppConfig, Settings, _deep_merge_dict, _read_yaml
from .execution.intraday_paper_execution import (
    INTRADAY_AUTO_PAPER_ENABLED_RELPATH,
    INTRADAY_LOOP_STATE_RELPATH,
    KILL_SWITCH_RELPATH,
    is_intraday_paper_runtime_enabled,
    is_kill_switch_active,
)
from .journal import Journal

PAPER_READINESS_STATE_RELPATH = "data/runtime/paper_readiness_state.json"
FIRST_PAPER_PASS_LAST_RELPATH = "data/runtime/first_paper_pass_last.json"

# Proposed merge for settings.local.yaml (tracked defaults + local-only overlay).
PAPER_LOCAL_PATCH: dict[str, Any] = {
    "account": {
        "mode": "paper",
        "block_live_trading": True,
    },
    "trading": {
        "enabled": True,
        "intraday_paper": {
            "enabled": True,
            "fully_automatic": False,
            "allow_strict_entries": True,
            "allow_aggressive_entries": True,
            "risk_per_trade_pct": 0.10,
            "max_concurrent_positions": 5,
            "max_one_position_per_symbol": True,
            "require_reconciliation_pass": True,
            "no_new_entries_before": "09:45",
            "no_new_entries_after": "15:30",
            "exit_open_positions_at": "15:55",
            "paper_only": True,
            "live_trading_allowed": False,
            "market_orders_allowed": False,
            "bracket_required": True,
            "stop_required": True,
            "target_required": True,
            "dry_run": False,
        },
    },
}


def settings_local_path(cfg: AppConfig) -> Path:
    return (cfg.project_root / "config" / "settings.local.yaml").resolve()


def _runtime_flag_content(cfg: AppConfig) -> str:
    p = cfg.absolute(INTRADAY_AUTO_PAPER_ENABLED_RELPATH)
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _last_reconcile_hint(cfg: AppConfig) -> str | None:
    sp = cfg.absolute(INTRADAY_LOOP_STATE_RELPATH)
    if not sp.is_file():
        return None
    try:
        st = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    r = st.get("reconciliation_status")
    return str(r) if r is not None else None


def build_paper_activation_status(
    cfg: AppConfig,
    *,
    probe_ibkr: bool = False,
    journal: Journal | None = None,
) -> dict[str, Any]:
    """Aggregate activation snapshot. Connects IBKR only when probe_ibkr=True."""
    s_local = settings_local_path(cfg)
    acct = cfg.settings.account
    t = cfg.settings.trading
    ip = t.intraday_paper
    runtime_on, runtime_off_exp = is_intraday_paper_runtime_enabled(cfg)
    kill = is_kill_switch_active(cfg)
    blocks: list[str] = []
    if acct.mode != "paper":
        blocks.append("account.mode must be paper")
    if not acct.block_live_trading:
        blocks.append("account.block_live_trading must be true")
    if not t.enabled:
        blocks.append("trading.enabled is false")
    if not ip.enabled:
        blocks.append("trading.intraday_paper.enabled is false")
    if ip.live_trading_allowed:
        blocks.append("intraday_paper.live_trading_allowed must be false")
    if ip.market_orders_allowed:
        blocks.append("intraday_paper.market_orders_allowed must be false")
    if not (ip.bracket_required and ip.stop_required and ip.target_required):
        blocks.append("bracket/stop/target must be required")
    if kill:
        blocks.append("kill switch is active")
    if not runtime_on:
        blocks.append("intraday runtime flag is not ON (data/runtime/intraday_auto_paper_enabled)")

    ready = len(blocks) == 0
    out: dict[str, Any] = {
        "settings_local_yaml_exists": s_local.is_file(),
        "settings_local_path": str(s_local),
        "trading_enabled": bool(t.enabled),
        "intraday_paper_enabled": bool(ip.enabled),
        "intraday_paper_dry_run": bool(ip.dry_run),
        "intraday_paper_fully_automatic": bool(ip.fully_automatic),
        "account_mode": str(acct.mode),
        "account_block_live_trading": bool(acct.block_live_trading),
        "paper_only": True,
        "live_trading_allowed": bool(ip.live_trading_allowed),
        "market_orders_allowed": bool(ip.market_orders_allowed),
        "bracket_required": bool(ip.bracket_required),
        "stop_required": bool(ip.stop_required),
        "target_required": bool(ip.target_required),
        "runtime_intraday_flag_path": str(cfg.absolute(INTRADAY_AUTO_PAPER_ENABLED_RELPATH)),
        "runtime_intraday_flag_raw": _runtime_flag_content(cfg) or None,
        "runtime_intraday_on": runtime_on,
        "runtime_intraday_explicit_off": runtime_off_exp,
        "kill_switch_active": kill,
        "kill_switch_path": str(cfg.absolute(KILL_SWITCH_RELPATH)),
        "last_reconcile_status_hint": _last_reconcile_hint(cfg),
        "final_readiness": "READY_FOR_PAPER_TEST" if ready else "NOT_READY",
        "blocking_reasons": blocks,
        "probe_ibkr": bool(probe_ibkr),
    }

    if probe_ibkr and journal is not None:
        rep, err = _try_reconcile(cfg, journal)
        out["ibkr_probe_error"] = err
        if rep is not None:
            out["reconciliation_passed"] = rep.passed
            out["reconciliation"] = {
                "passed": rep.passed,
                "positions_without_stops": list(rep.positions_without_stops),
                "unknown_open_orders": len(rep.unknown_open_orders),
                "missing_local_records": list(rep.missing_local_records),
            }
        else:
            out["reconciliation_passed"] = None
    return out


def _try_reconcile(cfg: AppConfig, journal: Journal) -> tuple[Any | None, str | None]:
    from .broker import Broker  # noqa: PLC0415
    from .ibkr_client import IBKRClient, IBKRClientError  # noqa: PLC0415
    from .reconciliation import reconcile  # noqa: PLC0415

    client = IBKRClient(cfg)
    try:
        client.connect(readonly=True)
    except (IBKRClientError, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc!s}"
    try:
        broker = Broker(cfg, client, journal)
        return reconcile(broker, journal), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc!s}"
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass


def _validate_safety_invariants(merged_local: dict[str, Any]) -> tuple[bool, str]:
    acct = (merged_local.get("account") or {}) if isinstance(merged_local, dict) else {}
    tr = (merged_local.get("trading") or {}) if isinstance(merged_local, dict) else {}
    ip = (tr.get("intraday_paper") or {}) if isinstance(tr, dict) else {}
    if str(acct.get("mode", "")).lower() != "paper":
        return False, "refuse: account.mode must be paper"
    if acct.get("block_live_trading") is not True:
        return False, "refuse: account.block_live_trading must be true"
    if ip.get("live_trading_allowed") is not False:
        return False, "refuse: intraday_paper.live_trading_allowed must be false"
    if ip.get("market_orders_allowed") is not False:
        return False, "refuse: intraday_paper.market_orders_allowed must be false"
    for k in ("bracket_required", "stop_required", "target_required"):
        if ip.get(k) is not True:
            return False, f"refuse: intraday_paper.{k} must be true"
    if ip.get("paper_only") is not True:
        return False, "refuse: intraday_paper.paper_only must be true"
    return True, "ok"


def propose_local_settings_merged_with_existing(
    project_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (existing_local_dict, proposed_merged_local_dict)."""
    s_path = project_root / "config" / "settings.yaml"
    s_local = project_root / "config" / "settings.local.yaml"
    base = _read_yaml(s_path)
    existing = _read_yaml(s_local) if s_local.is_file() else {}
    proposed = _deep_merge_dict(_deep_merge_dict({}, existing), PAPER_LOCAL_PATCH)
    return existing, proposed


def validate_merged_settings(project_root: Path) -> None:
    """Load base + proposed local and validate Pydantic Settings."""
    s_path = project_root / "config" / "settings.yaml"
    s_local = project_root / "config" / "settings.local.yaml"
    base = _read_yaml(s_path)
    if s_local.is_file():
        merged = _deep_merge_dict(base, _read_yaml(s_local))
    else:
        merged = base
    Settings(**merged)


def write_paper_local_config_file(
    project_root: Path,
    *,
    dry_run: bool,
    write: bool,
) -> dict[str, Any]:
    """Write or preview settings.local.yaml. Backs up existing file on write."""
    s_local = project_root / "config" / "settings.local.yaml"
    s_local.parent.mkdir(parents=True, exist_ok=True)
    _, proposed = propose_local_settings_merged_with_existing(project_root)
    ok, msg = _validate_safety_invariants(proposed)
    if not ok:
        return {"ok": False, "error": msg, "proposed_yaml": None}
    # Validate full settings tree after merge.
    s_path = project_root / "config" / "settings.yaml"
    base = _read_yaml(s_path)
    full_merge = _deep_merge_dict(base, proposed)
    try:
        Settings(**full_merge)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"invalid merged settings: {exc!s}", "proposed_yaml": None}

    yaml_s = yaml.safe_dump(proposed, sort_keys=False, allow_unicode=True)
    result: dict[str, Any] = {
        "ok": True,
        "path": str(s_local.resolve()),
        "proposed_yaml": yaml_s,
        "wrote": False,
        "backup_path": None,
    }
    if dry_run or not write:
        return result
    if s_local.is_file():
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        bak = s_local.with_name(f"settings.local.yaml.bak.{ts}")
        shutil.copy2(s_local, bak)
        result["backup_path"] = str(bak)
    s_local.write_text(yaml_s, encoding="utf-8")
    result["wrote"] = True
    return result


def set_intraday_runtime_flag(cfg: AppConfig, *, on: bool) -> Path:
    p = cfg.absolute(INTRADAY_AUTO_PAPER_ENABLED_RELPATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("1\n" if on else "0\n", encoding="utf-8")
    return p


@dataclass
class ReadinessResult:
    passed: bool
    status: str
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_action: str = ""
    scan_summary: dict[str, Any] | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def run_paper_readiness_check(
    cfg: AppConfig,
    journal: Journal,
    *,
    intraday: bool,
    probe_ibkr: bool,
    run_scan: bool,
    source: str = "dynamic",
    limit: int = 20,
) -> ReadinessResult:
    reasons: list[str] = []
    warns: list[str] = []
    rep: Any | None = None
    act: dict[str, Any] | None = None

    if not intraday:
        r = ReadinessResult(
            passed=False,
            status="FAIL",
            blocking_reasons=["--intraday is required for this check"],
            next_action="Run: paper-readiness-check --intraday",
        )
        r.payload = _readiness_to_dict(r, rep, act)
        _persist_readiness_state(cfg, r.payload)
        return r

    act = build_paper_activation_status(cfg, probe_ibkr=False, journal=None)
    reasons.extend(list(act.get("blocking_reasons") or []))

    if probe_ibkr:
        rep, err = _try_reconcile(cfg, journal)
        if err:
            reasons.append(f"ibkr: {err}")
        elif rep is not None and not rep.passed:
            reasons.append("reconciliation did not pass")
            if rep.missing_local_records:
                reasons.append(f"missing_local_records: {rep.missing_local_records}")
            if rep.unknown_open_orders:
                reasons.append(f"unknown_open_orders: {len(rep.unknown_open_orders)}")

    scan_summary: dict[str, Any] | None = None
    if run_scan:
        from .strategies.ict_smc_intraday import IntradayRiskConfig, scan_watchlist_with_ibkr

        try:
            scan_summary = scan_watchlist_with_ibkr(
                cfg,
                journal,
                use_ibkr=True,
                chart=False,
                telegram=False,
                limit=limit,
                source=source,
                save_json=True,
                risk_cfg=IntradayRiskConfig(),
            )
        except (FileNotFoundError, ValueError, OSError) as exc:
            reasons.append(f"scan failed: {exc!s}")
    else:
        idir = Path(cfg.absolute("data/intraday_smc"))
        if idir.is_dir():
            cands = sorted(idir.glob("*-watchlist-intraday-smc-summary.json"))
            if cands:
                try:
                    scan_summary = json.loads(cands[-1].read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    warns.append("could not read latest intraday watchlist summary json")
        if scan_summary is None and not any("scan failed" in x for x in reasons):
            reasons.append(
                "no intraday watchlist summary under data/intraday_smc/; re-run with --scan"
            )

    if scan_summary:
        strict_n = list(scan_summary.get("ready_strict_symbols") or [])
        aggr_n = list(scan_summary.get("ready_aggressive_symbols") or [])
        has_ready = len(strict_n) + len(aggr_n) > 0
        if not has_ready:
            reasons.append(
                "no DAY_TRADE_READY_STRICT or DAY_TRADE_READY_AGGRESSIVE symbols in scan summary"
            )
            ws = list(scan_summary.get("watch_symbols") or [])[:15]
            if ws:
                warns.append(f"top WATCH-only candidates (not ready): {', '.join(ws)}")

    passed = len(reasons) == 0
    next_a = "Run first-paper-pass (one bracket pass)" if passed else "Fix blocking items, then re-run"
    st = "PASS" if passed else "FAIL"
    r = ReadinessResult(
        passed=passed,
        status=st,
        blocking_reasons=reasons,
        warnings=warns,
        next_action=next_a,
        scan_summary=scan_summary,
    )
    r.payload = _readiness_to_dict(r, rep, act)
    _persist_readiness_state(cfg, r.payload)
    return r


def _readiness_to_dict(
    r: ReadinessResult,
    rep: Any,
    act: dict[str, Any] | None,
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "status": r.status,
        "passed": r.passed,
        "blocking_reasons": r.blocking_reasons,
        "warnings": r.warnings,
        "next_action": r.next_action,
    }
    if r.scan_summary:
        d["scan"] = {
            "date": r.scan_summary.get("date"),
            "ready_strict": r.scan_summary.get("ready_strict_symbols") or [],
            "ready_aggressive": r.scan_summary.get("ready_aggressive_symbols") or [],
            "watch_symbols": (r.scan_summary.get("watch_symbols") or [])[:20],
        }
    if rep is not None:
        d["reconciliation"] = rep.as_dict()
    if act is not None:
        d["activation"] = act
    d["checked_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return d


def _persist_readiness_state(cfg: AppConfig, payload: dict[str, Any]) -> None:
    p = Path(cfg.absolute(PAPER_READINESS_STATE_RELPATH))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_first_paper_pass(
    cfg: AppConfig,
    journal: Journal,
    *,
    source: str = "dynamic",
    limit: int = 20,
    telegram: bool = False,
) -> dict[str, Any]:
    """One pass: activation snapshot, readiness, then run_intraday_paper_pass."""
    from dataclasses import asdict

    out: dict[str, Any] = {
        "paper_only": True,
        "live_trading_allowed": False,
    }
    act = build_paper_activation_status(cfg, probe_ibkr=False)
    out["paper_activation_status"] = act
    if act.get("final_readiness") != "READY_FOR_PAPER_TEST":
        out["result"] = "skipped"
        out["reasons"] = act.get("blocking_reasons") or []
        _write_first_pass_state(cfg, out)
        return out

    rr = run_paper_readiness_check(
        cfg,
        journal,
        intraday=True,
        probe_ibkr=True,
        run_scan=True,
        source=source,
        limit=limit,
    )
    out["readiness"] = rr.payload
    if not rr.passed:
        out["result"] = "skipped"
        out["reasons"] = rr.blocking_reasons
        if rr.scan_summary:
            out["top_watch_candidates"] = (rr.scan_summary.get("watch_symbols") or [])[:15]
        _write_first_pass_state(cfg, out)
        return out

    from .execution.intraday_paper_execution import (  # noqa: PLC0415
        run_intraday_paper_pass,
        serialize_paper_submission,
    )

    result = run_intraday_paper_pass(
        cfg, journal, source=source, limit=limit, telegram=telegram, chart=False
    )
    pld = asdict(result)
    pld["submissions"] = [serialize_paper_submission(s) for s in result.submissions]
    out["execution"] = pld
    out["result"] = "ok" if result.last_status not in {"failed", "error"} else "error"

    if result.orders_submitted == 0:
        out["result"] = "skipped"
        w: list[str] = []
        for s in result.submissions:
            w.extend(s.skipped_reasons)
        if not w and result.last_reason:
            w.append(result.last_reason)
        out["reasons"] = w or ["no orders submitted"]
        out["top_watch_candidates"] = (rr.scan_summary.get("watch_symbols") or [])[:15] if rr.scan_summary else []
    else:
        out["order_summary"] = _summarize_submissions(result)
    _write_first_pass_state(cfg, out)
    return out


def _write_first_pass_state(cfg: AppConfig, payload: dict[str, Any]) -> None:
    p = Path(cfg.absolute(FIRST_PAPER_PASS_LAST_RELPATH))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False) + "\n", encoding="utf-8")


def _summarize_submissions(result: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for s in result.submissions:
        if not s.submitted or not s.intent:
            continue
        it = s.intent
        rows.append(
            {
                "symbol": it.symbol,
                "direction": it.direction,
                "signal_category": it.signal_category,
                "entry": it.entry_price,
                "stop": it.stop_price,
                "target": it.target_price,
                "planned_rr": it.planned_rr,
                "quantity": it.quantity,
                "order_ids": list(s.order_ids),
            }
        )
    return rows
