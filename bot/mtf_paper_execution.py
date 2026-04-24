"""MTF full-alignment paper bracket execution (Prompt 10C).

Long-only IBKR bracket: limit entry, limit take-profit, stop loss.
Only :func:`build_mtf_paper_intent` + :class:`bot.broker.Broker.place_order`
with ``mtf_paper_bracket=True`` may place orders; TWS must be paper;
``settings.trading.mtf_paper_bracket_enabled`` must be true.
"""

from __future__ import annotations

import math
from typing import Any

from .config import AppConfig
from .journal import Journal
from .risk_engine import TradeIntent


def _f(x: object) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def mtf_paper_may_run(cfg: AppConfig, mtf: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (ok, reasons) for attempting MTF paper bracket on this report."""
    reasons: list[str] = []
    t = cfg.settings.trading
    if not t.enabled:
        reasons.append("trading.enabled is false")
    if not t.mtf_paper_bracket_enabled:
        reasons.append("mtf_paper_bracket_enabled is false")
    if t.mtf_paper_require_full_alignment:
        if str(mtf.get("alignment_category") or "") != "FULL_ALIGNMENT":
            reasons.append("alignment_category is not FULL_ALIGNMENT")
        if not mtf.get("eligible_for_future_paper_trade"):
            reasons.append("eligible_for_future_paper_trade is false")
    if t.mtf_paper_require_confirmed_5m:
        t5 = (mtf.get("timeframes") or {}).get("5min") or {}
        if str(t5.get("trigger_state", "")).strip().lower() != "confirmed":
            reasons.append("5min trigger_state is not confirmed (10G)")
    if cfg.settings.account.mode != "paper":
        reasons.append("account.mode must be paper")
    return (len(reasons) == 0, reasons)


def build_mtf_paper_intent(
    mtf: dict[str, Any],
    cfg: AppConfig,
    *,
    account_equity: float,
) -> tuple[TradeIntent | None, list[str]]:
    """Build a long :class:`TradeIntent` with bracket prices from the MTF report."""
    err: list[str] = []
    ok, e2 = mtf_paper_may_run(cfg, mtf)
    if not ok:
        return None, e2
    t30 = (mtf.get("timeframes") or {}).get("30min") or {}
    entry = _f(t30.get("entry_price"))
    stop = _f(t30.get("stop_price"))
    t1 = _f(t30.get("target_1"))
    if entry is None or stop is None or t1 is None:
        return None, ["missing entry/stop/target on 30min timeframe block"]
    if not (stop < entry < t1):
        return None, [
            f"bracket order invalid: need stop<entry<target, got {stop=}, {entry=}, {t1=}"
        ]
    s_block = (cfg.strategies or {}).get("SMC_LIQUIDITY_REVERSAL_RESEARCH") or {}
    tfs = (s_block.get("timeframes") or {}) or {}
    t30c = tfs.get("30min") or {}
    rpt = float(t30c.get("risk_per_trade_pct") or 0.25)
    if rpt <= 0:
        rpt = 0.25
    per_sh = abs(entry - stop)
    if per_sh <= 0:
        return None, ["zero per-share risk (entry == stop)"]
    risk_doll = account_equity * (rpt / 100.0)
    qty = int(math.floor(risk_doll / per_sh))
    if qty < 1:
        return None, [f"size rounds to 0 (equity={account_equity}, risk%={rpt}, per_sh={per_sh})"]
    notional = qty * entry
    cap = account_equity * (cfg.settings.risk.max_equity_per_position_pct / 100.0)
    if notional > cap and cap > 0:
        q2 = int(math.floor(cap / entry))
        if q2 < 1:
            return None, ["max_equity_per_position caps size below 1 share"]
        qty = q2
    return (
        TradeIntent(
            symbol=str(mtf.get("symbol") or "UNK").upper(),
            sec_type="STK",
            side="BUY",
            quantity=float(qty),
            estimated_price=entry,
            take_profit_price=t1,
            stop_loss_price=stop,
            entry_limit_price=entry,
        ),
        [],
    )


def run_mtf_paper_bracket(
    broker: Any,
    cfg: AppConfig,
    mtf: dict[str, Any],
    *,
    account_equity: float,
    open_positions: int = 0,
    reconciliation_ok: bool = True,
) -> dict[str, Any]:
    """If allowed, call ``broker.place_order`` with ``mtf_paper_bracket=True``."""
    intent, err = build_mtf_paper_intent(
        mtf, cfg, account_equity=account_equity
    )
    if intent is None:
        return {
            "submitted": False,
            "error": "; ".join(err) if err else "unknown",
            "order_ids": [],
        }
    from .broker import Broker, TradingDisabled  # local import

    if not isinstance(broker, Broker):
        return {"submitted": False, "error": "invalid broker", "order_ids": []}
    try:
        ticket = broker.place_order(
            intent,
            dry_run=cfg.settings.trading.mtf_paper_dry_run,
            confirmed=False,
            reconciliation_passed=reconciliation_ok,
            account_equity=account_equity,
            open_positions_count=open_positions,
            mtf_paper_bracket=True,
        )
    except TradingDisabled as e:
        return {
            "submitted": False,
            "error": str(e),
            "order_ids": [],
        }
    oids: list = []
    if ticket.mtf_paper and isinstance(ticket.mtf_paper, dict):
        oids = list(ticket.mtf_paper.get("order_ids") or [])
    return {
        "submitted": (not ticket.dry_run) and bool(oids),
        "error": None,
        "order_ids": oids,
        "detail": ticket.mtf_paper,
    }


def connect_and_run_mtf_paper_bracket(
    cfg: AppConfig, journal: Journal, rep: dict[str, Any]
) -> dict[str, Any]:
    """IBKR + :class:`Broker` path (Prompt 10C/10G); same logic as :func:`bot.cli` helper.

    Call this only for MTF report dicts that have already been evaluated with
    fresh ``run_mtf_smc`` output; :func:`mtf_paper_may_run` enforces
    ``FULL_ALIGNMENT`` + ``eligible`` + 5m ``confirmed`` when config flags
    are on.
    """
    from .broker import Broker
    from .ibkr_client import IBKRClient

    ok, reasons = mtf_paper_may_run(cfg, rep)
    if not ok:
        return {"submitted": False, "skipped_reasons": reasons, "order_ids": []}
    ex: Any = None
    try:
        ex = IBKRClient(cfg)
        ex.connect(readonly=False)
        pos = ex.get_positions()
        summ = ex.get_account_summary()
        eq = 0.0
        for a in summ:
            if a.net_liquidation and float(a.net_liquidation) > 0:
                eq = float(a.net_liquidation)
                break
        npos = sum(1 for p in pos if abs(p.position) >= 1e-4)
        br = Broker(cfg, ex, journal=journal)
        return run_mtf_paper_bracket(
            br,
            cfg,
            rep,
            account_equity=eq,
            open_positions=npos,
            reconciliation_ok=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "submitted": False,
            "error": str(exc),
            "order_ids": [],
        }
    finally:
        if ex is not None:
            try:
                ex.disconnect()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "build_mtf_paper_intent",
    "connect_and_run_mtf_paper_bracket",
    "mtf_paper_may_run",
    "run_mtf_paper_bracket",
]
