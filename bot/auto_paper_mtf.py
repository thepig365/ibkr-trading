"""Automatic MTF SMC/ICT PAPER trading pass (read config + kill switch + runtime file)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AppConfig
from .journal import Journal
from .ny_session_windows import (
    us_morning_paper_window_allows,
    us_ny_rth_window_allows,
    us_rth_allows_new_entries,
)
from .reconciliation import reconcile
from .broker import Broker
from .ibkr_client import IBKRClient

logger = logging.getLogger(__name__)


def _abs(cfg: AppConfig, rel: str) -> Path:
    return cfg.absolute(rel)


def is_kill_switch_active(cfg: AppConfig) -> bool:
    return _abs(cfg, "data/KILL_SWITCH").is_file()


def is_runtime_mtf_auto_enabled(cfg: AppConfig) -> bool:
    p = _abs(cfg, "data/runtime/mtf_auto_paper_enabled")
    if not p.is_file():
        return False
    try:
        t = p.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return False
    return t in ("1", "true", "yes", "on")


def is_runtime_mtf_auto_disabled_explicit(cfg: AppConfig) -> bool:
    """True when the runtime file explicitly turns submissions off (e.g. /auto_mtf_off)."""
    p = _abs(cfg, "data/runtime/mtf_auto_paper_enabled")
    if not p.is_file():
        return False
    try:
        t = p.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return False
    return t in ("0", "false", "no", "off")


@dataclass
class AutoPaperMtfResult:
    ok: bool
    message: str
    summary: dict[str, Any] | None = None
    preflight: dict[str, Any] = field(default_factory=dict)


def _reconcile_or_fail(cfg: AppConfig, journal: Journal) -> tuple[bool, str]:
    c = IBKRClient(cfg)
    try:
        c.connect(readonly=True)
        b = Broker(cfg, c, journal=journal)
        rep = reconcile(b, journal)
        if not rep.passed and cfg.settings.risk.block_new_trades_if_reconciliation_fails:
            return False, f"reconciliation FAIL: {rep.notes!s}"
        return True, "ok" if rep.passed else f"reconciliation warn: {rep.notes!s}"
    except Exception as exc:  # noqa: BLE001
        return False, f"reconcile: {exc}"
    finally:
        try:
            c.disconnect()
        except Exception:  # noqa: BLE001
            pass


def _paper_account_ok(cfg: AppConfig) -> tuple[bool, str]:
    if cfg.settings.account.block_live_trading is not True:
        return False, "block_live_trading must be true"
    if (cfg.settings.account.mode or "").lower() != "paper":
        return False, "account.mode must be paper"
    if (cfg.ibkr.account_mode or "").lower() not in ("paper", "demo", "test"):
        return False, "IBKR_ACCOUNT_MODE is not paper in environment"
    return True, "paper ok"


def run_auto_paper_mtf(
    cfg: AppConfig,
    journal: Journal,
    *,
    source: str = "dynamic",
    limit: int = 20,
    max_paper_trades: int = 1,
    telegram: bool = False,
    chart: bool = False,
    bypass_runtime_guard: bool = False,
) -> AutoPaperMtfResult:
    """Single automatic pass: preflight, then `run_mtf_smc_watchlist_scan` with or without paper."""
    pre: dict[str, Any] = {"kill_switch": is_kill_switch_active(cfg)}
    if pre["kill_switch"]:
        return AutoPaperMtfResult(
            False, "KILL_SWITCH file present; no work done.", preflight=pre
        )
    ok, msg = _paper_account_ok(cfg)
    pre["paper_account"] = msg
    if not ok:
        return AutoPaperMtfResult(False, msg, preflight=pre)
    r_ok, rmsg = _reconcile_or_fail(cfg, journal)
    pre["reconcile"] = rmsg
    if not r_ok:
        return AutoPaperMtfResult(False, rmsg, preflight=pre)
    t = cfg.settings.trading
    ap = t.mtf_auto_paper
    run_gate = (
        bypass_runtime_guard
        or is_runtime_mtf_auto_enabled(cfg)
        or (ap.fully_automatic and ap.enabled and not is_runtime_mtf_auto_disabled_explicit(cfg))
    )
    can_paper = bool(
        t.enabled
        and t.mtf_paper_bracket_enabled
        and not t.mtf_paper_dry_run
        and ap.enabled
        and not ap.allow_live_trading
        and run_gate
    )
    pre["can_submit_paper"] = can_paper
    from .mtf_smc_batch import run_mtf_smc_watchlist_scan

    try:
        summary = run_mtf_smc_watchlist_scan(
            cfg,
            journal,
            use_ibkr=True,
            chart=chart,
            telegram=telegram,
            limit=limit,
            source=source,
            save_json=True,
            include_5min=True,
            include_daily=True,
            paper_bracket=can_paper,
            max_paper_trades=max_paper_trades if can_paper else 0,
        )
    except FileNotFoundError:
        return AutoPaperMtfResult(
            False, "Build dynamic watchlist first (build-watchlist).", preflight=pre
        )
    full_n = int((summary.get("counts") or {}).get("FULL_ALIGNMENT", 0) or 0)
    runs = list(summary.get("mtf_paper_bracket_runs") or [])
    any_sub = any(
        (r.get("result") or {}).get("submitted")
        for r in runs
        if isinstance(r, dict) and r.get("result")
    )
    if not can_paper:
        return AutoPaperMtfResult(
            True,
            "Scan complete; paper submission disabled (config/runtime/ dry_run).",
            summary=summary,
            preflight=pre,
        )
    if full_n == 0:
        return AutoPaperMtfResult(
            True,
            "No FULL_ALIGNMENT; no paper order.",
            summary=summary,
            preflight=pre,
        )
    if not any_sub and runs:
        return AutoPaperMtfResult(
            True,
            f"FULL_ALIGNMENT={full_n} but no bracket submitted: {runs!r}",
            summary=summary,
            preflight=pre,
        )
    if not any_sub:
        return AutoPaperMtfResult(
            True,
            f"FULL_ALIGNMENT count={full_n}; check eligibility / duplicates.",
            summary=summary,
            preflight=pre,
        )
    return AutoPaperMtfResult(
        True, "Paper execution path finished.", summary=summary, preflight=pre
    )