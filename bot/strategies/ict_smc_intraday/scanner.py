"""Per-symbol + watchlist scan orchestration for ICT/SMC Intraday V1.

The scanner glues :mod:`.detector` outputs together:

1. ``build_intraday_context`` (4H/30m/5m bias, 30m liquidity map).
2. ``detect_5m_setup``        (sweep + reclaim setup zone).
3. ``detect_1m_entry_trigger`` (micro sweep + MSS + entry source).
4. ``build_intraday_trade_plan`` (entry/stop/target + R/R sanity).
5. ``classify_intraday_signal`` (STRICT / AGGRESSIVE / WATCH / ...).

Hard invariants:

* ``execution_allowed`` is always False on every payload that leaves
  the scanner. This module NEVER places orders, NEVER imports
  :mod:`bot.broker`, and NEVER mutates broker state.
* IBKR is only touched inside :func:`scan_symbol_with_ibkr` and
  :func:`scan_watchlist_with_ibkr`, both gated by ``use_ibkr=True``
  and called only from the CLI / worker — never from the UI.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .detector import (
    build_intraday_context,
    detect_1m_entry_trigger,
    detect_5m_setup,
)
from .model import (
    DEFAULT_STOP_BUFFER_PCT,
    DIRECTION_FLAT,
    DIRECTION_LONG,
    DIRECTION_SHORT,
    ENTRY_SOURCE_BREAKER,
    ENTRY_SOURCE_FVG,
    ENTRY_SOURCE_NONE,
    ENTRY_SOURCE_OB,
    FiveMinuteSetup,
    IntradayContext,
    IntradayEvaluation,
    IntradayRiskConfig,
    IntradayTradePlan,
    OneMinuteTrigger,
    SIGNAL_BLOCKED,
    SIGNAL_DAY_TRADE_READY_AGGRESSIVE,
    SIGNAL_DAY_TRADE_READY_STRICT,
    SIGNAL_ERROR,
    SIGNAL_INVALID_RISK,
    SIGNAL_NO_SETUP,
    SIGNAL_WATCH_ONLY,
    STRATEGY_KEY,
)

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trade plan
# ---------------------------------------------------------------------------
def _round(v: float | None, ndigits: int = 4) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _higher_timeframe_context_ok_for_paper(ctx: IntradayContext | None) -> bool:
    """True when 4H and 30m bars were available (ICT context chain).

    Used in watchlist summary rows for paper execution invariants; not a
    scanner strategy filter on its own (see ``classify_intraday_signal``).
    """
    if ctx is None:
        return False
    md = {str(x).lower() for x in (ctx.missing_data or [])}
    if "4h" in md or "30min" in md:
        return False
    return True


def build_intraday_trade_plan(
    trigger: OneMinuteTrigger,
    context: IntradayContext,
    cfg: IntradayRiskConfig,
) -> IntradayTradePlan:
    """Translate the 1m trigger into entry / stop / target with R/R checks.

    Long entry rule (mirrored for short):

    * entry  = midpoint of FVG/OB if available, else the 5m setup's
      reclaim_close (the "breaker" entry).
    * stop   = trigger.swept_level_price - buffer  (long)
               trigger.swept_level_price + buffer  (short)
    * target = entry + (stop_distance * min_rr_strict)  — i.e. we plan
      the strict R/R first; the classifier downgrades to AGGRESSIVE if
      R/R is between aggressive and strict.

    All ``stop_distance_pct`` and ``extension_from_entry_pct`` are
    computed off the entry price.
    """
    plan = IntradayTradePlan(direction=trigger.direction)
    if trigger.direction not in (DIRECTION_LONG, DIRECTION_SHORT):
        plan.rejection_reasons.append("plan: trigger has no actionable direction")
        return plan
    if not trigger.found:
        plan.rejection_reasons.append("plan: 1m trigger not found")
        return plan
    if trigger.swept_level_price is None or trigger.swept_level_price <= 0:
        plan.rejection_reasons.append("plan: invalid swept level")
        return plan

    is_long = trigger.direction == DIRECTION_LONG

    # Pick entry source per priority FVG > OB > breaker/reclaim.
    entry: float | None = None
    if trigger.entry_source == ENTRY_SOURCE_FVG and trigger.fvg_low is not None and trigger.fvg_high is not None:
        entry = (trigger.fvg_low + trigger.fvg_high) / 2.0
    elif trigger.entry_source == ENTRY_SOURCE_OB and trigger.ob_low is not None and trigger.ob_high is not None:
        entry = (trigger.ob_low + trigger.ob_high) / 2.0
    elif trigger.entry_source == ENTRY_SOURCE_BREAKER:
        # Use the trigger's MSS pivot as the reclaim/breaker reference.
        if trigger.mss_pivot_price is not None and trigger.mss_pivot_price > 0:
            entry = float(trigger.mss_pivot_price)
    if entry is None or entry <= 0:
        plan.rejection_reasons.append(
            f"plan: cannot derive entry from source={trigger.entry_source!r}"
        )
        return plan

    swept = float(trigger.swept_level_price)
    buffer_pct = float(cfg.stop_buffer_pct or DEFAULT_STOP_BUFFER_PCT)
    buffer = abs(entry) * (buffer_pct / 100.0)

    if is_long:
        stop = swept - buffer
        if stop >= entry:
            plan.rejection_reasons.append(
                "plan: stop >= entry on long (invalid)"
            )
            plan.entry = _round(entry)
            plan.stop = _round(stop)
            return plan
        risk_per_share = entry - stop
        target = entry + risk_per_share * cfg.min_rr_strict
        if target <= entry:
            plan.rejection_reasons.append(
                "plan: target <= entry on long (invalid)"
            )
            return plan
    else:
        stop = swept + buffer
        if stop <= entry:
            plan.rejection_reasons.append(
                "plan: stop <= entry on short (invalid)"
            )
            plan.entry = _round(entry)
            plan.stop = _round(stop)
            return plan
        risk_per_share = stop - entry
        target = entry - risk_per_share * cfg.min_rr_strict
        if target >= entry:
            plan.rejection_reasons.append(
                "plan: target >= entry on short (invalid)"
            )
            return plan

    if risk_per_share <= 0:
        plan.rejection_reasons.append("plan: non-positive risk_per_share")
        return plan

    reward_per_share = abs(target - entry)
    rr = reward_per_share / risk_per_share if risk_per_share > 0 else 0.0
    stop_distance_pct = (abs(entry - stop) / entry) * 100.0 if entry > 0 else 0.0

    # Extension-from-entry: the most recent 1m close vs entry.
    last_close = context.bars_1m_count and entry  # fallback if we have no last close
    extension_pct: float | None = None
    if context and context.liquidity_levels:
        # we don't have the actual last close here; the scanner does
        # extension validation against the live last-close before classify.
        pass

    plan.valid = True
    plan.entry = _round(entry)
    plan.stop = _round(stop)
    plan.target = _round(target)
    plan.risk_per_share = _round(risk_per_share)
    plan.reward_per_share = _round(reward_per_share)
    plan.risk_reward = _round(rr)
    plan.stop_distance_pct = _round(stop_distance_pct)
    plan.extension_from_entry_pct = extension_pct
    return plan


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def classify_intraday_signal(
    eval_obj: IntradayEvaluation,
    cfg: IntradayRiskConfig,
    *,
    last_close: float | None = None,
) -> str:
    """Return one of the SIGNAL_* constants.

    Order of checks (first match wins):
      1. BLOCKED  — context says 1m is missing.
      2. NO_SETUP — 5m setup not found.
      3. WATCH    — 5m setup ok but 1m trigger missing OR plan invalid
                    *for "still waiting" reasons*.
      4. INVALID  — plan is structurally invalid (stop on wrong side,
                    R/R below aggressive floor, stop too wide, price
                    too extended from entry).
      5. STRICT / AGGRESSIVE — plan valid + thresholds met.
    """
    ctx = eval_obj.context
    setup = eval_obj.five_min_setup
    trig = eval_obj.one_min_trigger
    plan = eval_obj.trade_plan

    if ctx is None or "1min" in (ctx.missing_data or []):
        return SIGNAL_BLOCKED
    if setup is None or not setup.found:
        return SIGNAL_NO_SETUP
    if trig is None or not trig.found:
        return SIGNAL_WATCH_ONLY
    if plan is None:
        return SIGNAL_WATCH_ONLY
    # Plan invalid for "still waiting" reasons → WATCH; structural
    # rejections stay INVALID_RISK below.
    waiting_markers = ("price has not returned", "1m: 5m setup not found")
    if not plan.valid and any(
        any(m in r for m in waiting_markers)
        for r in (trig.rejection_reasons or [])
    ):
        return SIGNAL_WATCH_ONLY
    if not plan.valid:
        return SIGNAL_INVALID_RISK

    # Hard invalids based on plan numbers.
    if plan.stop_distance_pct is not None and plan.stop_distance_pct > cfg.max_stop_distance_pct:
        eval_obj.rejection_reasons.append(
            f"stop_distance_pct={plan.stop_distance_pct} > {cfg.max_stop_distance_pct}"
        )
        return SIGNAL_INVALID_RISK

    if last_close is not None and plan.entry and plan.entry > 0:
        ext_pct = abs(last_close - plan.entry) / plan.entry * 100.0
        plan.extension_from_entry_pct = _round(ext_pct)
        if ext_pct > cfg.max_extension_from_entry_pct:
            eval_obj.rejection_reasons.append(
                f"extension_from_entry_pct={ext_pct:.4f} > {cfg.max_extension_from_entry_pct}"
            )
            return SIGNAL_INVALID_RISK

    rr = plan.risk_reward or 0.0
    if rr < cfg.min_rr_aggressive:
        eval_obj.rejection_reasons.append(
            f"risk_reward={rr} < min_rr_aggressive={cfg.min_rr_aggressive}"
        )
        return SIGNAL_INVALID_RISK

    # STRICT requires FVG-or-displacement AND R/R >= strict.
    has_fvg_or_displacement = (
        trig.entry_source == ENTRY_SOURCE_FVG or trig.has_displacement
    )
    if has_fvg_or_displacement and rr >= cfg.min_rr_strict:
        return SIGNAL_DAY_TRADE_READY_STRICT
    # Otherwise AGGRESSIVE if any of OB / breaker / FVG-without-strict-rr.
    if trig.entry_source in (ENTRY_SOURCE_FVG, ENTRY_SOURCE_OB, ENTRY_SOURCE_BREAKER):
        return SIGNAL_DAY_TRADE_READY_AGGRESSIVE
    return SIGNAL_WATCH_ONLY


def _next_condition_for(eval_obj: IntradayEvaluation) -> str:
    cat = eval_obj.signal_category
    if cat == SIGNAL_NO_SETUP:
        return "等待 5m 出现扫流动 + 收回 (sweep + reclaim)。"
    if cat == SIGNAL_BLOCKED:
        return "1m 数据缺失，重新拉取 1m 后再试。"
    if cat == SIGNAL_WATCH_ONLY:
        if eval_obj.one_min_trigger and not eval_obj.one_min_trigger.found:
            return "5m 已就位；等待价格回到 setup 区并出现 1m 微扫 + MSS/ChoCH。"
        return "等待 1m 入场触发完成，再生成有效交易计划。"
    if cat == SIGNAL_INVALID_RISK:
        return "结构成立但风控不达标 (止损过宽 / R:R 偏低 / 价格远离入场)。"
    if cat == SIGNAL_DAY_TRADE_READY_STRICT:
        return "纸面研究信号: 已进入 STRICT 候选；仍需人工确认与风控。"
    if cat == SIGNAL_DAY_TRADE_READY_AGGRESSIVE:
        return "纸面研究信号: 已进入 AGGRESSIVE 候选；R:R 接近下限，请严格止损。"
    if cat == SIGNAL_ERROR:
        return "扫描出错；查看 rejection_reasons / data_quality 字段。"
    return ""


def _explanation_zh(eval_obj: IntradayEvaluation) -> str:
    setup = eval_obj.five_min_setup
    trig = eval_obj.one_min_trigger
    plan = eval_obj.trade_plan
    parts = [f"{eval_obj.symbol} ({eval_obj.signal_category})"]
    if eval_obj.context:
        ctx = eval_obj.context
        parts.append(
            f"4H={ctx.bias_4h} / 30m={ctx.bias_30m} / 5m={ctx.bias_5m} "
            f"premium_discount(30m)={ctx.premium_discount_30m}"
        )
    if setup and setup.found:
        parts.append(
            f"5m {setup.direction} sweep@{setup.sweep_timestamp} "
            f"swept={setup.swept_level_price} reclaim={setup.reclaim_close} "
            f"setup_kind={setup.setup_kind} mss={setup.mss_found}"
        )
    if trig and trig.found:
        parts.append(
            f"1m {trig.direction} sweep+MSS via {trig.entry_source}; "
            f"displacement={trig.has_displacement}"
        )
    if plan and plan.valid:
        parts.append(
            f"plan entry={plan.entry} stop={plan.stop} target={plan.target} "
            f"R:R={plan.risk_reward} stop_pct={plan.stop_distance_pct}"
        )
    if eval_obj.rejection_reasons:
        parts.append("notes: " + "; ".join(eval_obj.rejection_reasons[:5]))
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Public scan entrypoints (no IBKR import needed for in-memory use)
# ---------------------------------------------------------------------------
def scan_symbol_from_bars(
    symbol: str,
    *,
    bars_4h: list[dict[str, Any]] | None,
    bars_30m: list[dict[str, Any]] | None,
    bars_5m: list[dict[str, Any]] | None,
    bars_1m: list[dict[str, Any]] | None,
    risk_cfg: IntradayRiskConfig | None = None,
    direction_hint: str = "auto",
    data_source: str = "fixture",
    research_flags: Iterable[str] | None = None,
) -> IntradayEvaluation:
    """Run the full pipeline on already-fetched bars (no IBKR call here)."""
    risk_cfg = risk_cfg or IntradayRiskConfig()
    bars_1m = bars_1m or []
    ctx = build_intraday_context(
        symbol,
        bars_4h,
        bars_30m,
        bars_5m,
        bars_1m_count=len(bars_1m),
        data_source=data_source,
    )
    if not bars_1m:
        ctx.missing_data = list(set([*ctx.missing_data, "1min"]))
        ctx.notes.append("1m bars unavailable; entry trigger cannot run.")
        eval_obj = IntradayEvaluation(
            symbol=symbol.upper(),
            date=_utc_today(),
            direction=DIRECTION_FLAT,
            signal_category=SIGNAL_BLOCKED,
            context=ctx,
            five_min_setup=None,
            one_min_trigger=None,
            trade_plan=None,
            rejection_reasons=["1m data missing"],
            research_flags=list(research_flags or []),
            data_source=data_source,
            data_quality={
                "bars_1m_count": 0,
                "bars_5m_count": len(bars_5m or []),
                "bars_30m_count": len(bars_30m or []),
                "bars_4h_count": len(bars_4h or []),
                "data_source": data_source,
                "missing_data": ctx.missing_data,
            },
        )
        eval_obj.next_condition_to_watch = _next_condition_for(eval_obj)
        eval_obj.explanation_zh = _explanation_zh(eval_obj)
        return eval_obj

    setup = detect_5m_setup(bars_5m, ctx, direction_hint=direction_hint)
    trigger = (
        detect_1m_entry_trigger(bars_1m, setup, ctx)
        if setup.found
        else OneMinuteTrigger(direction=DIRECTION_FLAT)
    )
    plan = (
        build_intraday_trade_plan(trigger, ctx, risk_cfg)
        if trigger.found
        else IntradayTradePlan(direction=trigger.direction)
    )

    direction = setup.direction if setup.found else DIRECTION_FLAT
    last_close = None
    if bars_1m:
        try:
            last_close = float(bars_1m[-1].get("close", 0.0) or 0.0)
        except (TypeError, ValueError):
            last_close = None

    eval_obj = IntradayEvaluation(
        symbol=symbol.upper(),
        date=_utc_today(),
        direction=direction,
        signal_category=SIGNAL_NO_SETUP,  # placeholder; replaced below
        context=ctx,
        five_min_setup=setup,
        one_min_trigger=trigger,
        trade_plan=plan,
        rejection_reasons=[],
        research_flags=list(research_flags or []),
        data_source=data_source,
        data_quality={
            "bars_1m_count": len(bars_1m or []),
            "bars_5m_count": len(bars_5m or []),
            "bars_30m_count": len(bars_30m or []),
            "bars_4h_count": len(bars_4h or []),
            "data_source": data_source,
            "missing_data": ctx.missing_data,
        },
    )
    cat = classify_intraday_signal(eval_obj, risk_cfg, last_close=last_close)
    # Re-build the evaluation with the resolved category (frozen=False so
    # we can mutate, but using a setter for clarity).
    eval_obj.signal_category = cat
    eval_obj.next_condition_to_watch = _next_condition_for(eval_obj)
    eval_obj.explanation_zh = _explanation_zh(eval_obj)
    # A simple scoring: STRICT=80, AGGRESSIVE=60, WATCH=40, INVALID=20,
    # NO_SETUP=10, BLOCKED=0, ERROR=0.
    score_map = {
        SIGNAL_DAY_TRADE_READY_STRICT: 80.0,
        SIGNAL_DAY_TRADE_READY_AGGRESSIVE: 60.0,
        SIGNAL_WATCH_ONLY: 40.0,
        SIGNAL_INVALID_RISK: 20.0,
        SIGNAL_NO_SETUP: 10.0,
        SIGNAL_BLOCKED: 0.0,
        SIGNAL_ERROR: 0.0,
    }
    eval_obj.score = score_map.get(cat, 0.0)
    return eval_obj


# ---------------------------------------------------------------------------
# IBKR-backed scans (only called from CLI / worker — never from UI)
# ---------------------------------------------------------------------------
def _resolve_specs(cfg: Any) -> dict[str, Any]:
    from ...smc_timeframes import resolve_timeframe_spec

    return {
        "4h": resolve_timeframe_spec("4h", cfg),
        "30min": resolve_timeframe_spec("30min", cfg),
        "5min": resolve_timeframe_spec("5min", cfg),
        "1min": resolve_timeframe_spec("1min", cfg),
    }


def _connect(cfg: Any) -> Any:
    """Lazy-import the IBKR client; never imported at module load.

    Raises if connection fails. The CLI / worker is responsible for
    catching and degrading.
    """
    from ...ibkr_client import IBKRClient  # noqa: PLC0415

    client = IBKRClient(cfg)
    client.connect(readonly=True)
    return client


def _fetch_bars_for_symbol(
    client: Any,
    symbol: str,
    cfg: Any,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Read-only fetch of 4h / 30min / 5min / 1min bars."""
    specs = _resolve_specs(cfg)
    out: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    for key in ("4h", "30min", "5min", "1min"):
        try:
            rows = client.get_bars_for_timeframe(
                symbol, specs[key], out_warnings=warnings
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{key}: fetch failed ({exc!r})")
            rows = []
        out[key] = rows or []
    return out, warnings


def scan_symbol_with_ibkr(
    symbol: str,
    cfg: Any,
    journal: Any,
    *,
    risk_cfg: IntradayRiskConfig | None = None,
    direction_hint: str = "auto",
    research_flags: Iterable[str] | None = None,
    chart: bool = False,
    chart_dir: Path | None = None,
) -> IntradayEvaluation:
    """Connect read-only, fetch 4h/30m/5m/1m, run the pipeline, render charts."""
    risk_cfg = risk_cfg or IntradayRiskConfig()
    warnings: list[str] = []
    client = None
    bars: dict[str, list[dict[str, Any]]] = {"4h": [], "30min": [], "5min": [], "1min": []}
    data_source = "ibkr"
    try:
        try:
            client = _connect(cfg)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"ibkr_connect: {exc!r}")
            data_source = "missing"
        if client is not None:
            bars, fetch_warns = _fetch_bars_for_symbol(client, symbol, cfg)
            warnings.extend(fetch_warns)
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    eval_obj = scan_symbol_from_bars(
        symbol,
        bars_4h=bars["4h"],
        bars_30m=bars["30min"],
        bars_5m=bars["5min"],
        bars_1m=bars["1min"],
        risk_cfg=risk_cfg,
        direction_hint=direction_hint,
        data_source=data_source,
        research_flags=research_flags,
    )
    if warnings:
        eval_obj.rejection_reasons.extend([f"warning: {w}" for w in warnings])

    if chart and chart_dir is not None:
        try:
            from .charts import render_intraday_charts

            paths = render_intraday_charts(
                symbol,
                bars_30m=bars["30min"],
                bars_5m=bars["5min"],
                bars_1m=bars["1min"],
                evaluation=eval_obj,
                output_dir=Path(chart_dir),
            )
            eval_obj.chart_paths = paths
        except Exception as exc:  # noqa: BLE001
            eval_obj.chart_error = f"chart_render_failed: {exc!r}"
            LOG.info("intraday chart render failed for %s: %s", symbol, exc)

    if journal is not None:
        try:
            journal.record_event(
                category="ict_smc_intraday",
                level="INFO",
                message="scan-intraday-smc",
                payload={
                    "symbol": symbol.upper(),
                    "signal_category": eval_obj.signal_category,
                    "direction": eval_obj.direction,
                    "data_source": eval_obj.data_source,
                    "execution_allowed": False,
                },
            )
        except Exception:  # noqa: BLE001
            pass
    return eval_obj


# ---------------------------------------------------------------------------
# Watchlist orchestration
# ---------------------------------------------------------------------------
def _resolve_watchlist(cfg: Any, source: str | None) -> list[str]:
    """Return symbols for the watchlist scan.

    Honours the same ``static`` / ``dynamic`` semantics used by
    ``scan-mtf-smc-watchlist``. ``manual`` is accepted as an alias of
    ``static`` for friendlier CLI usage.
    """
    chosen = (source or "dynamic").strip().lower()
    if chosen in {"manual"}:
        chosen = "static"
    if chosen not in {"static", "dynamic"}:
        raise ValueError("source must be static, dynamic, or manual")
    if chosen == "dynamic":
        from ...watchlist_builder import load_dynamic_watchlist  # noqa: PLC0415

        dw = load_dynamic_watchlist(cfg)
        if dw is None:
            raise FileNotFoundError("dynamic watchlist not built")
        return [r.symbol for r in dw.symbols if not getattr(r, "blocked", False)]

    eqs = (getattr(cfg, "watchlist", {}) or {}).get("equities") or []
    out: list[str] = []
    for e in eqs:
        if isinstance(e, dict) and e.get("symbol"):
            out.append(str(e["symbol"]).upper())
        elif isinstance(e, str):
            out.append(e.upper())
    if not out:
        out = list((getattr(cfg, "watchlist", {}) or {}).get("static_core") or [])
    return [s.upper() for s in out]


def scan_watchlist_with_ibkr(
    cfg: Any,
    journal: Any,
    *,
    use_ibkr: bool,
    chart: bool,
    telegram: bool,
    limit: int | None,
    source: str | None,
    save_json: bool = True,
    risk_cfg: IntradayRiskConfig | None = None,
    research_flags: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Loop over watchlist symbols, scan each, write summary JSON."""
    if not use_ibkr:
        raise ValueError("scan_watchlist_with_ibkr requires use_ibkr=True")
    risk_cfg = risk_cfg or IntradayRiskConfig()
    symbols = _resolve_watchlist(cfg, source)
    if limit is not None and limit > 0:
        symbols = symbols[: int(limit)]
    chart_dir = (
        Path(cfg.absolute("data/debug_charts")) if chart else None
    )
    out_dir = Path(cfg.absolute("data/intraday_smc"))
    out_dir.mkdir(parents=True, exist_ok=True)

    items: list[dict[str, Any]] = []
    saved_paths: list[str] = []
    for sym in symbols:
        eval_obj = scan_symbol_with_ibkr(
            sym,
            cfg,
            journal,
            risk_cfg=risk_cfg,
            chart=chart,
            chart_dir=chart_dir,
            research_flags=research_flags,
        )
        if save_json:
            p = save_intraday_evaluation(out_dir, eval_obj)
            saved_paths.append(str(p))
        items.append(_compact_summary_row(eval_obj))

    summary = build_watchlist_summary(
        items=items,
        symbols_scanned=len(symbols),
        source=str(source or ""),
    )
    summary_path: Path | None = None
    if save_json:
        summary_path = save_intraday_watchlist_summary(out_dir, summary)
    summary["_saved_summary_path"] = str(summary_path) if summary_path else None
    summary["_saved_per_symbol_paths"] = saved_paths

    if telegram:
        try:
            from ...notifications.telegram import send_telegram_message  # noqa: PLC0415

            tg_cfg = getattr(cfg, "telegram", None)
            if tg_cfg and getattr(tg_cfg, "is_configured", False):
                text = format_intraday_telegram_zh(summary)
                send_telegram_message(text, cfg=cfg, journal=journal)
                summary["_telegram_sent"] = True
            else:
                summary["_telegram_sent"] = False
                summary.setdefault("_notes", []).append(
                    "telegram not configured; digest skipped."
                )
        except Exception as exc:  # noqa: BLE001
            summary["_telegram_sent"] = False
            summary.setdefault("_notes", []).append(f"telegram send failed: {exc!r}")
    else:
        summary["_telegram_sent"] = False

    return summary


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------
def _compact_summary_row(eval_obj: IntradayEvaluation) -> dict[str, Any]:
    plan = eval_obj.trade_plan
    return {
        "symbol": eval_obj.symbol,
        "signal_category": eval_obj.signal_category,
        "direction": eval_obj.direction,
        "score": eval_obj.score,
        "five_min_setup_found": bool(eval_obj.five_min_setup and eval_obj.five_min_setup.found),
        "one_min_trigger_found": bool(eval_obj.one_min_trigger and eval_obj.one_min_trigger.found),
        "higher_timeframe_context_ok": _higher_timeframe_context_ok_for_paper(
            eval_obj.context
        ),
        "entry": plan.entry if plan else None,
        "stop": plan.stop if plan else None,
        "target": plan.target if plan else None,
        "risk_reward": plan.risk_reward if plan else None,
        "stop_distance_pct": plan.stop_distance_pct if plan else None,
        "next_condition_to_watch": eval_obj.next_condition_to_watch,
        "explanation_zh": eval_obj.explanation_zh,
        "chart_paths": list(eval_obj.chart_paths or []),
        "data_source": eval_obj.data_source,
        "data_quality": dict(eval_obj.data_quality or {}),
        "execution_allowed": False,
        "paper_only": True,
    }


def build_watchlist_summary(
    *,
    items: list[dict[str, Any]],
    symbols_scanned: int,
    source: str,
) -> dict[str, Any]:
    counts = {
        SIGNAL_DAY_TRADE_READY_STRICT: 0,
        SIGNAL_DAY_TRADE_READY_AGGRESSIVE: 0,
        SIGNAL_WATCH_ONLY: 0,
        SIGNAL_INVALID_RISK: 0,
        SIGNAL_BLOCKED: 0,
        SIGNAL_NO_SETUP: 0,
        SIGNAL_ERROR: 0,
    }
    for it in items:
        cat = str(it.get("signal_category") or "")
        if cat in counts:
            counts[cat] += 1
    ready_strict = [it["symbol"] for it in items if it.get("signal_category") == SIGNAL_DAY_TRADE_READY_STRICT]
    ready_aggr = [it["symbol"] for it in items if it.get("signal_category") == SIGNAL_DAY_TRADE_READY_AGGRESSIVE]
    watch = [it["symbol"] for it in items if it.get("signal_category") == SIGNAL_WATCH_ONLY]
    invalid = [it["symbol"] for it in items if it.get("signal_category") == SIGNAL_INVALID_RISK]
    top = sorted(
        items,
        key=lambda it: (it.get("score") or 0.0),
        reverse=True,
    )[:10]
    return {
        "date": _utc_today(),
        "strategy_id": STRATEGY_KEY,
        "source": source,
        "symbols_scanned": symbols_scanned,
        "paper_only": True,
        "execution_allowed": False,
        "counts": counts,
        "ready_strict_symbols": ready_strict,
        "ready_aggressive_symbols": ready_aggr,
        "watch_symbols": watch,
        "invalid_symbols": invalid,
        "top_candidates": top,
        "items": items,
    }


def save_intraday_evaluation(out_dir: Path, eval_obj: IntradayEvaluation) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{eval_obj.date or _utc_today()}-{eval_obj.symbol}-intraday-smc.json"
    path = out_dir / name
    payload = eval_obj.to_dict()
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return path


def save_intraday_watchlist_summary(out_dir: Path, summary: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    day = summary.get("date") or _utc_today()
    name = f"{day}-watchlist-intraday-smc-summary.json"
    path = out_dir / name
    payload = {k: v for k, v in summary.items() if not k.startswith("_")}
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return path


def format_intraday_telegram_zh(summary: Mapping[str, Any]) -> str:
    counts = dict(summary.get("counts") or {})
    strict = list(summary.get("ready_strict_symbols") or [])
    aggr = list(summary.get("ready_aggressive_symbols") or [])
    watch = list(summary.get("watch_symbols") or [])
    invalid = list(summary.get("invalid_symbols") or [])
    lines = [
        "<b>【ICT/SMC 日内扫描】</b>",
        f"日期: {summary.get('date', '?')}  来源: {summary.get('source') or '-'}",
        f"扫描数量: {summary.get('symbols_scanned', 0)}",
        "—— 计数 ——",
        f"STRICT: {counts.get(SIGNAL_DAY_TRADE_READY_STRICT, 0)}",
        f"AGGRESSIVE: {counts.get(SIGNAL_DAY_TRADE_READY_AGGRESSIVE, 0)}",
        f"WATCH: {counts.get(SIGNAL_WATCH_ONLY, 0)}",
        f"INVALID: {counts.get(SIGNAL_INVALID_RISK, 0)}",
        f"NO_SETUP: {counts.get(SIGNAL_NO_SETUP, 0)}  BLOCKED: {counts.get(SIGNAL_BLOCKED, 0)}",
    ]
    if strict:
        lines.append("STRICT 候选: " + ", ".join(strict[:8]))
    if aggr:
        lines.append("AGGRESSIVE 候选: " + ", ".join(aggr[:8]))
    if watch:
        lines.append("观察列表: " + ", ".join(watch[:8]))
    if invalid:
        lines.append("风控不达标: " + ", ".join(invalid[:8]))
    lines.append("纸面研究; 不下单, 不开 live。execution_allowed=false。")
    return "\n".join(lines)


__all__ = [
    "build_intraday_trade_plan",
    "build_watchlist_summary",
    "classify_intraday_signal",
    "format_intraday_telegram_zh",
    "save_intraday_evaluation",
    "save_intraday_watchlist_summary",
    "scan_symbol_from_bars",
    "scan_symbol_with_ibkr",
    "scan_watchlist_with_ibkr",
]
