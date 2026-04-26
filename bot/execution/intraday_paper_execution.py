"""ICT/SMC intraday paper bracket execution (Prompt 13F).

This module is the **only** intraday code path that may produce a paper
order. It wraps :class:`bot.broker.Broker` with extra defence-in-depth
checks, but it never bypasses any broker safety gate.

Hard invariants (re-checked on every call):

* ``paper_only=True``
* ``live_trading_allowed=False``
* ``market_orders_allowed=False`` (we always submit a LIMIT bracket)
* every order is a bracket (LIMIT entry + STOP loss + LIMIT target)

Hard-block conditions (the broker may also re-block):

* live account detected (``account.mode != "paper"`` /
  ``block_live_trading=false``)
* kill switch active
* runtime intraday flag explicitly OFF *or* config disabled (and not
  fully-automatic)
* reconciliation fail when ``require_reconciliation_pass=true``
* missing stop / target, invalid bracket geometry, R/R below ``min_rr``
* duplicate same-symbol position OR duplicate same-symbol open order
* unknown broker / account state
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..config import AppConfig
from ..journal import Journal
from ..risk_engine import TradeIntent
from ..strategies.ict_smc_intraday.model import (
    DIRECTION_LONG,
    DIRECTION_SHORT,
    SIGNAL_DAY_TRADE_READY_AGGRESSIVE,
    SIGNAL_DAY_TRADE_READY_STRICT,
    STRATEGY_KEY,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical paths shared with the UI / loop / runtime flags.
# ---------------------------------------------------------------------------
KILL_SWITCH_RELPATH = "data/KILL_SWITCH"
INTRADAY_AUTO_PAPER_ENABLED_RELPATH = "data/runtime/intraday_auto_paper_enabled"
INTRADAY_LOOP_STATE_RELPATH = "data/runtime/intraday_auto_paper_loop_state.json"
PAPER_ORDERS_DIR = "data/paper_orders"


READY_STRICT = SIGNAL_DAY_TRADE_READY_STRICT
READY_AGGRESSIVE = SIGNAL_DAY_TRADE_READY_AGGRESSIVE


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IntradayPaperIntent:
    """Validated, ready-to-submit paper bracket intent.

    All financial fields are pre-rounded; quantity is at least 1.
    The dataclass is frozen so it cannot be mutated between validation
    and submission.
    """

    strategy_id: str
    symbol: str
    direction: str  # "long" / "short"
    signal_category: str  # READY_STRICT or READY_AGGRESSIVE
    entry_price: float
    stop_price: float
    target_price: float
    planned_rr: float
    quantity: int
    risk_amount: float
    risk_per_trade_pct: float
    reason: str = ""
    source_scan_path: str | None = None
    chart_paths: tuple[str, ...] = ()
    research_flags: tuple[str, ...] = ()
    order_type: str = "LIMIT_BRACKET"
    paper_only: bool = True
    execution_allowed: bool = True
    live_trading_allowed: bool = False
    tif: str = "DAY"
    sizing_audit: Any | None = None

    def to_trade_intent(self) -> TradeIntent:
        side = "BUY" if self.direction == DIRECTION_LONG else "SELL"
        return TradeIntent(
            symbol=self.symbol,
            sec_type="STK",
            side=side,
            quantity=float(self.quantity),
            estimated_price=float(self.entry_price),
            entry_limit_price=float(self.entry_price),
            take_profit_price=float(self.target_price),
            stop_loss_price=float(self.stop_price),
        )

    def as_audit_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "signal_category": self.signal_category,
            "entry": self.entry_price,
            "stop": self.stop_price,
            "target": self.target_price,
            "planned_rr": self.planned_rr,
            "quantity": self.quantity,
            "risk_amount": self.risk_amount,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "reason": self.reason,
            "source_scan_path": self.source_scan_path,
            "chart_paths": list(self.chart_paths),
            "research_flags": list(self.research_flags),
            "order_type": self.order_type,
            "paper_only": True,
            "execution_allowed": True,
            "live_trading_allowed": False,
            "tif": self.tif,
            "sizing_audit": self.sizing_audit,
        }


@dataclass(frozen=True)
class IntradayPaperSubmissionResult:
    """Outcome of one symbol's submission attempt.

    **submitted** is only ``True`` when the bracket is fully accepted
    (``bracket_integrity == "complete"``), not when a child leg is rejected
    (e.g. IBKR Error 110 on stop price). See *submitted_to_broker* for
    "we called ``placeOrder``" without implying protection.
    """

    symbol: str
    submitted: bool
    skipped_reasons: list[str] = field(default_factory=list)
    intent: IntradayPaperIntent | None = None
    order_ids: list[int] = field(default_factory=list)
    error: str | None = None
    audit_path: str | None = None
    # --- Prompt 13J.1: tick + integrity ---
    submitted_to_broker: bool = False
    bracket_integrity: str = "not_submitted"  # complete|incomplete|unknown|not_submitted
    bracket_protected: bool | None = None
    parent_order_id: int | None = None
    stop_order_id: int | None = None
    target_order_id: int | None = None
    parent_order_status: str | None = None
    stop_order_status: str | None = None
    target_order_status: str | None = None
    rejected_legs: list[str] = field(default_factory=list)
    cancelled_legs: list[str] = field(default_factory=list)
    broker_errors: list[str] = field(default_factory=list)
    broker_error_codes: list[int] = field(default_factory=list)
    verified_at_utc: str | None = None
    verify_in_tws_required: bool = False
    tick_meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntradayPaperPassResult:
    """Outcome of one batch pass over a watchlist scan."""

    timestamp_utc: str
    paper_only: bool
    runtime_intraday_on: bool
    kill_switch: bool
    reconciliation_status: str
    config_enabled: bool
    fully_automatic: bool
    symbols_scanned: list[str]
    strict_ready_count: int
    aggressive_ready_count: int
    submissions: list[IntradayPaperSubmissionResult] = field(default_factory=list)
    skipped_reasons: list[str] = field(default_factory=list)
    last_status: str = ""
    last_reason: str = ""
    audit_log_path: str | None = None
    state_file_path: str | None = None

    @property
    def orders_submitted(self) -> int:
        return sum(1 for s in self.submissions if s.submitted)


# ---------------------------------------------------------------------------
# Runtime flag helpers (canonical: same paths the UI writes).
# ---------------------------------------------------------------------------
def is_kill_switch_active(cfg: AppConfig) -> bool:
    return Path(cfg.absolute(KILL_SWITCH_RELPATH)).is_file()


def is_intraday_paper_runtime_enabled(cfg: AppConfig) -> tuple[bool, bool]:
    """Return ``(enabled, explicit_off)`` for the runtime flag file.

    Missing file => ``(False, False)`` and the caller falls back to
    ``trading.intraday_paper.enabled`` / ``fully_automatic``.
    """
    p = Path(cfg.absolute(INTRADAY_AUTO_PAPER_ENABLED_RELPATH))
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


# ---------------------------------------------------------------------------
# Build / validate / submit
# ---------------------------------------------------------------------------
def _f(x: object) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _normalize_scan_item(scan_item: Any) -> dict[str, Any]:
    """Accept dict / IntradayEvaluation / compact summary row."""
    if isinstance(scan_item, dict):
        d = dict(scan_item)
    else:
        to_dict = getattr(scan_item, "to_dict", None)
        if callable(to_dict):
            d = to_dict()
        else:
            d = dict(getattr(scan_item, "__dict__", {}))
    # Compact summary rows put entry/stop/target at the top level;
    # full IntradayEvaluation puts them under trade_plan. Normalise.
    plan = d.get("trade_plan")
    if isinstance(plan, dict):
        for k in ("entry", "stop", "target", "risk_reward"):
            if d.get(k) is None and plan.get(k) is not None:
                d[k] = plan.get(k)
    return d


def build_intraday_paper_intent(
    scan_item: Any,
    account_snapshot: Mapping[str, Any] | None,
    cfg: AppConfig,
    *,
    source_scan_path: str | None = None,
) -> tuple[IntradayPaperIntent | None, list[str]]:
    """Build an ``IntradayPaperIntent`` from a scanner output row.

    ``account_snapshot`` should contain at minimum:
        - ``net_liquidation`` (float, > 0 — required to size)
        - ``mode`` (``"paper"`` — verified again in ``validate_*``)
        - ``block_live_trading`` (bool, must be true)

    Returns ``(intent, [])`` on success or ``(None, [reasons...])`` on
    soft skip. This function never raises for "no signal"-style cases.
    """
    reasons: list[str] = []
    item = _normalize_scan_item(scan_item)

    sym_raw = item.get("symbol")
    symbol = str(sym_raw).upper().strip() if sym_raw else ""
    if not symbol:
        return None, ["scan item missing symbol"]

    category = str(item.get("signal_category") or "").strip()
    if category not in {READY_STRICT, READY_AGGRESSIVE}:
        return None, [f"signal_category={category!r} not paper-eligible"]

    direction = str(item.get("direction") or "").strip().lower()
    if direction not in {DIRECTION_LONG, DIRECTION_SHORT}:
        return None, [f"direction={direction!r} not tradable"]

    ip = cfg.settings.trading.intraday_paper
    if category == READY_STRICT and not ip.allow_strict_entries:
        return None, ["allow_strict_entries=false"]
    if category == READY_AGGRESSIVE and not ip.allow_aggressive_entries:
        return None, ["allow_aggressive_entries=false"]

    entry = _f(item.get("entry"))
    stop = _f(item.get("stop"))
    target = _f(item.get("target"))
    if entry is None or stop is None or target is None or entry <= 0:
        return None, ["missing entry/stop/target on scan item"]

    if direction == DIRECTION_LONG:
        if not (stop < entry < target):
            return None, [
                f"long bracket invalid: need stop<entry<target "
                f"(stop={stop}, entry={entry}, target={target})"
            ]
        per_share_risk = entry - stop
        per_share_reward = target - entry
    else:  # short
        if not (target < entry < stop):
            return None, [
                f"short bracket invalid: need target<entry<stop "
                f"(target={target}, entry={entry}, stop={stop})"
            ]
        per_share_risk = stop - entry
        per_share_reward = entry - target

    if per_share_risk <= 0:
        return None, ["zero per-share risk (entry == stop)"]

    rr = per_share_reward / per_share_risk if per_share_risk > 0 else 0.0
    if rr < float(ip.min_rr):
        return None, [
            f"R/R {rr:.2f} below min_rr {float(ip.min_rr):.2f}"
        ]

    snapshot = dict(account_snapshot or {})
    equity = _f(snapshot.get("net_liquidation")) or _f(snapshot.get("equity")) or 0.0
    if equity <= 0:
        return None, ["account equity unknown / non-positive; cannot size"]

    risk_pct = float(ip.risk_per_trade_pct)
    if risk_pct <= 0:
        return None, ["risk_per_trade_pct must be > 0"]
    risk_dollars = equity * (risk_pct / 100.0)
    risk_q = int(math.floor(risk_dollars / per_share_risk))
    if risk_q < 1:
        return None, [
            f"position size rounds to 0 "
            f"(equity={equity:.0f}, risk%={risk_pct}, per_share={per_share_risk:.4f})"
        ]

    from .intraday_paper_sizing import (  # noqa: PLC0415
        apply_paper_sizing_caps,
        normalize_intraday_paper_tif,
    )

    tif = normalize_intraday_paper_tif(getattr(ip, "tif", None) or "DAY")
    final_q, sizing_audit, cap_skip = apply_paper_sizing_caps(
        cfg,
        entry=float(entry),
        risk_based_quantity=risk_q,
        equity=float(equity),
        per_share_risk=float(per_share_risk),
        ip=ip,
    )
    if cap_skip:
        return None, cap_skip
    qty = int(final_q)

    raw_charts = item.get("chart_paths") or []
    charts = tuple(str(p) for p in raw_charts if p) if isinstance(raw_charts, list) else ()
    raw_flags = item.get("research_flags") or []
    flags = tuple(str(f) for f in raw_flags if f) if isinstance(raw_flags, list) else ()

    intent = IntradayPaperIntent(
        strategy_id=str(item.get("strategy_id") or STRATEGY_KEY),
        symbol=symbol,
        direction=direction,
        signal_category=category,
        entry_price=float(entry),
        stop_price=float(stop),
        target_price=float(target),
        planned_rr=float(rr),
        quantity=int(qty),
        risk_amount=float(qty * per_share_risk),
        risk_per_trade_pct=float(risk_pct),
        reason=str(item.get("explanation_zh") or item.get("next_condition_to_watch") or ""),
        source_scan_path=source_scan_path,
        chart_paths=charts,
        research_flags=flags,
        tif=tif,
        sizing_audit=sizing_audit,
    )
    return intent, []


def validate_intraday_paper_intent(
    intent: IntradayPaperIntent,
    broker_state: Mapping[str, Any],
    cfg: AppConfig,
) -> tuple[bool, list[str]]:
    """Re-check every hard invariant just before submit.

    ``broker_state`` should contain:

    * ``account_mode`` (``"paper"``)
    * ``block_live_trading`` (bool)
    * ``kill_switch_active`` (bool)
    * ``runtime_intraday_on`` (bool)
    * ``reconciliation_passed`` (bool)
    * ``positions``  — iterable of ``(symbol, position_qty)`` tuples or
      objects with ``.symbol`` / ``.position``
    * ``open_orders`` — iterable of objects/dicts with ``symbol``
    * ``open_positions_count`` (int)
    """
    reasons: list[str] = []
    s = cfg.settings
    ip = s.trading.intraday_paper

    if not (intent.paper_only and not intent.live_trading_allowed):
        reasons.append("intent failed paper_only invariant")

    if str(broker_state.get("account_mode", "")).lower() != "paper":
        reasons.append("account.mode is not paper (live blocked)")
    if not bool(broker_state.get("block_live_trading", True)):
        reasons.append("account.block_live_trading must be true")
    if bool(broker_state.get("kill_switch_active", False)):
        reasons.append("kill switch active")
    if not bool(broker_state.get("runtime_intraday_on", False)):
        reasons.append("intraday auto-paper runtime flag is OFF")
    if ip.require_reconciliation_pass and not bool(
        broker_state.get("reconciliation_passed", True)
    ):
        reasons.append("reconciliation failed; new trades blocked")

    # Hard-block: invalid bracket / missing stop or target.
    if intent.stop_price is None or intent.target_price is None:
        reasons.append("missing stop or target (bracket required)")
    if intent.direction == DIRECTION_LONG:
        if not (intent.stop_price < intent.entry_price < intent.target_price):
            reasons.append("long bracket: require stop < entry < target")
    elif intent.direction == DIRECTION_SHORT:
        if not (intent.target_price < intent.entry_price < intent.stop_price):
            reasons.append("short bracket: require target < entry < stop")
    else:
        reasons.append(f"unknown direction {intent.direction!r}")

    if intent.quantity < 1:
        reasons.append("quantity rounds to 0 — risk too small for this stop")

    # Duplicate same-symbol guard.
    sym = intent.symbol.upper()
    if ip.max_one_position_per_symbol:
        for p in broker_state.get("positions") or []:
            ps_sym, ps_qty = _position_pair(p)
            if ps_sym == sym and abs(float(ps_qty or 0)) >= 0.5:
                reasons.append(
                    f"existing position in {sym} — refuse duplicate paper entry"
                )
                break
    for o in broker_state.get("open_orders") or []:
        o_sym = _order_symbol(o)
        if o_sym == sym:
            reasons.append(
                f"open order exists for {sym} — refuse duplicate paper entry"
            )
            break

    open_count = int(broker_state.get("open_positions_count") or 0)
    cap = int(ip.max_concurrent_positions)
    if cap > 0 and open_count >= cap:
        reasons.append(
            f"max_concurrent_positions reached ({open_count} >= {cap})"
        )

    # Direction allow-list at the broker layer.
    if intent.direction == DIRECTION_SHORT and not s.trading.allow_shorting:
        reasons.append("trading.allow_shorting=false; cannot submit short bracket")

    # MKT must never appear here.
    if intent.order_type != "LIMIT_BRACKET":
        reasons.append(f"unsupported order_type {intent.order_type!r}; expect LIMIT_BRACKET")

    return (len(reasons) == 0, reasons)


def _position_pair(p: Any) -> tuple[str, float]:
    if isinstance(p, tuple) and len(p) >= 2:
        return str(p[0]).upper(), float(p[1] or 0)
    if isinstance(p, Mapping):
        return str(p.get("symbol") or "").upper(), float(p.get("position") or 0)
    sym = getattr(p, "symbol", "")
    qty = getattr(p, "position", 0)
    return str(sym or "").upper(), float(qty or 0)


def _order_symbol(o: Any) -> str:
    if isinstance(o, Mapping):
        return str(o.get("symbol") or "").upper()
    return str(getattr(o, "symbol", "") or "").upper()


def _rebuild_intent_after_tick_normalization(
    base: IntradayPaperIntent,
    norm: Any,  # BracketTickNormalization
    equity: float,
    cfg: AppConfig,
) -> tuple[IntradayPaperIntent | None, list[str]]:
    """Recompute size from normalized prices; same risk % rules as the scanner."""
    from .price_ticks import BracketTickNormalization  # noqa: PLC0415

    if not isinstance(norm, BracketTickNormalization) or not norm.valid:
        return None, ["tick normalization not valid"]
    if norm.entry is None or norm.stop is None or norm.target is None:
        return None, ["normalized prices missing"]
    entry = float(norm.entry)
    stop = float(norm.stop)
    target = float(norm.target)
    direction = base.direction
    ip = cfg.settings.trading.intraday_paper
    if direction == DIRECTION_LONG:
        per_share_risk = entry - stop
        per_share_reward = target - entry
    else:
        per_share_risk = stop - entry
        per_share_reward = entry - target
    if per_share_risk <= 0:
        return None, ["zero per-share risk after tick normalization"]
    rr = per_share_reward / per_share_risk
    if rr < float(ip.min_rr):
        return None, [f"R/R {rr:.4f} below min_rr after tick normalization"]
    risk_dollars = equity * (float(base.risk_per_trade_pct) / 100.0)
    risk_q = int(math.floor(risk_dollars / per_share_risk))
    if risk_q < 1:
        return None, [
            f"position size rounds to 0 after tick normalization (per_share_risk={per_share_risk:.4f})"
        ]
    from .intraday_paper_sizing import (  # noqa: PLC0415
        apply_paper_sizing_caps,
        normalize_intraday_paper_tif,
    )

    tif = normalize_intraday_paper_tif(getattr(ip, "tif", None) or "DAY")
    final_q, sizing_audit, cap_skip = apply_paper_sizing_caps(
        cfg,
        entry=float(entry),
        risk_based_quantity=risk_q,
        equity=float(equity),
        per_share_risk=float(per_share_risk),
        ip=ip,
    )
    if cap_skip:
        return None, cap_skip
    qty = int(final_q)
    rebuilt = IntradayPaperIntent(
        strategy_id=base.strategy_id,
        symbol=base.symbol,
        direction=base.direction,
        signal_category=base.signal_category,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        planned_rr=float(rr),
        quantity=int(qty),
        risk_amount=float(qty * per_share_risk),
        risk_per_trade_pct=base.risk_per_trade_pct,
        reason=base.reason,
        source_scan_path=base.source_scan_path,
        chart_paths=base.chart_paths,
        research_flags=base.research_flags,
        tif=tif,
        sizing_audit=sizing_audit,
    )
    return rebuilt, []


def verify_intraday_paper_bracket_trades(
    ib: Any,
    order_ids: list[int],
    *,
    timeout: float = 2.0,
) -> dict[str, Any]:
    """Poll ``ib.trades()`` after a bracket ``placeOrder`` to classify integrity."""
    oids: list[int] = []
    for o in order_ids:
        if o is None:
            continue
        try:
            oids.append(int(o))
        except (TypeError, ValueError):
            continue
    ver_ts = _utc_now_str()
    if len(oids) < 3:
        return {
            "bracket_integrity": "unknown",
            "bracket_protected": None,
            "parent_order_id": oids[0] if oids else None,
            "target_order_id": oids[1] if len(oids) > 1 else None,
            "stop_order_id": oids[2] if len(oids) > 2 else None,
            "parent_order_status": None,
            "target_order_status": None,
            "stop_order_status": None,
            "rejected_legs": [],
            "cancelled_legs": [],
            "broker_errors": ["Fewer than three order ids; cannot verify bracket."],
            "broker_error_codes": [],
            "verified_at_utc": ver_ts,
            "verify_in_tws_required": True,
        }
    parent_id, target_id, stop_id = oids[0], oids[1], oids[2]
    good = {
        "Presubmitted",
        "PreSubmitted",
        "Submitted",
        "PendingSubmit",
        "Filled",
        "PartiallyFilled",
        "ApiPending",
    }
    bad = {"Cancelled", "Inactive", "ApiCancelled"}
    t0 = time.time()
    best_status: dict[int, str] = {}
    all_codes: list[int] = []
    all_errs: list[str] = []
    while time.time() - t0 < timeout:
        for tr in ib.trades() if hasattr(ib, "trades") else []:
            try:
                oid = int(
                    getattr(getattr(tr, "order", object()), "orderId", 0) or 0
                )
            except (TypeError, ValueError):
                continue
            if oid not in oids:
                continue
            st = (
                str(getattr(getattr(tr, "orderStatus", None), "status", None) or "")
            )
            best_status[oid] = st
            for le in list(getattr(tr, "log", None) or []):
                ec = int(getattr(le, "errorCode", 0) or 0)
                if ec:
                    all_codes.append(ec)
                    all_errs.append(
                        f"order {oid} error {ec}: {getattr(le, 'message', '')!s}"
                    )
        if hasattr(ib, "sleep"):
            ib.sleep(0.25)  # type: ignore[operator]
        else:
            time.sleep(0.25)

    pstat = best_status.get(parent_id)
    tstat = best_status.get(target_id)
    sstat = best_status.get(stop_id)
    for oid in oids:
        if oid not in best_status:
            return {
                "bracket_integrity": "unknown",
                "bracket_protected": None,
                "parent_order_id": parent_id,
                "target_order_id": target_id,
                "stop_order_id": stop_id,
                "parent_order_status": pstat,
                "target_order_status": tstat,
                "stop_order_status": sstat,
                "rejected_legs": [],
                "cancelled_legs": [],
                "broker_errors": all_errs
                or ["Could not read trade status for all bracket legs after submission."],
                "broker_error_codes": sorted(set(all_codes)),
                "verified_at_utc": ver_ts,
                "verify_in_tws_required": True,
            }

    def _is_bad(st: str | None) -> bool:
        return st is not None and st in bad

    cancelled: list[str] = []
    for label, st in (
        ("parent", pstat),
        ("target", tstat),
        ("stop", sstat),
    ):
        if _is_bad(st):
            cancelled.append(label)
    if 110 in all_codes and "stop" not in cancelled:
        cancelled.append("stop")

    incomplete = (
        any(_is_bad(st) for st in (pstat, tstat, sstat))
        or (110 in all_codes)
        or (10349 in all_codes)
    )
    if incomplete:
        msg = "Submitted to broker, but bracket protection is incomplete."
        rej = [x for x in cancelled if x in {"stop", "parent", "target"}]
        if 110 in all_codes and "stop" not in rej:
            rej.append("stop")
        if 10349 in all_codes and not rej:
            rej.extend(["parent", "target", "stop"])
        return {
            "bracket_integrity": "incomplete",
            "bracket_protected": False,
            "parent_order_id": parent_id,
            "target_order_id": target_id,
            "stop_order_id": stop_id,
            "parent_order_status": pstat,
            "target_order_status": tstat,
            "stop_order_status": sstat,
            "rejected_legs": rej,
            "cancelled_legs": [x for x in cancelled if x in {"parent", "target", "stop"}],
            "broker_errors": [msg] + all_errs,
            "broker_error_codes": sorted(set(all_codes)),
            "verified_at_utc": ver_ts,
            "verify_in_tws_required": bool(110 in all_codes or 10349 in all_codes),
        }
    if (
        pstat in good
        and tstat in good
        and sstat in good
    ):
        return {
            "bracket_integrity": "complete",
            "bracket_protected": True,
            "parent_order_id": parent_id,
            "target_order_id": target_id,
            "stop_order_id": stop_id,
            "parent_order_status": pstat,
            "target_order_status": tstat,
            "stop_order_status": sstat,
            "rejected_legs": [],
            "cancelled_legs": [],
            "broker_errors": [],
            "broker_error_codes": [],
            "verified_at_utc": ver_ts,
            "verify_in_tws_required": False,
        }
    return {
        "bracket_integrity": "unknown",
        "bracket_protected": None,
        "parent_order_id": parent_id,
        "target_order_id": target_id,
        "stop_order_id": stop_id,
        "parent_order_status": pstat,
        "target_order_status": tstat,
        "stop_order_status": sstat,
        "rejected_legs": [],
        "cancelled_legs": [],
        "broker_errors": all_errs,
        "broker_error_codes": sorted(set(all_codes)),
        "verified_at_utc": ver_ts,
        "verify_in_tws_required": True,
    }


def submit_intraday_paper_bracket(
    intent: IntradayPaperIntent,
    broker_state: Mapping[str, Any],
    cfg: AppConfig,
    *,
    broker: Any,
    journal: Journal | None = None,
) -> IntradayPaperSubmissionResult:
    """Submit (or skip) one paper bracket via :class:`bot.broker.Broker`.

    Min-tick normalization (Prompt 13J.1) runs **before** ``placeOrder``.
    ``submitted`` is only true when post-trade verification shows all three
    legs in an acceptable state (see ``bracket_integrity``).
    """
    from .price_ticks import (  # noqa: PLC0415
        BracketTickNormalization,
        MIN_TICK_US_STOCK_DEFAULT,
        normalize_bracket_prices,
    )

    tick_meta: dict[str, Any] = {
        "min_tick": None,
        "min_tick_source": None,
        "min_tick_fetch_error": None,
        "min_tick_fallback_warning": None,
    }
    ok, reasons = validate_intraday_paper_intent(intent, broker_state, cfg)
    if not ok:
        return IntradayPaperSubmissionResult(
            symbol=intent.symbol,
            submitted=False,
            submitted_to_broker=False,
            bracket_integrity="not_submitted",
            skipped_reasons=reasons,
            intent=intent,
            tick_meta=tick_meta,
        )
    from ..broker import Broker, LiveTradingBlocked, ManualConfirmationRequired, TradingDisabled

    if Broker is None or not hasattr(broker, "place_order"):
        return IntradayPaperSubmissionResult(
            symbol=intent.symbol,
            submitted=False,
            submitted_to_broker=False,
            skipped_reasons=["invalid broker"],
            intent=intent,
            tick_meta=tick_meta,
        )
    if not hasattr(broker, "client") or not hasattr(
        broker.client, "fetch_stock_min_tick"
    ):
        return IntradayPaperSubmissionResult(
            symbol=intent.symbol,
            submitted=False,
            submitted_to_broker=False,
            skipped_reasons=["broker client missing fetch_stock_min_tick"],
            intent=intent,
            tick_meta=tick_meta,
        )
    mti = broker.client.fetch_stock_min_tick(intent.symbol)
    min_t = mti.get("min_tick", MIN_TICK_US_STOCK_DEFAULT)
    tick_meta["min_tick"] = str(min_t)
    tick_meta["min_tick_source"] = mti.get("min_tick_source")
    tick_meta["min_tick_fetch_error"] = mti.get("min_tick_fetch_error")
    if mti.get("min_tick_source") == "fallback_us_stock_0.01":
        tick_meta["min_tick_fallback_warning"] = "min_tick_fallback_used"

    ip = cfg.settings.trading.intraday_paper
    norm: BracketTickNormalization = normalize_bracket_prices(
        intent.direction,
        intent.entry_price,
        intent.stop_price,
        intent.target_price,
        min_t,
        float(ip.min_rr),
    )
    tick_meta["original_entry"] = float(norm.original_entry)
    tick_meta["original_stop"] = float(norm.original_stop)
    tick_meta["original_target"] = float(norm.original_target)
    tick_meta["entry"] = float(norm.entry) if norm.entry is not None else None
    tick_meta["stop"] = float(norm.stop) if norm.stop is not None else None
    tick_meta["target"] = float(norm.target) if norm.target is not None else None
    tick_meta["planned_rr_before"] = (
        float(norm.planned_rr_before) if norm.planned_rr_before is not None else None
    )
    tick_meta["planned_rr_after"] = (
        float(norm.planned_rr_after) if norm.planned_rr_after is not None else None
    )
    tick_meta["tick_rounding_applied"] = bool(norm.tick_rounding_applied)
    for k in ("original_entry_f", "original_stop_f", "original_target_f"):
        if hasattr(norm, k):
            tick_meta[k] = float(getattr(norm, k, 0.0) or 0.0)
    if not norm.valid:
        rsn = list(norm.rejection_reasons) or ["invalid_after_tick_rounding"]
        return IntradayPaperSubmissionResult(
            symbol=intent.symbol,
            submitted=False,
            submitted_to_broker=False,
            bracket_integrity="not_submitted",
            skipped_reasons=rsn,
            intent=intent,
            tick_meta=tick_meta,
        )

    equity = _f(broker_state.get("net_liquidation")) or _f(
        broker_state.get("equity")
    )
    if equity is None or float(equity) <= 0:
        return IntradayPaperSubmissionResult(
            symbol=intent.symbol,
            submitted=False,
            submitted_to_broker=False,
            bracket_integrity="not_submitted",
            skipped_reasons=["net_liquidation / equity not available for ret sizing"],
            intent=intent,
            tick_meta=tick_meta,
        )
    rebuilt, rsz = _rebuild_intent_after_tick_normalization(
        intent, norm, float(equity), cfg
    )
    if rebuilt is not None:
        tif_s = getattr(rebuilt, "tif", None) or "DAY"
        if rebuilt.sizing_audit:
            tick_meta["sizing_audit"] = dict(rebuilt.sizing_audit)
        tick_meta["tif"] = tif_s
        tick_meta["parent_tif"] = tif_s
        tick_meta["stop_tif"] = tif_s
        tick_meta["target_tif"] = tif_s
        est_n = None
        if isinstance(rebuilt.sizing_audit, dict):
            est_n = rebuilt.sizing_audit.get("estimated_notional")
        if est_n is None:
            est_n = float(rebuilt.quantity) * float(rebuilt.entry_price)
        tick_meta["estimated_notional"] = float(est_n)
    if rebuilt is None:
        rsn2 = list(rsz) or ["rebuild after tick failed"]
        if "rr_below_min" not in " ".join(rsn2) and "below min" in " ".join(rsn2):
            rsn2.append("rr_below_min_after_rounding")
        if not any("invalid" in x for x in rsn2) and "tick" in " ".join(rsn2).lower():
            rsn2.insert(0, "invalid_after_tick_rounding")
        return IntradayPaperSubmissionResult(
            symbol=intent.symbol,
            submitted=False,
            submitted_to_broker=False,
            bracket_integrity="not_submitted",
            skipped_reasons=rsn2,
            intent=intent,
            tick_meta=tick_meta,
        )
    v_ok, vrs = validate_intraday_paper_intent(rebuilt, broker_state, cfg)
    if not v_ok:
        return IntradayPaperSubmissionResult(
            symbol=intent.symbol,
            submitted=False,
            submitted_to_broker=False,
            bracket_integrity="not_submitted",
            skipped_reasons=vrs,
            intent=rebuilt,
            tick_meta=tick_meta,
        )

    open_count = int(broker_state.get("open_positions_count") or 0)
    recon_ok = bool(broker_state.get("reconciliation_passed", True))
    try:
        ticket = broker.place_order(
            rebuilt.to_trade_intent(),
            dry_run=cfg.settings.trading.intraday_paper.dry_run,
            confirmed=False,
            reconciliation_passed=recon_ok,
            account_equity=equity,
            open_positions_count=open_count,
            intraday_paper_bracket=True,
        )
    except (TradingDisabled, LiveTradingBlocked, ManualConfirmationRequired) as exc:
        return IntradayPaperSubmissionResult(
            symbol=intent.symbol,
            submitted=False,
            submitted_to_broker=False,
            bracket_integrity="not_submitted",
            skipped_reasons=[type(exc).__name__],
            intent=rebuilt,
            error=str(exc),
            tick_meta=tick_meta,
        )
    if ticket.dry_run:
        return IntradayPaperSubmissionResult(
            symbol=intent.symbol,
            submitted=False,
            submitted_to_broker=False,
            bracket_integrity="not_submitted",
            skipped_reasons=["dry-run"],
            intent=rebuilt,
            tick_meta=tick_meta,
        )
    detail = ticket.intraday_paper or {}
    raw_oids = detail.get("order_ids") if isinstance(detail, dict) else None
    oids: list[int] = []
    if isinstance(raw_oids, list):
        for o in raw_oids:
            if o is None:
                continue
            try:
                oids.append(int(o))
            except (TypeError, ValueError):
                continue
    if not oids:
        return IntradayPaperSubmissionResult(
            symbol=intent.symbol,
            submitted=False,
            submitted_to_broker=False,
            bracket_integrity="incomplete",
            skipped_reasons=["broker returned no order ids after placeOrder"],
            intent=rebuilt,
            order_ids=oids,
            tick_meta=tick_meta,
        )
    ib = getattr(broker.client, "_ib", None)
    integ: dict[str, Any] = {
        "bracket_integrity": "unknown",
        "bracket_protected": None,
    }
    if ib is not None:
        integ = verify_intraday_paper_bracket_trades(ib, oids, timeout=2.0)
    bint = str(integ.get("bracket_integrity") or "unknown")
    wprot = integ.get("bracket_protected")
    wprot_typed: bool | None
    if wprot is None:
        wprot_typed = None
    else:
        wprot_typed = bool(wprot)
    submitted_ok = bint == "complete"
    sk: list[str] = []
    if not submitted_ok:
        sk = list(
            integ.get("broker_errors", [])
        ) or [
            "Paper order reached broker, but bracket protection is incomplete. Verify/cancel in TWS."
        ]
    return IntradayPaperSubmissionResult(
        symbol=intent.symbol,
        submitted=submitted_ok,
        submitted_to_broker=True,
        bracket_integrity=bint,
        bracket_protected=wprot_typed,
        parent_order_id=integ.get("parent_order_id"),
        stop_order_id=integ.get("stop_order_id"),
        target_order_id=integ.get("target_order_id"),
        parent_order_status=(
            str(integ.get("parent_order_status"))
            if integ.get("parent_order_status") is not None
            else None
        ),
        stop_order_status=(
            str(integ.get("stop_order_status"))
            if integ.get("stop_order_status") is not None
            else None
        ),
        target_order_status=(
            str(integ.get("target_order_status"))
            if integ.get("target_order_status") is not None
            else None
        ),
        rejected_legs=list(integ.get("rejected_legs") or []),
        cancelled_legs=list(integ.get("cancelled_legs") or []),
        broker_errors=list(integ.get("broker_errors") or []),
        broker_error_codes=[int(c) for c in (integ.get("broker_error_codes") or [])],
        verified_at_utc=str(integ.get("verified_at_utc") or ""),
        verify_in_tws_required=bool(integ.get("verify_in_tws_required", False)),
        skipped_reasons=sk,
        intent=rebuilt,
        order_ids=oids,
        tick_meta=tick_meta,
    )


# ---------------------------------------------------------------------------
# Audit log + state file
# ---------------------------------------------------------------------------
def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _audit_log_path(cfg: AppConfig) -> Path:
    p = Path(cfg.absolute(PAPER_ORDERS_DIR))
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{_utc_today()}-intraday-paper-orders.jsonl"


def _append_audit_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _state_file_path(cfg: AppConfig) -> Path:
    p = Path(cfg.absolute(INTRADAY_LOOP_STATE_RELPATH))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _write_state_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def serialize_paper_submission(sub: IntradayPaperSubmissionResult) -> dict[str, Any]:
    """CLI / API JSON view of one submission (Prompt 13J.1)."""
    return {
        "symbol": sub.symbol,
        "submitted": sub.submitted,
        "submitted_to_broker": sub.submitted_to_broker,
        "skipped_reasons": list(sub.skipped_reasons),
        "order_ids": list(sub.order_ids),
        "intent": sub.intent.as_audit_dict() if sub.intent else None,
        "error": sub.error,
        "bracket_integrity": sub.bracket_integrity,
        "bracket_protected": sub.bracket_protected,
        "parent_order_id": sub.parent_order_id,
        "stop_order_id": sub.stop_order_id,
        "target_order_id": sub.target_order_id,
        "parent_order_status": sub.parent_order_status,
        "stop_order_status": sub.stop_order_status,
        "target_order_status": sub.target_order_status,
        "rejected_legs": list(sub.rejected_legs),
        "cancelled_legs": list(sub.cancelled_legs),
        "broker_errors": list(sub.broker_errors),
        "broker_error_codes": list(sub.broker_error_codes),
        "verified_at_utc": sub.verified_at_utc,
        "verify_in_tws_required": sub.verify_in_tws_required,
        "tick_meta": dict(sub.tick_meta),
    }


def _record_submission_audit(
    cfg: AppConfig,
    sub: IntradayPaperSubmissionResult,
) -> str:
    intent = sub.intent
    if intent is None:
        return ""
    tm = dict(sub.tick_meta)
    row: dict[str, Any] = {
        "timestamp": _utc_now_str(),
        "strategy_id": intent.strategy_id,
        "symbol": intent.symbol,
        "direction": intent.direction,
        "signal_category": intent.signal_category,
        "submitted": sub.submitted,
        "submitted_to_broker": sub.submitted_to_broker,
        "skipped_reasons": list(sub.skipped_reasons),
        "entry": intent.entry_price,
        "stop": intent.stop_price,
        "target": intent.target_price,
        "planned_rr": intent.planned_rr,
        "quantity": intent.quantity,
        "order_ids": list(sub.order_ids),
        "paper_only": True,
        "live_trading_allowed": False,
        "source_scan_path": intent.source_scan_path,
        "chart_paths": list(intent.chart_paths),
        "original_entry": tm.get("original_entry"),
        "original_stop": tm.get("original_stop"),
        "original_target": tm.get("original_target"),
        "min_tick": tm.get("min_tick"),
        "min_tick_source": tm.get("min_tick_source"),
        "min_tick_fetch_error": tm.get("min_tick_fetch_error"),
        "min_tick_fallback_warning": tm.get("min_tick_fallback_warning"),
        "planned_rr_before": tm.get("planned_rr_before"),
        "planned_rr_after": tm.get("planned_rr_after"),
        "tick_rounding_applied": tm.get("tick_rounding_applied"),
        "tif": tm.get("tif") or intent.tif,
        "parent_tif": tm.get("parent_tif") or intent.tif,
        "stop_tif": tm.get("stop_tif") or intent.tif,
        "target_tif": tm.get("target_tif") or intent.tif,
        "estimated_notional": tm.get("estimated_notional")
        or (
            float(intent.entry_price) * float(intent.quantity)
            if intent.entry_price
            else None
        ),
        "sizing_audit": intent.sizing_audit
        or tm.get("sizing_audit"),
        "bracket_integrity": sub.bracket_integrity,
        "bracket_protected": sub.bracket_protected,
        "broker_errors": list(sub.broker_errors),
        "broker_error_codes": list(sub.broker_error_codes),
        "verified_at_utc": sub.verified_at_utc,
        "verify_in_tws_required": sub.verify_in_tws_required,
    }
    if sub.error:
        row["error"] = sub.error
    p = _audit_log_path(cfg)
    _append_audit_row(p, row)
    return str(p)


# ---------------------------------------------------------------------------
# One-pass driver
# ---------------------------------------------------------------------------
def _resolve_runtime_state(cfg: AppConfig) -> dict[str, Any]:
    ip = cfg.settings.trading.intraday_paper
    runtime_on, explicit_off = is_intraday_paper_runtime_enabled(cfg)
    config_enabled = bool(ip.enabled)
    fully_auto = bool(ip.fully_automatic)
    # If config is enabled and not explicitly turned off at runtime, we
    # honour fully_automatic to allow the loop to keep working without an
    # operator toggling the file.
    effective_on = (
        runtime_on
        or (config_enabled and fully_auto and not explicit_off)
    )
    return {
        "config_enabled": config_enabled,
        "fully_automatic": fully_auto,
        "runtime_intraday_on": runtime_on,
        "runtime_intraday_explicit_off": explicit_off,
        "effective_on": effective_on,
        "kill_switch": is_kill_switch_active(cfg),
    }


def _connect_broker(cfg: AppConfig, journal: Journal) -> tuple[Any, Any]:
    """Lazy broker connect; only called when we will actually try to submit."""
    from ..broker import Broker  # noqa: PLC0415
    from ..ibkr_client import IBKRClient  # noqa: PLC0415

    client = IBKRClient(cfg)
    client.connect(readonly=False)
    broker = Broker(cfg, client, journal=journal)
    return client, broker


def _build_broker_state(client: Any, *, kill_switch: bool, runtime_on: bool, recon_ok: bool, cfg: AppConfig) -> dict[str, Any]:
    summ = client.get_account_summary()
    equity = 0.0
    for a in summ:
        if a.net_liquidation and float(a.net_liquidation) > 0:
            equity = float(a.net_liquidation)
            break
    positions = list(client.get_positions())
    open_orders = list(client.get_open_orders())
    open_count = sum(1 for p in positions if abs(float(getattr(p, "position", 0) or 0)) >= 1e-4)
    return {
        "account_mode": cfg.settings.account.mode,
        "block_live_trading": bool(cfg.settings.account.block_live_trading),
        "kill_switch_active": bool(kill_switch),
        "runtime_intraday_on": bool(runtime_on),
        "reconciliation_passed": bool(recon_ok),
        "net_liquidation": equity,
        "positions": positions,
        "open_orders": open_orders,
        "open_positions_count": open_count,
    }


def _scan_watchlist(
    cfg: AppConfig,
    journal: Journal,
    *,
    source: str,
    limit: int,
    chart: bool,
) -> dict[str, Any]:
    from ..strategies.ict_smc_intraday import (  # noqa: PLC0415
        IntradayRiskConfig,
        scan_watchlist_with_ibkr,
    )

    return scan_watchlist_with_ibkr(
        cfg,
        journal,
        use_ibkr=True,
        chart=chart,
        telegram=False,
        limit=limit,
        source=source,
        save_json=True,
        risk_cfg=IntradayRiskConfig(),
    )


def run_intraday_paper_pass(
    cfg: AppConfig,
    journal: Journal,
    *,
    source: str = "dynamic",
    limit: int = 20,
    telegram: bool = False,
    chart: bool = False,
    require_reconciliation: bool | None = None,
) -> IntradayPaperPassResult:
    """One end-to-end pass: preflight → scan → submit ready candidates.

    Never raises; it always returns a populated
    :class:`IntradayPaperPassResult` and writes the loop state file.
    """
    ts = _utc_now_str()
    state_path = _state_file_path(cfg)
    runtime = _resolve_runtime_state(cfg)
    audit_path: str | None = None
    submissions: list[IntradayPaperSubmissionResult] = []
    skipped: list[str] = []
    last_status = "skipped"
    last_reason = ""
    symbols_scanned: list[str] = []
    strict_count = 0
    aggr_count = 0
    recon_status = "skipped"

    # ---- preflight (do not connect IBKR yet) ------------------------------
    if runtime["kill_switch"]:
        last_reason = "kill switch active"
        skipped.append(last_reason)
    elif cfg.settings.account.mode != "paper":
        last_reason = f"account.mode={cfg.settings.account.mode!r} (live blocked)"
        skipped.append(last_reason)
    elif not runtime["effective_on"]:
        if runtime["runtime_intraday_explicit_off"]:
            last_reason = "runtime intraday flag explicitly OFF"
        elif not runtime["config_enabled"]:
            last_reason = "trading.intraday_paper.enabled=false"
        else:
            last_reason = "intraday auto-paper runtime flag missing"
        skipped.append(last_reason)
    elif not cfg.settings.trading.intraday_paper.bracket_required:
        last_reason = "intraday_paper.bracket_required=false (config invariant violated)"
        skipped.append(last_reason)

    if skipped:
        result = IntradayPaperPassResult(
            timestamp_utc=ts,
            paper_only=True,
            runtime_intraday_on=runtime["effective_on"],
            kill_switch=runtime["kill_switch"],
            reconciliation_status=recon_status,
            config_enabled=runtime["config_enabled"],
            fully_automatic=runtime["fully_automatic"],
            symbols_scanned=symbols_scanned,
            strict_ready_count=0,
            aggressive_ready_count=0,
            submissions=submissions,
            skipped_reasons=skipped,
            last_status=last_status,
            last_reason=last_reason,
            audit_log_path=str(_audit_log_path(cfg)) if Path(cfg.absolute(PAPER_ORDERS_DIR)).exists() else None,
            state_file_path=str(state_path),
        )
        _write_state(state_path, result, runtime, recon_status)
        if telegram:
            _maybe_send_critical_skip_telegram(cfg, journal, last_reason)
        return result

    # ---- run reconciliation if configured ---------------------------------
    require_recon = (
        cfg.settings.trading.intraday_paper.require_reconciliation_pass
        if require_reconciliation is None
        else bool(require_reconciliation)
    )
    recon_ok = True
    if require_recon:
        try:
            from ..reconciliation import reconcile  # noqa: PLC0415

            client_for_recon, _ = _connect_broker(cfg, journal)
            try:
                report = reconcile(client_for_recon, journal)
                recon_ok = bool(getattr(report, "ok", True))
                recon_status = "passed" if recon_ok else "failed"
            finally:
                try:
                    client_for_recon.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            recon_ok = False
            recon_status = f"error: {exc}"
        if not recon_ok:
            last_reason = f"reconciliation status={recon_status}"
            skipped.append(last_reason)
            result = IntradayPaperPassResult(
                timestamp_utc=ts,
                paper_only=True,
                runtime_intraday_on=True,
                kill_switch=False,
                reconciliation_status=recon_status,
                config_enabled=runtime["config_enabled"],
                fully_automatic=runtime["fully_automatic"],
                symbols_scanned=symbols_scanned,
                strict_ready_count=0,
                aggressive_ready_count=0,
                submissions=submissions,
                skipped_reasons=skipped,
                last_status=last_status,
                last_reason=last_reason,
                state_file_path=str(state_path),
            )
            _write_state(state_path, result, runtime, recon_status)
            if telegram:
                _maybe_send_critical_skip_telegram(cfg, journal, last_reason)
            return result

    # ---- scan watchlist ---------------------------------------------------
    try:
        summary = _scan_watchlist(
            cfg, journal, source=source, limit=limit, chart=chart,
        )
    except FileNotFoundError as exc:
        last_reason = f"watchlist not built: {exc}"
        skipped.append(last_reason)
        result = IntradayPaperPassResult(
            timestamp_utc=ts,
            paper_only=True,
            runtime_intraday_on=True,
            kill_switch=False,
            reconciliation_status=recon_status,
            config_enabled=runtime["config_enabled"],
            fully_automatic=runtime["fully_automatic"],
            symbols_scanned=symbols_scanned,
            strict_ready_count=0,
            aggressive_ready_count=0,
            submissions=submissions,
            skipped_reasons=skipped,
            last_status="error",
            last_reason=last_reason,
            state_file_path=str(state_path),
        )
        _write_state(state_path, result, runtime, recon_status)
        return result

    items = list(summary.get("items") or [])
    symbols_scanned = [str(it.get("symbol") or "").upper() for it in items if it.get("symbol")]
    strict_count = int(summary.get("counts", {}).get(READY_STRICT, 0))
    aggr_count = int(summary.get("counts", {}).get(READY_AGGRESSIVE, 0))
    saved = summary.get("_saved_per_symbol_paths") or []
    saved_map: dict[str, str] = {}
    for p in saved:
        try:
            stem = Path(str(p)).name  # YYYY-MM-DD-SYM-intraday-smc.json
            sym = stem.split("-", 3)[3].rsplit("-", 2)[0].upper()
            saved_map[sym] = str(p)
        except Exception:  # noqa: BLE001
            continue

    # ---- connect broker for the actual submissions ------------------------
    eligible_items = [
        it for it in items
        if str(it.get("signal_category") or "") in {READY_STRICT, READY_AGGRESSIVE}
    ]
    if not eligible_items:
        last_status = "no_signals"
        last_reason = "no READY_STRICT or READY_AGGRESSIVE signals"
        result = IntradayPaperPassResult(
            timestamp_utc=ts,
            paper_only=True,
            runtime_intraday_on=True,
            kill_switch=False,
            reconciliation_status=recon_status,
            config_enabled=runtime["config_enabled"],
            fully_automatic=runtime["fully_automatic"],
            symbols_scanned=symbols_scanned,
            strict_ready_count=strict_count,
            aggressive_ready_count=aggr_count,
            submissions=submissions,
            skipped_reasons=skipped,
            last_status=last_status,
            last_reason=last_reason,
            state_file_path=str(state_path),
        )
        _write_state(state_path, result, runtime, recon_status)
        return result

    client: Any = None
    try:
        client, broker = _connect_broker(cfg, journal)
        broker_state = _build_broker_state(
            client,
            kill_switch=False,
            runtime_on=True,
            recon_ok=recon_ok,
            cfg=cfg,
        )
        for it in eligible_items:
            sym = str(it.get("symbol") or "").upper()
            src = saved_map.get(sym)
            account_snapshot = {
                "net_liquidation": broker_state.get("net_liquidation"),
                "mode": cfg.settings.account.mode,
                "block_live_trading": cfg.settings.account.block_live_trading,
            }
            intent, build_err = build_intraday_paper_intent(
                it, account_snapshot, cfg, source_scan_path=src,
            )
            if intent is None:
                sub = IntradayPaperSubmissionResult(
                    symbol=sym,
                    submitted=False,
                    skipped_reasons=build_err,
                )
                submissions.append(sub)
                skipped.extend(build_err)
                continue
            sub = submit_intraday_paper_bracket(
                intent, broker_state, cfg, broker=broker, journal=journal,
            )
            submissions.append(sub)
            audit_path = _record_submission_audit(cfg, sub)
            if not sub.submitted:
                skipped.extend(sub.skipped_reasons)
            if sub.submitted and telegram:
                _maybe_send_submitted_telegram(cfg, journal, sub)
            elif sub.submitted_to_broker and (not sub.submitted) and telegram:
                _maybe_send_incomplete_bracket_telegram(cfg, journal, sub)
            elif (not sub.submitted) and (not sub.submitted_to_broker) and telegram:
                _maybe_send_critical_skip_telegram(
                    cfg, journal, ", ".join(sub.skipped_reasons), symbol=sym,
                )
    except Exception as exc:  # noqa: BLE001
        last_status = "error"
        last_reason = f"unexpected error: {exc}"
        skipped.append(last_reason)
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    if last_status == "skipped":
        last_status = "ok" if any(s.submitted for s in submissions) else "skipped_after_scan"
        if not last_reason:
            last_reason = "submitted={}/{}".format(
                sum(1 for s in submissions if s.submitted), len(submissions),
            )

    result = IntradayPaperPassResult(
        timestamp_utc=ts,
        paper_only=True,
        runtime_intraday_on=True,
        kill_switch=False,
        reconciliation_status=recon_status,
        config_enabled=runtime["config_enabled"],
        fully_automatic=runtime["fully_automatic"],
        symbols_scanned=symbols_scanned,
        strict_ready_count=strict_count,
        aggressive_ready_count=aggr_count,
        submissions=submissions,
        skipped_reasons=skipped,
        last_status=last_status,
        last_reason=last_reason,
        audit_log_path=audit_path or str(_audit_log_path(cfg))
        if Path(cfg.absolute(PAPER_ORDERS_DIR)).exists() else None,
        state_file_path=str(state_path),
    )
    _write_state(state_path, result, runtime, recon_status)
    return result


def _write_state(
    path: Path,
    result: IntradayPaperPassResult,
    runtime: Mapping[str, Any],
    recon_status: str,
) -> None:
    """Atomically write loop state JSON; safe to call from any worker."""
    _bscore = {"complete": 0, "unknown": 1, "not_submitted": 2, "incomplete": 3}
    worst = "not_submitted"
    for s in result.submissions:
        bi = str(getattr(s, "bracket_integrity", "not_submitted") or "not_submitted")
        if _bscore.get(bi, 1) > _bscore.get(worst, 0):
            worst = bi
    last_inc = any(
        getattr(s, "bracket_integrity", "") == "incomplete"
        for s in result.submissions
    ) or any(
        bool(getattr(s, "submitted_to_broker", False)) and (not s.submitted)
        for s in result.submissions
    )
    payload = {
        "last_cycle_utc": result.timestamp_utc,
        "cycles": _bump_cycle_count(path),
        "last_status": result.last_status,
        "last_reason": result.last_reason,
        "last_symbols_scanned": list(result.symbols_scanned),
        "strict_ready_count": int(result.strict_ready_count),
        "aggressive_ready_count": int(result.aggressive_ready_count),
        "orders_submitted": int(result.orders_submitted),
        "last_worst_bracket_integrity": worst,
        "last_bracket_incomplete": bool(last_inc),
        "skipped_reasons": list(dict.fromkeys(result.skipped_reasons))[:50],
        "kill_switch": bool(result.kill_switch),
        "runtime_intraday_on": bool(result.runtime_intraday_on),
        "reconciliation_status": str(recon_status),
        "paper_only": True,
        "config_enabled": bool(runtime.get("config_enabled")),
        "fully_automatic": bool(runtime.get("fully_automatic")),
        "last_heartbeat_ts": time.time(),
    }
    _write_state_atomic(path, payload)


def _bump_cycle_count(path: Path) -> int:
    if not path.exists():
        return 1
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        return int(old.get("cycles") or 0) + 1
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 1


# ---------------------------------------------------------------------------
# Telegram digest (Chinese)
# ---------------------------------------------------------------------------
def format_intraday_paper_digest_zh(result: IntradayPaperPassResult) -> str:
    """Build a Chinese Telegram digest for the loop / one-pass output.

    HTML-escaped, suitable to pass directly to
    :func:`bot.notifications.send_telegram_message`.
    """
    from html import escape

    lines: list[str] = []
    lines.append("<b>" + escape("【ICT/SMC 日内 纸面】Intraday Paper Pass") + "</b>")
    lines.append("<pre>")
    lines.append(f"时间(UTC): {escape(result.timestamp_utc)}")
    lines.append(f"扫描数: {len(result.symbols_scanned)}")
    lines.append(f"严格READY: {result.strict_ready_count}")
    lines.append(f"放宽READY: {result.aggressive_ready_count}")
    lines.append(f"已提交: {result.orders_submitted}")
    lines.append(f"杀键: {'ON' if result.kill_switch else 'off'}")
    lines.append(f"运行时开关: {'ON' if result.runtime_intraday_on else 'off'}")
    lines.append(f"对账: {escape(result.reconciliation_status or '-')}")
    lines.append(f"状态: {escape(result.last_status or '-')}")
    if result.last_reason:
        lines.append(f"原因: {escape(result.last_reason)}")
    if result.submissions:
        lines.append("")
        for sub in result.submissions[:8]:
            it = sub.intent
            if sub.submitted:
                tag = "OK"
            elif sub.submitted_to_broker:
                tag = "INCOMPLETE"
            else:
                tag = "skip"
            sym = escape(sub.symbol)
            if it is None:
                lines.append(f"  [{tag}] {sym} ({escape(', '.join(sub.skipped_reasons))})")
                continue
            mode = "严格" if it.signal_category == READY_STRICT else "放宽"
            dirn = "多" if it.direction == DIRECTION_LONG else "空"
            bi = escape(str(getattr(sub, "bracket_integrity", "")))
            lines.append(
                f"  [{tag}] {sym}  {dirn}/{mode}  integ={bi}  "
                f"E={it.entry_price:.2f} SL={it.stop_price:.2f} "
                f"TP={it.target_price:.2f} R/R={it.planned_rr:.2f} "
                f"qty={it.quantity}  ids={list(sub.order_ids)}"
            )
    lines.append("</pre>")
    lines.append(escape("仅纸面账户; 不会触发实盘交易; 仅 LIMIT bracket."))
    return "\n".join(lines)


def _maybe_send_submitted_telegram(
    cfg: AppConfig, journal: Journal, sub: IntradayPaperSubmissionResult,
) -> None:
    if not cfg.telegram.is_configured or sub.intent is None:
        return
    from html import escape

    from ..notifications import send_telegram_message

    it = sub.intent
    mode = "严格" if it.signal_category == READY_STRICT else "放宽"
    dirn = "多" if it.direction == DIRECTION_LONG else "空"
    tm = sub.tick_meta or {}
    body = (
        "<b>" + escape("【Paper Trade Submitted】纸面 Bracket 已提交") + "</b>\n"
        + "<pre>"
        + escape(f"strategy: {it.strategy_id}\n")
        + escape(f"symbol:   {it.symbol}\n")
        + escape(f"direction:{dirn}\n")
        + escape(f"mode:     {mode} ({it.signal_category})\n")
        + escape(f"entry:    {it.entry_price}\n")
        + escape(f"stop:     {it.stop_price}\n")
        + escape(f"target:   {it.target_price}\n")
        + escape(f"R/R:      {it.planned_rr:.2f}\n")
        + escape(f"qty:      {it.quantity}\n")
        + escape(f"order_ids:{sub.order_ids}\n")
        + escape(f"integrity: {sub.bracket_integrity}\n")
        + escape(f"min_tick:  {tm.get('min_tick', '-')}\n")
        + "</pre>\n"
        + escape("仅纸面账户; 不会触发实盘交易.")
    )
    try:
        send_telegram_message(body, cfg=cfg, journal=journal)
    except Exception:  # noqa: BLE001
        logger.warning("telegram submitted digest failed", exc_info=True)


def _maybe_send_incomplete_bracket_telegram(
    cfg: AppConfig, journal: Journal, sub: IntradayPaperSubmissionResult,
) -> None:
    if not cfg.telegram.is_configured or sub.intent is None:
        return
    from html import escape

    from ..notifications import send_telegram_message

    it = sub.intent
    body = (
        "<b>"
        + escape("【Paper Bracket 不完整】Bracket protection is INCOMPLETE")
        + "</b>\n<pre>"
        + escape(f"symbol: {it.symbol}\n")
        + escape(f"order_ids: {sub.order_ids}\n")
        + escape(f"integrity: {sub.broker_errors}\n")
        + escape(f"codes: {sub.broker_error_codes}\n")
        + "</pre>"
        + escape("已在 TWS 提交，但止损/子单可能被拒。请在 TWS 核查或撤单。")
    )
    try:
        send_telegram_message(body, cfg=cfg, journal=journal)
    except Exception:  # noqa: BLE001
        logger.warning("telegram incomplete bracket digest failed", exc_info=True)


_CRITICAL_SKIP_KEYS: tuple[str, ...] = (
    "kill switch",
    "reconciliation",
    "duplicate",
    "invalid bracket",
    "long bracket",
    "short bracket",
    "missing stop",
    "missing target",
    "live blocked",
    "block_live_trading",
)


def _is_critical_skip(reason: str) -> bool:
    r = (reason or "").lower()
    return any(k in r for k in _CRITICAL_SKIP_KEYS)


def _maybe_send_critical_skip_telegram(
    cfg: AppConfig,
    journal: Journal,
    reason: str,
    *,
    symbol: str | None = None,
) -> None:
    if not cfg.telegram.is_configured:
        return
    if not _is_critical_skip(reason):
        return
    from html import escape

    from ..notifications import send_telegram_message

    head = "【Paper Trade Skipped】纸面交易跳过 (重要)"
    body = (
        "<b>" + escape(head) + "</b>\n<pre>"
        + escape(f"symbol: {symbol or '-'}\n")
        + escape(f"reason: {reason}\n")
        + "</pre>"
    )
    try:
        send_telegram_message(body, cfg=cfg, journal=journal)
    except Exception:  # noqa: BLE001
        logger.warning("telegram skipped digest failed", exc_info=True)


__all__ = [
    "INTRADAY_AUTO_PAPER_ENABLED_RELPATH",
    "INTRADAY_LOOP_STATE_RELPATH",
    "KILL_SWITCH_RELPATH",
    "PAPER_ORDERS_DIR",
    "READY_AGGRESSIVE",
    "READY_STRICT",
    "IntradayPaperIntent",
    "IntradayPaperPassResult",
    "IntradayPaperSubmissionResult",
    "build_intraday_paper_intent",
    "format_intraday_paper_digest_zh",
    "is_intraday_paper_runtime_enabled",
    "is_kill_switch_active",
    "run_intraday_paper_pass",
    "serialize_paper_submission",
    "submit_intraday_paper_bracket",
    "validate_intraday_paper_intent",
    "verify_intraday_paper_bracket_trades",
]
