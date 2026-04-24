"""Batch MTF SMC watchlist scan (shared by scan-mtf-smc-watchlist and auto-paper-mtf)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import AppConfig
from .journal import Journal
from .watchlist_builder import load_dynamic_watchlist


def run_mtf_smc_watchlist_scan(
    cfg: AppConfig,
    journal: Journal,
    *,
    use_ibkr: bool,
    chart: bool,
    telegram: bool,
    limit: int | None,
    source: str | None,
    save_json: bool,
    include_5min: bool,
    include_daily: bool,
    paper_bracket: bool,
    max_paper_trades: int,
) -> dict[str, Any]:
    """Run multi-symbol `run_mtf_smc` and optional paper brackets; return summary dict."""
    from . import cli as cli_mod
    from .mtf_chart import render_mtf_smc_charts
    from .mtf_smc_engine import run_mtf_smc

    _maybe_mtf_paper_bracket = cli_mod._maybe_mtf_paper_bracket
    _mtf_connect_and_fetch = cli_mod._mtf_connect_and_fetch
    _mtf_save_json = cli_mod._mtf_save_json
    _mtf_save_watchlist_summary = cli_mod._mtf_save_watchlist_summary
    _resolve_regime_context = cli_mod._resolve_regime_context

    regime_ctx = _resolve_regime_context(cfg, None)
    regime = str(regime_ctx["market_regime"])
    conf = str(regime_ctx.get("regime_confidence") or "medium")
    chosen = (source or str(cfg.watchlist.get("default_source") or "static")).lower()
    if chosen not in {"static", "dynamic"}:
        raise ValueError("source must be static or dynamic")
    symbols: list[str] = []
    if chosen == "dynamic":
        dw = load_dynamic_watchlist(cfg)
        if dw is None:
            raise FileNotFoundError("dynamic watchlist not built")
        symbols = [r.symbol for r in dw.symbols if not r.blocked]
    else:
        eqs = (cfg.watchlist or {}).get("equities") or []
        symbols = [e.get("symbol") for e in eqs if e.get("symbol")]
        if not symbols:
            symbols = list(cfg.watchlist.get("static_core") or [])
    if limit is not None:
        symbols = symbols[: int(limit)]
    if not use_ibkr:
        raise ValueError("use_ibkr required")

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    items: list[dict] = []
    full_reports: list[tuple[str, dict]] = []
    counts: dict[str, int] = {
        "FULL_ALIGNMENT": 0,
        "SETUP_READY_WAITING_TRIGGER": 0,
        "BIAS_OK_SETUP_INCOMPLETE": 0,
        "CONFLICTED": 0,
        "BLOCKED": 0,
    }
    for sym in symbols:
        b, w, client = _mtf_connect_and_fetch(
            sym, cfg, include_5min=include_5min, include_daily=include_daily
        )
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        out_ev: dict = {}
        rep = run_mtf_smc(
            sym,
            cfg,
            b,
            market_regime=regime,
            regime_confidence=conf,
            include_5min=include_5min,
            include_daily=include_daily,
            out_eval=out_ev,
        )
        rep["warnings"] = list(dict.fromkeys((rep.get("warnings") or []) + w))
        if chart and out_ev:
            rep["chart_paths"] = render_mtf_smc_charts(
                sym,
                {
                    "daily": b.daily,
                    "4h": b.h4,
                    "30min": b.m30,
                    "5min": b.m5,
                },
                {k: out_ev.get(k) for k in ("daily", "4h", "30min", "5min")},
                output_dir=cfg.absolute("data/debug_charts"),
            )
        if save_json:
            _mtf_save_json(cfg, rep)
        full_reports.append((sym, rep))
        cat = str(rep.get("alignment_category") or "BLOCKED")
        if cat in counts:
            counts[cat] = counts[cat] + 1
        items.append(
            {
                "symbol": sym.upper(),
                "mtf_alignment_score": rep.get("mtf_alignment_score", 0),
                "alignment_category": cat,
                "eligible_for_future_paper_trade": rep.get(
                    "eligible_for_future_paper_trade", False
                ),
            }
        )
    items.sort(key=lambda r: -float(r.get("mtf_alignment_score", 0)))
    top5 = items[:5]
    elig_names = [i["symbol"] for i in items if i.get("eligible_for_future_paper_trade")]
    paper_runs: list[dict] = []
    n_exec = 0
    if (
        paper_bracket
        and max_paper_trades > 0
        and cfg.settings.trading.mtf_paper_bracket_enabled
    ):
        for sym, rep in full_reports:
            if n_exec >= max_paper_trades:
                break
            if (
                rep.get("alignment_category") == "FULL_ALIGNMENT"
                and rep.get("eligible_for_future_paper_trade")
            ):
                paper_runs.append(
                    {
                        "symbol": sym.upper(),
                        "result": _maybe_mtf_paper_bracket(cfg, journal, rep),
                    }
                )
                n_exec += 1
    elif paper_bracket and max_paper_trades > 0 and not (
        cfg.settings.trading.mtf_paper_bracket_enabled
    ):
        paper_runs = [
            {
                "symbol": None,
                "result": {
                    "submitted": False,
                    "skipped_reasons": ["mtf_paper_bracket_enabled is false"],
                },
            }
        ]
    summary: dict[str, Any] = {
        "date": day,
        "source": chosen,
        "symbols_scanned": len(symbols),
        "research_only": True,
        "execution_allowed": False,
        "counts": counts,
        "top_by_alignment_score": top5,
        "eligible_for_future_paper_trade": elig_names,
        "mtf_paper_bracket_runs": paper_runs,
        "items": items,
    }
    saved = None
    if save_json:
        saved = _mtf_save_watchlist_summary(cfg, summary)
    if telegram and cfg.telegram.is_configured:
        from html import escape

        from .mtf_smc_engine import format_mtf_watchlist_digest_zh
        from .notifications import send_telegram_message

        digest = format_mtf_watchlist_digest_zh(summary)
        body = "<pre>" + escape(digest) + "</pre>"
        send_telegram_message(body, cfg=cfg, journal=journal)
    journal.record_event(
        category="mtf_smc",
        level="INFO",
        message="scan-mtf-smc-watchlist",
        payload={"n": len(symbols), "execution_allowed": False},
    )
    summary["_saved_summary_path"] = str(saved) if saved else None
    return summary
