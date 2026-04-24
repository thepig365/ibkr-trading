"""Typer-based CLI entry point.

Most commands are read-only. The only write path is ``--paper-bracket``
on MTF scan commands (paper accounts, :mod:`bot.broker` only). Run with::

    python -m bot.cli portfolio
    python -m bot.cli open-orders
    python -m bot.cli reconcile
    python -m bot.cli test-telegram

Add ``--verbose`` to any of the above to unmask third-party
(``ib_async``, ``httpx``, ``apscheduler``) debug logs.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .broker import Broker
from .config import AppConfig, load_config
from .ibkr_client import IBKRClient, IBKRClientError, LiveTradingBlocked
from .market_regime import MarketInputs, evaluate_regime
from .watchlist_builder import (
    DEFAULT_STATIC_CORE,
    DynamicWatchlist,
    WatchlistCandidate,
    build_candidate_from_bars,
    build_dynamic_watchlist,
    load_dynamic_watchlist,
    save_dynamic_watchlist,
)
from .journal import Journal
from .news_report import (
    append_report_markdown,
    generate_report,
    notify_report,
    save_report_json,
)
from .notifications import notify_event, send_telegram_message
from .reconciliation import ReconciliationReport, reconcile
from .strategy_engine import (
    STRATEGY_NAME as SMC_STRATEGY_NAME,
    StrategyEvaluation,
    evaluate_smc_liquidity_reversal,
)
from .tws_watchlist_export import (
    ExportPaths,
    export_tws_watchlist,
    load_watchlist_by_date_or_latest,
    validate_contracts,
)

app = typer.Typer(
    add_completion=False,
    help="Read-only CLI for the IBKR paper trading bot foundation.",
    no_args_is_help=True,
)
console = Console()

# Third-party loggers that are *informational* in normal operation. IBKR
# in particular emits a stream of status events (market-data farms,
# sec-def farm, API connection ready) that are not actionable.
_NOISY_LOGGERS: tuple[str, ...] = (
    "ib_async",
    "ib_async.client",
    "ib_async.wrapper",
    "ib_async.ib",
    "ib_insync",
    "ib_insync.client",
    "ib_insync.wrapper",
    "ib_insync.ib",
    "httpx",
    "httpcore",
    "apscheduler",
    "apscheduler.scheduler",
    "apscheduler.executors.default",
)

# IBKR status codes that are advisory, not errors. See
# https://interactivebrokers.github.io/tws-api/message_codes.html
#
# We also include codes that signal "no historical-data subscription"
# (162, 200, 354) because the pre-open report intentionally probes
# VIX / VIX3M which most paper accounts can't fetch. Surface them
# only with --verbose; the structured report still records the
# missing fields under market_data.missing_fields.
_IBKR_INFORMATIONAL_CODES: set[int] = {
    162,                     # Historical Market Data Service - no subscription
    200,                     # No security definition found for the request
    354,                     # Requested market data is not subscribed
    1100, 1101, 1102,        # connection restore/loss notices
    2103, 2104, 2105, 2106,  # market-data farm status
    2107, 2108,              # HMDS data farm status
    2157, 2158,              # sec-def data farm status
    2168, 2169,              # market-data warnings
}


class _IBKRStatusFilter(logging.Filter):
    """Drop purely-informational IBKR status events from non-verbose output.

    `ib_async` logs farm-status messages at INFO through sub-loggers.
    When verbose mode is off we silence those loggers entirely (see
    ``_configure_logging``); this filter is an extra belt-and-braces
    guard for setups that call ``logging.basicConfig`` externally.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        code = getattr(record, "errorCode", None)
        if isinstance(code, int) and code in _IBKR_INFORMATIONAL_CODES:
            return False
        # ib_async emits these as plain strings like
        # "Error 162, reqId 12: Historical Market Data Service error message:..."
        # without setting an `errorCode` attribute, so we also pattern
        # match the formatted message.
        for code_int in _IBKR_INFORMATIONAL_CODES:
            if f"Error {code_int}," in msg or f"Error {code_int}:" in msg:
                return False
        for needle in (
            "Market data farm connection is OK",
            "HMDS data farm connection is OK",
            "Sec-def data farm connection is OK",
            "API connection ready",
            "Market data farm connection is inactive",
            "Historical Market Data Service error message",
            "No security definition has been found",
        ):
            if needle in msg:
                return False
        return True


# Mutable process-wide CLI state populated by the Typer callback.
_STATE: dict[str, object] = {"verbose": False}


def _configure_logging(cfg: AppConfig, verbose: bool) -> None:
    """Configure logging so third-party noise stays hidden by default."""
    cfg_level = getattr(logging, cfg.settings.logging.level.upper(), logging.INFO)
    root_level = logging.DEBUG if verbose else max(cfg_level, logging.WARNING)

    logging.basicConfig(
        level=root_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    # Our own loggers follow the configured verbosity.
    logging.getLogger("bot").setLevel(logging.DEBUG if verbose else cfg_level)

    third_party_level = logging.DEBUG if verbose else logging.WARNING
    status_filter = _IBKRStatusFilter()
    for name in _NOISY_LOGGERS:
        lg = logging.getLogger(name)
        lg.setLevel(third_party_level)
        # Only install the status filter once; logging.Filter comparisons
        # are by identity, so we track membership manually.
        if not any(isinstance(f, _IBKRStatusFilter) for f in lg.filters):
            lg.addFilter(status_filter)


def _connect(cfg: AppConfig, *, readonly: bool = True) -> IBKRClient:
    client = IBKRClient(cfg)
    try:
        client.connect(readonly=readonly)
    except LiveTradingBlocked as exc:
        console.print(
            Panel.fit(f"[bold red]Live trading blocked:[/bold red] {exc}", style="red")
        )
        raise typer.Exit(code=2)
    except IBKRClientError as exc:
        console.print(
            Panel.fit(f"[bold red]Connection error:[/bold red] {exc}", style="red")
        )
        raise typer.Exit(code=1)
    return client


def _bootstrap() -> tuple[AppConfig, Journal]:
    cfg = load_config()
    _configure_logging(cfg, verbose=bool(_STATE.get("verbose", False)))
    journal = Journal(cfg)
    return cfg, journal


@app.callback()
def _root(
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help=(
            "Show third-party debug logs (ib_async, httpx, apscheduler). "
            "Routine IBKR status messages (farm connections, API ready) "
            "are suppressed unless this flag is set."
        ),
    ),
) -> None:
    """Global options for the bot CLI."""
    _STATE["verbose"] = verbose


@app.command()
def portfolio() -> None:
    """Show account summary and current positions. Read-only."""
    cfg, journal = _bootstrap()
    client = _connect(cfg)
    try:
        broker = Broker(cfg, client, journal)
        summaries = broker.get_account_summary()
        positions = broker.get_positions()
    finally:
        client.disconnect()

    if not summaries:
        console.print("[yellow]No account summary returned by IBKR.[/yellow]")
    for s in summaries:
        t = Table(title=f"Account {s.account_id} ({s.currency or '?'})")
        t.add_column("Metric", style="cyan")
        t.add_column("Value", justify="right")
        t.add_row(
            "NetLiquidation",
            f"{s.net_liquidation:,.2f}" if s.net_liquidation is not None else "-",
        )
        t.add_row("TotalCash", f"{s.total_cash:,.2f}" if s.total_cash is not None else "-")
        t.add_row(
            "BuyingPower",
            f"{s.buying_power:,.2f}" if s.buying_power is not None else "-",
        )
        t.add_row(
            "AvailableFunds",
            f"{s.available_funds:,.2f}" if s.available_funds is not None else "-",
        )
        console.print(t)
        journal.record_account_snapshot(s.to_dict())

    if not positions:
        console.print("[green]No open positions.[/green]")
        return

    pt = Table(title="Open positions")
    for col in ("Account", "Symbol", "SecType", "Exchange", "Currency", "Qty", "AvgCost"):
        pt.add_column(col)
    for p in positions:
        pt.add_row(
            p.account,
            p.symbol,
            p.sec_type,
            p.exchange,
            p.currency,
            f"{p.position:g}",
            f"{p.avg_cost:,.4f}",
        )
    console.print(pt)
    journal.record_positions_snapshot([p.to_dict() for p in positions])


@app.command("open-orders")
def open_orders() -> None:
    """List currently open orders at the broker. Read-only."""
    cfg, journal = _bootstrap()
    client = _connect(cfg)
    try:
        broker = Broker(cfg, client, journal)
        orders = broker.get_open_orders()
    finally:
        client.disconnect()

    if not orders:
        console.print("[green]No open orders.[/green]")
        return

    t = Table(title="Open orders")
    for col in (
        "PermId", "OrderId", "Account", "Symbol", "Action", "Type",
        "Qty", "Lmt", "Aux", "TIF", "Status",
    ):
        t.add_column(col)
    for o in orders:
        t.add_row(
            str(o.perm_id) if o.perm_id is not None else "-",
            str(o.order_id) if o.order_id is not None else "-",
            o.account, o.symbol, o.action, o.order_type,
            f"{o.total_quantity:g}",
            f"{o.lmt_price:.4f}" if o.lmt_price is not None else "-",
            f"{o.aux_price:.4f}" if o.aux_price is not None else "-",
            o.tif or "-", o.status or "-",
        )
        journal.record_open_order(o.to_dict(), source="cli.open-orders")
    console.print(t)


def _reconcile_failure_body(report: ReconciliationReport) -> str:
    lines = [
        f"positions_without_stops: {report.positions_without_stops}",
        f"unknown_open_orders count: {len(report.unknown_open_orders)}",
        f"missing_local_records count: {len(report.missing_local_records)}",
        "trading remains blocked",
    ]
    if report.notes:
        lines.append(f"notes: {report.notes}")
    return "\n".join(lines)


@app.command(name="reconcile")
def reconcile_(
    notify: bool = typer.Option(
        False,
        "--notify",
        help="Also notify on PASS. Failures are always notified (with fallback).",
    ),
) -> None:
    """Cross-check broker state against the local journal. NEVER places orders."""
    cfg, journal = _bootstrap()
    client = _connect(cfg)
    try:
        broker = Broker(cfg, client, journal)
        report = reconcile(broker, journal)
    finally:
        client.disconnect()

    style = "green" if report.passed else "red"
    console.print(
        Panel.fit(
            f"Reconciliation: [{style}]{'PASS' if report.passed else 'FAIL'}[/{style}]\n"
            f"positions_without_stops: {report.positions_without_stops}\n"
            f"unknown_open_orders: {len(report.unknown_open_orders)}\n"
            f"missing_local_records: {report.missing_local_records}\n"
            f"notes: {report.notes}",
            style=style,
        )
    )

    if not report.passed:
        # Failures are always surfaced, regardless of --notify.
        notify_event(
            event_type="reconciliation.failed",
            title="Reconciliation Failed",
            body=_reconcile_failure_body(report),
            severity="warning",
            cfg=cfg,
            journal=journal,
        )
    elif notify:
        notify_event(
            event_type="reconciliation.passed",
            title="Reconciliation Passed",
            body="All broker state is consistent with the local journal.",
            severity="info",
            cfg=cfg,
            journal=journal,
        )

    raise typer.Exit(code=0 if report.passed else 3)


@app.command("test-telegram")
def test_telegram(
    text: Optional[str] = typer.Option(
        None, "--text", help="Custom message body.",
    ),
) -> None:
    """Send a test Telegram message.

    This command never connects to IBKR and never places orders. If
    Telegram credentials are missing or the API rejects the message we
    append to ``memory/DAILY-SUMMARY.md`` instead and exit non-zero.
    """
    cfg = load_config()
    _configure_logging(cfg, verbose=bool(_STATE.get("verbose", False)))
    journal = Journal(cfg)

    body = text or "ibkr-trading-bot: test message (foundation milestone)"
    ok = notify_event(
        event_type="test.telegram",
        title="Telegram connectivity test",
        body=body,
        severity="info",
        cfg=cfg,
        journal=journal,
    )

    if ok:
        console.print("[green]Telegram delivered.[/green]")
    elif not cfg.telegram.is_configured:
        console.print(
            "[yellow]Telegram credentials missing; "
            "test message written to memory/DAILY-SUMMARY.md instead.[/yellow]"
        )
    elif not cfg.settings.notifications.telegram.enabled:
        console.print(
            "[yellow]Telegram disabled in settings.yaml; "
            "test message written to memory/DAILY-SUMMARY.md instead.[/yellow]"
        )
    else:
        console.print(
            "[red]Telegram API rejected or errored; "
            "see memory/DAILY-SUMMARY.md for the payload.[/red]"
        )
    raise typer.Exit(code=0 if ok else 4)


@app.command("pre-open-news")
def pre_open_news(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help=(
            "Generate the report in memory only. Skip Telegram, skip writes "
            "to memory/NEWS-REPORT.md and data/pre_open_news/."
        ),
    ),
) -> None:
    """Generate the pre-open major news & risk report.

    Never places orders and never modifies broker state. Writes:
    * ``memory/NEWS-REPORT.md`` (append)
    * ``data/pre_open_news/YYYY-MM-DD.json``

    Also delivers a concise Telegram digest (with fallback). When
    Perplexity is not configured the report is still produced, but
    ``new_positions_allowed`` is forced to ``false`` and the operator
    is asked to review the symbols manually.
    """
    cfg, journal = _bootstrap()

    try:
        report = generate_report(cfg)
    except Exception as exc:  # noqa: BLE001 - we want to recover gracefully
        console.print(
            Panel.fit(
                f"[bold red]pre-open-news failed:[/bold red] {exc!r}",
                style="red",
            )
        )
        journal.record_event(
            category="pre_open_news",
            level="ERROR",
            message="generate_report raised",
            payload={"error": repr(exc)},
        )
        raise typer.Exit(code=5)

    summary_panel = Panel.fit(
        "\n".join(
            [
                f"Date: {report.date}   Regime: [bold]{report.market_regime}[/bold]",
                f"New positions allowed: "
                f"{'yes' if report.new_positions_allowed else 'no'}",
                f"Research available: "
                f"{'yes' if report.research_available else 'no'} "
                f"(ibkr={report.ibkr_news_available}, "
                f"external={report.external_research_available})",
                (
                    f"Blocked: {', '.join(report.blocked_symbols) or '-'}"
                ),
                (
                    f"Manual review: "
                    f"{', '.join(report.manual_review_required) or '-'}"
                ),
                f"Bot instruction: {report.bot_instruction}",
            ]
        ),
        title="Pre-Open News Report",
        style="cyan" if report.new_positions_allowed else "red",
    )
    console.print(summary_panel)

    if dry_run:
        console.print(
            "[yellow]--dry-run: skipping markdown, JSON, and Telegram writes.[/yellow]"
        )
        journal.record_event(
            category="pre_open_news",
            level="INFO",
            message="dry-run",
            payload={
                "date": report.date,
                "regime": report.market_regime,
                "new_positions_allowed": report.new_positions_allowed,
            },
        )
        raise typer.Exit(code=0)

    json_path = save_report_json(cfg, report)
    md_path = append_report_markdown(cfg, report)
    delivered = notify_report(cfg, report, journal=journal)

    journal.record_event(
        category="pre_open_news",
        level="INFO" if report.new_positions_allowed else "WARNING",
        message="report generated",
        payload={
            "date": report.date,
            "regime": report.market_regime,
            "new_positions_allowed": report.new_positions_allowed,
            "blocked_symbols": report.blocked_symbols,
            "manual_review_required": report.manual_review_required,
            "telegram_delivered": delivered,
            "json_path": str(json_path),
            "markdown_path": str(md_path),
        },
    )
    console.print(f"[green]Saved JSON:[/green] {json_path}")
    console.print(f"[green]Appended markdown:[/green] {md_path}")
    if delivered:
        console.print("[green]Telegram digest delivered.[/green]")
    else:
        console.print(
            "[yellow]Telegram digest not delivered; "
            "see memory/DAILY-SUMMARY.md for fallback.[/yellow]"
        )

    raise typer.Exit(code=0)


def _gather_regime_inputs(
    cfg: AppConfig, use_ibkr: bool
) -> tuple[MarketInputs, list[str], IBKRClient | None]:
    """Fetch VIX / SPY / QQQ inputs for the regime evaluator.

    Failures degrade silently — we log the reason and leave the
    corresponding ``MarketInputs`` field as ``None`` so the evaluator
    can apply the trend-only fallback.
    """
    notes: list[str] = []
    client: IBKRClient | None = None
    market = MarketInputs()
    if not use_ibkr:
        notes.append("IBKR not requested; regime will rely on static inputs")
        return market, notes, None
    try:
        client = _connect(cfg)
    except (IBKRClientError, LiveTradingBlocked) as exc:
        notes.append(f"IBKR connect failed: {exc}")
        return market, notes, None
    except Exception as exc:  # noqa: BLE001
        notes.append(f"IBKR connect error: {exc!r}")
        return market, notes, None

    def _safe(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).debug("regime fetch failed: %s", exc)
            return None

    market = MarketInputs(
        vix=_safe(client.get_latest_close, "VIX", sec_type="IND", exchange="CBOE"),
        vix3m=_safe(client.get_latest_close, "VIX3M", sec_type="IND", exchange="CBOE"),
        spy=_safe(client.get_latest_close, "SPY", sec_type="STK", exchange="ARCA"),
        qqq=_safe(client.get_latest_close, "QQQ", sec_type="STK", exchange="NASDAQ"),
        spy_200ma=_safe(
            client.get_simple_moving_average, "SPY", window=200,
            sec_type="STK", exchange="ARCA",
        ),
        qqq_200ma=_safe(
            client.get_simple_moving_average, "QQQ", window=200,
            sec_type="STK", exchange="NASDAQ",
        ),
    )
    for name, val in (
        ("VIX", market.vix), ("VIX3M", market.vix3m),
        ("SPY", market.spy), ("QQQ", market.qqq),
        ("SPY 200MA", market.spy_200ma),
        ("QQQ 200MA", market.qqq_200ma),
    ):
        if val is None:
            notes.append(f"{name} unavailable from IBKR")
    return market, notes, client


@app.command("market-regime")
def market_regime(
    use_ibkr: bool = typer.Option(
        False, "--ibkr",
        help="Fetch VIX / VIX3M / SPY / QQQ from IBKR (read-only)."
    ),
    save: bool = typer.Option(True, "--save/--no-save"),
) -> None:
    """Evaluate the deterministic market regime + confidence.

    Never trades and never modifies broker state. Writes an optional
    snapshot JSON to ``data/market_regime/YYYY-MM-DD.json``.
    """
    cfg, journal = _bootstrap()
    market, notes, client = _gather_regime_inputs(cfg, use_ibkr=use_ibkr)
    try:
        evaluation = evaluate_regime(market, cfg.settings.market_regime.model_dump())
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    md = evaluation.market_data
    panel_body = "\n".join(
        [
            f"Regime: [bold]{evaluation.market_regime}[/bold] "
            f"(confidence={evaluation.regime_confidence})",
            f"Research scans allowed: "
            f"{'yes' if evaluation.research_scans_allowed else 'no'}",
            f"New positions allowed: "
            f"{'yes' if evaluation.new_positions_allowed else 'no'}",
            "",
            f"SPY close:   {md.get('spy_close') or '-'}",
            f"SPY 200MA:   {md.get('spy_200ma') or '-'}",
            f"SPY > 200MA: {md.get('spy_above_200ma')}",
            f"QQQ close:   {md.get('qqq_close') or '-'}",
            f"QQQ 200MA:   {md.get('qqq_200ma') or '-'}",
            f"QQQ > 200MA: {md.get('qqq_above_200ma')}",
            f"VIX:         {md.get('vix') or '-'}",
            f"VIX3M:       {md.get('vix3m') or '-'}",
            f"VIX/VIX3M:   {md.get('vix_vix3m_ratio') or '-'}",
            "",
            f"Missing: {', '.join(md.get('missing_fields') or []) or '-'}",
            f"Reason: {evaluation.reason or '-'}",
        ]
    )
    console.print(
        Panel.fit(
            panel_body,
            title="Market Regime",
            style="cyan" if evaluation.research_scans_allowed else "yellow",
        )
    )

    journal.record_event(
        category="market_regime",
        level="INFO",
        message="regime evaluation",
        payload={
            **evaluation.to_dict(),
            "notes": notes,
        },
    )

    if save:
        out_dir = cfg.absolute("data/market_regime")
        out_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = out_dir / f"{day}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(
                {**evaluation.to_dict(), "notes": notes},
                f, indent=2, default=str,
            )
        console.print(f"[green]Saved:[/green] {path}")

    console.print(
        "[dim]execution_allowed=false. This CLI never places orders "
        "and never modifies broker state.[/dim]"
    )
    raise typer.Exit(code=0)


# ---------------------------------------------------------------------------
# Dynamic watchlist builder (research only)
# ---------------------------------------------------------------------------
def _build_universe_candidates(
    cfg: AppConfig,
    seed_universe: list[str],
    *,
    use_ibkr: bool,
    ibkr_days: int = 60,
    existing_client: IBKRClient | None = None,
) -> tuple[list[WatchlistCandidate], IBKRClient | None, list[str]]:
    """Pull daily bars for each seed symbol and build candidate rows.

    Read-only. Errors per-symbol degrade to an empty candidate so the
    overall build never crashes.
    """
    notes: list[str] = []
    client = existing_client
    if use_ibkr and client is None:
        try:
            client = _connect(cfg)
        except (IBKRClientError, LiveTradingBlocked) as exc:
            notes.append(f"IBKR connect failed: {exc}")
            client = None

    candidates: list[WatchlistCandidate] = []
    for sym in seed_universe:
        bars: list[dict[str, object]] = []
        if client is not None:
            try:
                bars = client.get_daily_bars(sym, days=ibkr_days)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{sym}: bars unavailable ({exc})")
                bars = []
        if not bars:
            candidates.append(
                WatchlistCandidate(symbol=sym.upper(), missing_fields=["bars"])
            )
            continue
        candidates.append(build_candidate_from_bars(sym, bars))
    return candidates, client, notes


@app.command("build-watchlist")
def build_watchlist_cmd(
    use_ibkr: bool = typer.Option(
        False, "--ibkr",
        help="Pull daily bars from IBKR (read-only).",
    ),
    ibkr_days: int = typer.Option(
        60, "--ibkr-days",
        help="Number of daily bars to load per seed symbol.",
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", min=1,
        help="Cap the final dynamic watchlist at this many symbols.",
    ),
    save: bool = typer.Option(True, "--save/--no-save"),
) -> None:
    """Build and save today's dynamic research watchlist.

    Never trades. Never enables execution. Writes to
    ``data/watchlists/YYYY-MM-DD-dynamic-watchlist.json``.
    """
    cfg, journal = _bootstrap()
    dynamic_cfg = dict(cfg.watchlist.get("dynamic") or {})
    if limit is not None:
        dynamic_cfg["max_symbols"] = int(limit)

    seed_universe = list(dynamic_cfg.get("seed_universe") or [])
    if not seed_universe:
        seed_universe = list(cfg.watchlist.get("static_core") or DEFAULT_STATIC_CORE)

    static_core = list(cfg.watchlist.get("static_core") or DEFAULT_STATIC_CORE)

    blocked: list[str] = _blocked_symbols_from_latest_pre_open(cfg)

    client: IBKRClient | None = None
    try:
        candidates, client, notes = _build_universe_candidates(
            cfg, seed_universe, use_ibkr=use_ibkr, ibkr_days=ibkr_days,
        )
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    watchlist = build_dynamic_watchlist(
        universe_candidates=candidates,
        static_core=static_core,
        cfg=dynamic_cfg,
        blocked_symbols=blocked,
        source="ibkr" if use_ibkr else "static",
    )

    table = Table(title=f"Dynamic watchlist — {watchlist.date}")
    for col in (
        "Symbol", "Reasons", "Price", "Cur$Vol", "Avg20D$Vol",
        "RelVol", "Activity", "ATR%", "Score", "Missing",
    ):
        table.add_column(col, overflow="fold")
    for r in watchlist.symbols:
        table.add_row(
            r.symbol,
            ", ".join(sorted(set(r.reason))),
            f"{r.latest_price:.2f}" if isinstance(r.latest_price, (int, float)) else "-",
            f"{r.current_dollar_volume:,.0f}" if r.current_dollar_volume else "-",
            f"{r.avg_20d_dollar_volume:,.0f}" if r.avg_20d_dollar_volume else "-",
            f"{r.relative_volume:.2f}" if r.relative_volume is not None else "-",
            r.volume_activity,
            f"{r.atr_pct:.2f}" if r.atr_pct is not None else "-",
            f"{r.volume_rank_score:.3f}" if r.volume_rank_score is not None else "-",
            ", ".join(sorted(set(r.missing_fields))) or "-",
        )
    console.print(table)

    path: Path | None = None
    export_paths: ExportPaths | None = None
    if save:
        path = save_dynamic_watchlist(cfg, watchlist)
        console.print(f"[green]Saved:[/green] {path}")
        # Always emit the TWS-friendly CSV + TXT next to the JSON so
        # operators can import into TWS without running a second
        # command. Contract validation is intentionally skipped here;
        # use ``export-tws-watchlist --latest --validate --ibkr`` when
        # stronger metadata is needed.
        try:
            export_paths = export_tws_watchlist(
                cfg, watchlist, source_json=path,
            )
            console.print(
                f"[green]TWS CSV:[/green] {export_paths.dated_csv}"
            )
            console.print(
                f"[green]TWS symbols TXT:[/green] {export_paths.dated_txt}"
            )
            console.print(
                f"[dim]latest aliases: {export_paths.latest_csv.name}, "
                f"{export_paths.latest_txt.name}[/dim]"
            )
        except Exception as exc:  # noqa: BLE001 - export must never kill builder
            console.print(
                f"[yellow]TWS watchlist export skipped: {exc!r}[/yellow]"
            )

    journal.record_event(
        category="watchlist_builder",
        level="INFO",
        message="dynamic watchlist built",
        payload={
            "date": watchlist.date,
            "source": watchlist.source,
            "symbols_count": len(watchlist.symbols),
            "blocked_symbols": sum(1 for r in watchlist.symbols if r.blocked),
            "missing_data": watchlist.missing_data,
            "execution_allowed": False,
            "research_only": True,
            "notes": notes,
            "tws_export": (
                export_paths.to_dict() if export_paths is not None else None
            ),
        },
    )
    console.print(
        "[dim]execution_allowed=false. High volume / high beta names "
        "are research candidates only — no trading is enabled.[/dim]"
    )
    raise typer.Exit(code=0)


@app.command("export-tws-watchlist")
def export_tws_watchlist_cmd(
    date: Optional[str] = typer.Option(
        None, "--date",
        help=(
            "Export the dynamic watchlist for this date (YYYY-MM-DD). "
            "Reads data/watchlists/YYYY-MM-DD-dynamic-watchlist.json."
        ),
    ),
    latest: bool = typer.Option(
        False, "--latest",
        help="Export the most recently saved dynamic watchlist.",
    ),
    validate: bool = typer.Option(
        False, "--validate",
        help=(
            "Validate each symbol's contract via IBKR (read-only "
            "qualifyContracts). Combine with --ibkr to open a "
            "connection."
        ),
    ),
    use_ibkr: bool = typer.Option(
        False, "--ibkr",
        help="Open a read-only IBKR connection for --validate.",
    ),
    telegram: bool = typer.Option(
        False, "--telegram",
        help=(
            "Send a Chinese Telegram note listing the export paths so "
            "the operator knows the files are ready to import into TWS."
        ),
    ),
) -> None:
    """Export the dynamic watchlist as TWS-friendly CSV + TXT files.

    This command is **export only**. It never places orders, never
    calls ``broker.place_order``, and never changes IBKR account
    state. The optional ``--validate --ibkr`` path only issues
    ``qualifyContracts``, which is read-only.
    """
    cfg, journal = _bootstrap()
    if not latest and not date:
        console.print(
            "[red]Pass --latest or --date YYYY-MM-DD.[/red]"
        )
        raise typer.Exit(code=2)

    watchlist, source_path = load_watchlist_by_date_or_latest(
        cfg, date=date, latest=latest,
    )
    if watchlist is None:
        console.print(
            "[yellow]No dynamic watchlist found. Run "
            "`python -m bot.cli build-watchlist --ibkr --limit 50` first.[/yellow]"
        )
        raise typer.Exit(code=3)

    validations = None
    client: IBKRClient | None = None
    if validate:
        if not use_ibkr:
            console.print(
                "[yellow]--validate requires --ibkr; exporting with "
                "offline PrimaryExchange inference instead.[/yellow]"
            )
        else:
            try:
                client = _connect(cfg)
                validations = validate_contracts(
                    client,
                    [c.symbol for c in watchlist.symbols if not c.blocked],
                )
            except (IBKRClientError, LiveTradingBlocked, ConnectionError) as exc:
                console.print(
                    f"[yellow]Contract validation skipped: {exc!r}; "
                    "falling back to offline inference.[/yellow]"
                )
            finally:
                if client is not None:
                    try:
                        client.disconnect()
                    except Exception:  # noqa: BLE001
                        pass

    export_paths = export_tws_watchlist(
        cfg, watchlist, validations=validations, source_json=source_path,
    )
    console.print(
        Panel.fit(
            json.dumps(export_paths.to_dict(), indent=2),
            title="export-tws-watchlist",
        )
    )

    if telegram:
        text = (
            "TWS 监视列表已导出（仅研究，不执行）：\n"
            f"- CSV：{export_paths.latest_csv}\n"
            f"- 符号列表：{export_paths.latest_txt}\n"
            f"- 当日 CSV：{export_paths.dated_csv.name}\n"
            f"- 当日 TXT：{export_paths.dated_txt.name}\n"
            f"- 导出条数：{export_paths.row_count}\n"
            f"- IBKR 合约验证：{export_paths.validated_count}\n"
            "请在 TWS Watchlist 中导入 CSV，或复制 TXT symbols 到新的 Watchlist。\n"
            "execution_allowed=false；research_only=true。"
        )
        delivered = send_telegram_message(text, cfg=cfg, journal=journal)
        console.print(
            f"[dim]telegram delivered={delivered}[/dim]"
        )

    journal.record_event(
        category="tws_watchlist_export",
        level="INFO",
        message="tws watchlist exported",
        payload={
            **export_paths.to_dict(),
            "validate": validate,
            "ibkr_used_for_validation": validate and use_ibkr,
        },
    )
    raise typer.Exit(code=0)


def _blocked_symbols_from_latest_pre_open(cfg: AppConfig) -> list[str]:
    pre = cfg.absolute("data/pre_open_news")
    if not pre.is_dir():
        return []
    files = sorted(pre.glob("*.json"))
    if not files:
        return []
    try:
        payload = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return list(payload.get("blocked_symbols") or [])


# ---------------------------------------------------------------------------
# SMC Liquidity Reversal — research / dry-run only (V0)
# ---------------------------------------------------------------------------
# These commands NEVER place orders. The evaluator forces
# ``execution_allowed=False`` regardless of CLI flags. The CSV path is the
# primary input; ``--ibkr`` is opt-in and only fetches OHLCV bars (read-only).


_REQUIRED_CSV_COLUMNS = {"timestamp", "open", "high", "low", "close"}


def _load_candles_from_csv(path: Path) -> list[dict[str, object]]:
    """Load OHLCV rows from a CSV file with a header row.

    Required columns: ``timestamp,open,high,low,close``. ``volume`` is
    optional. Rows with empty / non-numeric prices are skipped silently
    so a partial download still produces a usable evaluation.
    """
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return []
        missing = _REQUIRED_CSV_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"CSV {path.name} missing required columns: "
                f"{sorted(missing)}; have {reader.fieldnames}"
            )
        for raw in reader:
            try:
                rows.append(
                    {
                        "timestamp": (raw.get("timestamp") or "").strip(),
                        "open": float(raw["open"]),
                        "high": float(raw["high"]),
                        "low": float(raw["low"]),
                        "close": float(raw["close"]),
                        "volume": float(raw.get("volume") or 0.0),
                    }
                )
            except (TypeError, ValueError):
                continue
    return rows


def _load_regime_snapshot(cfg: AppConfig) -> dict[str, object] | None:
    """Return the most recent regime snapshot, or ``None``.

    Preference order:

    1. ``data/market_regime/*.json`` — written by
       ``python -m bot.cli market-regime``; this is the fresh source
       of truth because the operator explicitly asked for it.
    2. ``data/pre_open_news/*.json`` — the fallback. These files have
       the same schema for the regime fields so the caller can treat
       them interchangeably.

    The returned dict always has these top-level keys (any of which
    may be ``None`` / empty when the source file was incomplete):

        market_regime, regime_confidence, new_positions_allowed,
        research_scans_allowed, market_data, source_file.

    ``source_file`` is the absolute path (string) the snapshot came
    from so callers can surface it for debugging.
    """

    def _normalise(payload: dict[str, object], source: Path) -> dict[str, object]:
        md = payload.get("market_data") or {}
        return {
            "market_regime": payload.get("market_regime"),
            "regime_confidence": payload.get("regime_confidence", "low"),
            "new_positions_allowed": bool(
                payload.get("new_positions_allowed", False)
            ),
            "research_scans_allowed": bool(
                payload.get("research_scans_allowed", False)
            ),
            "market_data": md if isinstance(md, dict) else {},
            "source_file": str(source),
        }

    reg_dir = cfg.absolute("data/market_regime")
    if reg_dir.is_dir():
        files = sorted(reg_dir.glob("*.json"))
        if files:
            try:
                payload = json.loads(files[-1].read_text(encoding="utf-8"))
                return _normalise(payload, files[-1])
            except (OSError, json.JSONDecodeError):
                pass

    pre_dir = cfg.absolute("data/pre_open_news")
    if pre_dir.is_dir():
        files = sorted(pre_dir.glob("*.json"))
        if files:
            try:
                payload = json.loads(files[-1].read_text(encoding="utf-8"))
                return _normalise(payload, files[-1])
            except (OSError, json.JSONDecodeError):
                return None
    return None


def _resolve_regime_context(
    cfg: AppConfig, override: str | None
) -> dict[str, object]:
    """Resolve the full regime context for SMC scans.

    Order of precedence for the label:

    1. CLI ``--market-regime`` override (unchanged from previous behaviour).
    2. Latest ``data/market_regime/*.json`` snapshot.
    3. Latest ``data/pre_open_news/*.json`` file.
    4. Literal ``"neutral"`` as a final fallback.

    Returns a dict with the same keys as
    :func:`_load_regime_snapshot` plus a guaranteed non-None
    ``market_regime`` string. When the override forces a label, the
    confidence is downgraded to ``"medium"`` unless the snapshot
    already has a higher value for the same regime; that way a
    manually-chosen regime does not over-promise on certainty.
    """
    snapshot = _load_regime_snapshot(cfg) or {}
    missing = list(
        (snapshot.get("market_data") or {}).get("missing_fields") or []
    )
    context: dict[str, object] = {
        "market_regime": snapshot.get("market_regime") or "neutral",
        "regime_confidence": snapshot.get("regime_confidence") or (
            "low" if not snapshot else "medium"
        ),
        "new_positions_allowed": bool(snapshot.get("new_positions_allowed", False)),
        "research_scans_allowed": bool(
            snapshot.get("research_scans_allowed", True if snapshot else True)
        ),
        "regime_missing_fields": missing,
        "market_data": snapshot.get("market_data") or {},
        "source_file": snapshot.get("source_file"),
    }
    # Apply the override last so the operator always wins.
    if override:
        context["market_regime"] = override.strip().lower()
        context["source_file"] = "cli_override"
        # A manual label is only as strong as the operator believes it
        # is; keep confidence at `medium` to avoid implying the
        # snapshot agreed.
        context["regime_confidence"] = "medium"
    return context


def _resolve_market_regime(cfg: AppConfig, override: str | None) -> str:
    """Back-compat shim — returns just the label used by the evaluator."""
    return str(_resolve_regime_context(cfg, override).get("market_regime") or "neutral")


def _save_smc_setup(cfg: AppConfig, evaluation: StrategyEvaluation) -> Path:
    """Persist the evaluation under ``data/smc_setups/YYYY-MM-DD-SYMBOL.json``."""
    out_dir = cfg.absolute("data/smc_setups")
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe_symbol = evaluation.symbol.upper().replace("/", "_")
    path = out_dir / f"{today}-{safe_symbol}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(evaluation.to_dict(), f, indent=2, sort_keys=True)
    return path


def _format_yes_no(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "FOUND" if value else "NOT FOUND"
    return str(value)


def _print_smc_evaluation(evaluation: StrategyEvaluation) -> None:
    """Render the structured CLI output described in section 17 of the brief."""
    seq = evaluation.sequence
    plan = evaluation.trade_plan or {}
    lines: list[str] = []
    lines.append(f"Strategy: {evaluation.strategy}")
    lines.append(f"Symbol: {evaluation.symbol}")
    lines.append(f"Timeframe: {evaluation.timeframe}")
    lines.append(f"Market regime: {evaluation.market_regime or '-'}")
    lines.append(f"Candles loaded: {evaluation.candle_count}")
    lines.append("")
    lines.append("Sequence:")
    lines.append(
        f"- Liquidity Sweep: "
        f"{_format_yes_no(seq.get('sweep', {}).get('found'))}"
    )
    lines.append(
        f"- ChoCH: {_format_yes_no(seq.get('choch', {}).get('found'))}"
    )
    lines.append(
        f"- Bullish FVG: {_format_yes_no(seq.get('fvg', {}).get('found'))}"
    )
    lines.append(
        f"- Order Block: "
        f"{_format_yes_no(seq.get('order_block', {}).get('found'))}"
    )
    lines.append("")
    lines.append("Dry-run plan:")
    if plan:
        lines.append(f"- Limit entry: {plan.get('entry_price', '-')} "
                     f"({plan.get('entry_type', '-')})")
        lines.append(f"- Structural stop: {plan.get('structural_stop', '-')}")
        lines.append(f"- Stop distance: {plan.get('stop_distance_pct', '-')}%")
        lines.append(f"- Target 1: {plan.get('target_1', '-')}")
        lines.append(f"- R/R: {plan.get('risk_reward_to_target_1', '-')}")
        lines.append(f"- Qty by 1% risk: {plan.get('qty_by_risk', '-')}")
        if plan.get("extension_pct_vs_latest_close") is not None:
            lines.append(
                f"- Latest close vs entry: "
                f"{plan['extension_pct_vs_latest_close']}%"
            )
    else:
        lines.append("- (not built; structural sequence incomplete)")
    lines.append("")
    lines.append("Execution:")
    lines.append("- Disabled (execution_allowed=false)")
    lines.append("- Research mode only")
    if evaluation.rejection_reasons:
        lines.append("")
        lines.append("Rejection reasons:")
        for r in evaluation.rejection_reasons:
            lines.append(f"- {r}")
    if evaluation.notes:
        lines.append("")
        lines.append("Notes:")
        for n in evaluation.notes:
            lines.append(f"- {n}")
    style = "green" if evaluation.approved_for_dry_run else "yellow"
    title = (
        f"SMC dry-run plan: {evaluation.symbol} (APPROVED for paper review)"
        if evaluation.approved_for_dry_run
        else f"SMC dry-run plan: {evaluation.symbol} (REJECTED)"
    )
    console.print(Panel.fit("\n".join(lines), title=title, style=style))


def _gather_candles(
    cfg: AppConfig,
    symbol: str,
    *,
    csv_path: Path | None,
    use_ibkr: bool,
    ibkr_days: int,
    client: IBKRClient | None = None,
    timeframe_spec: Any | None = None,
) -> tuple[list[dict[str, object]], str, IBKRClient | None]:
    """Load OHLCV candles for ``symbol``.

    Returns ``(rows, source, client)``. When ``client`` is provided we
    reuse it (useful for batch scanning); otherwise we may open a new
    one when ``--ibkr`` is requested.

    ``timeframe_spec`` (a :class:`bot.smc_timeframes.TimeframeSpec`)
    routes intraday timeframes through :meth:`IBKRClient.
    get_bars_for_timeframe`. Legacy callers may omit it — the daily
    path with ``--ibkr-days`` is preserved for backward compatibility.
    """
    if csv_path is not None:
        return _load_candles_from_csv(csv_path), f"csv:{csv_path}", client
    if use_ibkr:
        if client is None:
            client = _connect(cfg)
        if timeframe_spec is not None:
            rows = client.get_bars_for_timeframe(symbol, timeframe_spec)
            src = (
                f"ibkr:{timeframe_spec.bar_size}"
                if getattr(timeframe_spec, "is_intraday", False)
                else f"ibkr:{timeframe_spec.bar_size}@{timeframe_spec.duration}"
            )
            return rows, src, client
        rows = client.get_daily_bars(symbol, days=ibkr_days)
        return rows, "ibkr", client
    raise typer.BadParameter(
        "Provide --csv PATH or --ibkr to load candles."
    )


def _resolve_account_values(
    cfg: AppConfig,
    *,
    account_equity: float | None,
    available_cash: float | None,
    use_account_values: bool,
    client: IBKRClient | None,
) -> tuple[float | None, float | None, IBKRClient | None, str]:
    """Resolve the ``(equity, cash)`` pair used for sizing.

    Order of precedence:
      1. CLI overrides (``--account-equity``/``--available-cash``).
      2. ``--use-account-values`` → IBKR paper account summary
         (read-only, never places orders).
      3. ``(None, None)`` so the evaluator skips qty sizing.

    The opt-in IBKR call respects privacy mode by never echoing raw
    account numbers when ``settings.notifications.telegram.privacy_mode``
    is true (we still display them in the Rich panel for the local
    operator since the privacy guarantee is about Telegram).
    """
    source = "none"
    if account_equity is not None or available_cash is not None:
        return account_equity, available_cash, client, "cli_override"
    if not use_account_values:
        return None, None, client, source

    if client is None:
        client = _connect(cfg)
    summaries = client.get_account_summary()
    if not summaries:
        console.print(
            "[yellow]--use-account-values: IBKR returned no account "
            "summary; sizing will be skipped.[/yellow]"
        )
        return None, None, client, "ibkr_empty"
    head = summaries[0]
    equity = head.net_liquidation
    cash = head.available_funds if head.available_funds is not None else head.total_cash
    return equity, cash, client, "ibkr_account"


def _render_chart_for(
    cfg: AppConfig,
    evaluation: StrategyEvaluation,
    rows: list[dict[str, object]],
) -> Path | None:
    """Render the validation PNG and stamp ``chart_path`` on the evaluation.

    Returns the path so the CLI can echo it. Failures are logged and
    swallowed so the JSON pipeline keeps working. The renderer never
    touches the broker.
    """
    try:
        from .market_structure import (
            candles_from_records,
            detect_swing_highs,
            detect_swing_lows,
        )
        from .smc_chart import render_smc_chart
    except RuntimeError as exc:
        console.print(f"[yellow]chart skipped: {exc}[/yellow]")
        return None
    candles = candles_from_records(rows)
    output_dir = cfg.absolute("data/debug_charts")
    try:
        chart_path = render_smc_chart(
            evaluation,
            candles,
            output_dir=output_dir,
            swings_high=detect_swing_highs(candles),
            swings_low=detect_swing_lows(candles),
        )
    except Exception as exc:  # noqa: BLE001 - chart is best-effort
        console.print(f"[red]chart render failed: {exc}[/red]")
        return None
    evaluation.chart_path = str(chart_path)
    return chart_path


@app.command("scan-smc")
def scan_smc(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Ticker, e.g. AAPL."),
    timeframe: str = typer.Option(
        "daily",
        "--timeframe",
        help=(
            "Bar timeframe: 'daily' or '30min'. 30min fetches "
            "IBKR intraday bars (RTH only) and applies stricter "
            "risk thresholds (see docs/30min-smc-test-mode.md). "
            "Research-only; no execution."
        ),
    ),
    csv_path: Optional[Path] = typer.Option(
        None,
        "--csv",
        exists=False,
        help=(
            "Path to a CSV with header "
            "timestamp,open,high,low,close[,volume]. Primary V0 input."
        ),
    ),
    use_ibkr: bool = typer.Option(
        False,
        "--ibkr",
        help=(
            "Fetch daily bars from IBKR (read-only). Requires a "
            "running paper TWS/Gateway."
        ),
    ),
    ibkr_days: int = typer.Option(
        300, "--ibkr-days", help="Days of history when --ibkr is set."
    ),
    market_regime: Optional[str] = typer.Option(
        None,
        "--market-regime",
        help=(
            "Override market regime; defaults to the regime in the most "
            "recent pre_open_news report, or 'neutral' if none."
        ),
    ),
    account_equity: Optional[float] = typer.Option(
        None,
        "--account-equity",
        help="Account equity used for risk-based position sizing.",
    ),
    available_cash: Optional[float] = typer.Option(
        None,
        "--available-cash",
        help=(
            "Cash available for the dry-run plan. Currently informational "
            "only; sizing uses risk + per-position cap."
        ),
    ),
    use_account_values: bool = typer.Option(
        False,
        "--use-account-values",
        help=(
            "Read account equity/cash from IBKR (paper). Read-only; "
            "still does not place any orders."
        ),
    ),
    chart: bool = typer.Option(
        False,
        "--chart",
        help=(
            "Also render a validation PNG under data/debug_charts/. "
            "Charts are rendered even for rejected setups so reviewers "
            "can see what was (and was not) detected."
        ),
    ),
    save: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Save the structured evaluation under data/smc_setups/.",
    ),
) -> None:
    """Run the SMC liquidity-reversal *research* evaluator on one symbol.

    Never places orders. The evaluator hard-codes
    ``execution_allowed=false``. Output is JSON and a human-readable
    panel; the JSON is also persisted under ``data/smc_setups/`` unless
    ``--no-save`` is used. With ``--chart`` we additionally write a PNG
    to ``data/debug_charts/`` for visual validation.
    """
    from .smc_timeframes import normalise_timeframe, resolve_timeframe_spec

    cfg, journal = _bootstrap()
    timeframe = normalise_timeframe(timeframe)
    tf_spec = resolve_timeframe_spec(timeframe, cfg)
    regime_ctx = _resolve_regime_context(cfg, market_regime)
    regime = str(regime_ctx["market_regime"])

    # Informational only: surface confidence + missing fields so the
    # reviewer can tell at a glance whether the regime is trustworthy.
    regime_conf = regime_ctx["regime_confidence"]
    regime_missing = regime_ctx.get("regime_missing_fields") or []
    src = regime_ctx.get("source_file")
    console.print(
        f"[dim]timeframe={timeframe} duration={tf_spec.duration} "
        f"bar_size={tf_spec.bar_size} RTH={tf_spec.use_rth}[/dim]"
    )
    console.print(
        f"[dim]regime={regime} confidence={regime_conf}"
        + (f" missing={','.join(regime_missing)}" if regime_missing else "")
        + (f" source={Path(src).name}" if src and src != "cli_override" else "")
        + (" source=cli_override" if src == "cli_override" else "")
        + "[/dim]"
    )

    client: IBKRClient | None = None
    try:
        try:
            rows, source, client = _gather_candles(
                cfg,
                symbol,
                csv_path=csv_path,
                use_ibkr=use_ibkr,
                ibkr_days=ibkr_days,
                client=client,
                timeframe_spec=tf_spec,
            )
        except (FileNotFoundError, ValueError) as exc:
            console.print(
                f"[red]Failed to load candles for {symbol}: {exc}[/red]"
            )
            raise typer.Exit(code=6)

        if not rows:
            console.print(
                f"[yellow]No candles loaded for {symbol} from {source}; "
                "nothing to evaluate.[/yellow]"
            )
            raise typer.Exit(code=7)

        equity, cash, client, equity_source = _resolve_account_values(
            cfg,
            account_equity=account_equity,
            available_cash=available_cash,
            use_account_values=use_account_values,
            client=client,
        )

        latest_close = float(rows[-1].get("close") or 0.0) or None
        evaluation = evaluate_smc_liquidity_reversal(
            symbol=symbol.upper(),
            candles=rows,
            timeframe=timeframe,
            cfg=cfg,
            market_regime=regime,
            account_equity=equity,
            latest_close=latest_close,
        )
        chart_path = _render_chart_for(cfg, evaluation, rows) if chart else None

        _print_smc_evaluation(evaluation)
        if equity_source != "none":
            console.print(
                f"[dim]sizing source: {equity_source}; equity={equity}, "
                f"cash={cash}[/dim]"
            )
        if chart_path:
            console.print(f"[green]Saved chart:[/green] {chart_path}")

        json_path: Path | None = None
        if save:
            json_path = _save_smc_setup(cfg, evaluation)
            console.print(f"[green]Saved JSON:[/green] {json_path}")

    finally:
        if client is not None:
            client.disconnect()

    journal.record_event(
        category="smc_research",
        level="INFO",
        message="scan-smc evaluated",
        payload={
            "symbol": evaluation.symbol,
            "timeframe": evaluation.timeframe,
            "approved_for_dry_run": evaluation.approved_for_dry_run,
            "execution_allowed": evaluation.execution_allowed,
            "market_regime": evaluation.market_regime,
            "candle_source": source,
            "candle_count": evaluation.candle_count,
            "rejection_reasons": evaluation.rejection_reasons,
            "json_path": str(json_path) if json_path else None,
            "chart_path": evaluation.chart_path,
            "equity_source": equity_source,
        },
    )
    raise typer.Exit(code=0)


@app.command("scan-smc-watchlist")
def scan_smc_watchlist(
    timeframe: str = typer.Option(
        "daily",
        "--timeframe",
        help=(
            "Bar timeframe: 'daily' or '30min'. 30min fetches "
            "IBKR intraday bars (RTH only) and applies stricter "
            "risk thresholds. Research-only; no execution."
        ),
    ),
    candles_dir: Optional[Path] = typer.Option(
        None,
        "--candles-dir",
        help=(
            "Directory of CSVs named <SYMBOL>.csv. Symbols missing a "
            "CSV are skipped (or pulled from IBKR when --ibkr is set)."
        ),
    ),
    use_ibkr: bool = typer.Option(
        False,
        "--ibkr",
        help="Pull daily bars from IBKR for any symbol not in --candles-dir.",
    ),
    ibkr_days: int = typer.Option(300, "--ibkr-days"),
    market_regime: Optional[str] = typer.Option(None, "--market-regime"),
    account_equity: Optional[float] = typer.Option(None, "--account-equity"),
    available_cash: Optional[float] = typer.Option(None, "--available-cash"),
    use_account_values: bool = typer.Option(
        False,
        "--use-account-values",
        help="Read account equity/cash from IBKR (paper). Read-only.",
    ),
    chart: bool = typer.Option(
        False,
        "--chart",
        help="Also render a validation PNG per symbol under data/debug_charts/.",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        min=1,
        help="Stop after evaluating this many symbols (e.g. --limit 10).",
    ),
    telegram: bool = typer.Option(
        False,
        "--telegram",
        help=(
            "Send a concise research digest to Telegram after the scan. "
            "Research-only; no trading signal is implied."
        ),
    ),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        help=(
            "Which symbol list to scan: 'static' (config/watchlist.yaml) "
            "or 'dynamic' (today's dynamic watchlist, rebuilt on demand). "
            "Defaults to ``default_source`` in config/watchlist.yaml."
        ),
    ),
    save: bool = typer.Option(True, "--save/--no-save"),
) -> None:
    """Run :command:`scan-smc` over every symbol in the chosen watchlist.

    With ``--chart`` each symbol also gets a PNG saved under
    ``data/debug_charts/``. With ``--telegram`` a short research
    digest is pushed to the operator's Telegram chat so reviewers can
    flip through the annotated charts before any execution version is
    unlocked.
    """
    from .smc_scanner import (
        BUCKETS,
        ScanBatch,
        build_scan_row,
        format_telegram_digest,
        save_batch_summary,
        today_utc_iso,
    )
    from .smc_timeframes import normalise_timeframe, resolve_timeframe_spec
    from .strategy_engine import DEFAULT_STRATEGY_CFG

    cfg, journal = _bootstrap()
    timeframe = normalise_timeframe(timeframe)
    tf_spec = resolve_timeframe_spec(timeframe, cfg)
    regime_ctx = _resolve_regime_context(cfg, market_regime)
    regime = str(regime_ctx["market_regime"])

    chosen_source = (source or str(cfg.watchlist.get("default_source") or "static")).lower()
    if chosen_source not in {"static", "dynamic"}:
        console.print(
            f"[red]--source must be 'static' or 'dynamic', got {source!r}.[/red]"
        )
        raise typer.Exit(code=2)

    symbols: list[str] = []
    dyn_watchlist: DynamicWatchlist | None = None
    if chosen_source == "dynamic":
        dyn_watchlist = load_dynamic_watchlist(cfg)
        if dyn_watchlist is None:
            console.print(
                "[yellow]No dynamic watchlist for today; building one now.[/yellow]"
            )
            dynamic_cfg = dict(cfg.watchlist.get("dynamic") or {})
            seed_universe = list(
                dynamic_cfg.get("seed_universe")
                or cfg.watchlist.get("static_core")
                or DEFAULT_STATIC_CORE
            )
            static_core_list = list(
                cfg.watchlist.get("static_core") or DEFAULT_STATIC_CORE
            )
            prebuild_client: IBKRClient | None = None
            try:
                cands, prebuild_client, _notes = _build_universe_candidates(
                    cfg, seed_universe, use_ibkr=use_ibkr, ibkr_days=60,
                )
            finally:
                if prebuild_client is not None:
                    try:
                        prebuild_client.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
            dyn_watchlist = build_dynamic_watchlist(
                universe_candidates=cands,
                static_core=static_core_list,
                cfg=dynamic_cfg,
                blocked_symbols=_blocked_symbols_from_latest_pre_open(cfg),
                source="ibkr" if use_ibkr else "static",
            )
            if save:
                save_dynamic_watchlist(cfg, dyn_watchlist)
        # Filter out blocked rows before handing to SMC.
        symbols = [r.symbol for r in dyn_watchlist.symbols if not r.blocked]
    else:
        equities = (cfg.watchlist or {}).get("equities") or []
        symbols = [e.get("symbol") for e in equities if e.get("symbol")]
        if not symbols:
            symbols = list(cfg.watchlist.get("static_core") or [])

    if not symbols:
        console.print(
            "[yellow]Watchlist is empty; nothing to scan. "
            "Add symbols to config/watchlist.yaml (static_core / equities) "
            "or build a dynamic watchlist first.[/yellow]"
        )
        raise typer.Exit(code=0)
    if limit is not None:
        symbols = symbols[:limit]

    # Resolve the live strategy block once so we score against whatever
    # is in config/strategy.yaml rather than the compiled defaults.
    strategy_block = (
        getattr(cfg, "strategies", None) or {}
    ).get(SMC_STRATEGY_NAME) or DEFAULT_STRATEGY_CFG

    # Use the unified regime context so console header, per-symbol
    # evaluation, batch summary JSON, and Telegram digest all read
    # from the same snapshot. This is what keeps the scanner in sync
    # with ``data/market_regime/*.json``.
    regime_conf = str(regime_ctx.get("regime_confidence") or "low")
    regime_missing = list(regime_ctx.get("regime_missing_fields") or [])
    regime_src = regime_ctx.get("source_file")
    new_pos_allowed = bool(regime_ctx.get("new_positions_allowed", False))
    research_allowed = bool(regime_ctx.get("research_scans_allowed", True))

    batch = ScanBatch(
        date=today_utc_iso(),
        timeframe=timeframe,
        market_regime=regime,
        regime_confidence=regime_conf,
        regime_missing_fields=regime_missing,
        research_scans_allowed=research_allowed,
        new_positions_allowed=new_pos_allowed,
        regime_source=(
            Path(regime_src).name if regime_src and regime_src != "cli_override"
            else regime_src
        ),
    )

    if not regime_src:
        # Nothing under data/market_regime/ or data/pre_open_news/. We
        # print a clear notice so the reviewer does not mistake the
        # fallback label for a real evaluation.
        console.print(
            "[yellow]No market-regime snapshot found under "
            "data/market_regime/ or data/pre_open_news/. "
            "Using fallback regime=neutral confidence=low; "
            "run `python -m bot.cli market-regime --ibkr` for a "
            "deterministic SPY/QQQ evaluation.[/yellow]"
        )
    regime_bits = [
        f"timeframe={timeframe}",
        f"regime={regime}",
        f"confidence={regime_conf}",
    ]
    if regime_missing:
        regime_bits.append("missing=" + ",".join(regime_missing))
    regime_bits.append(f"new_pos={'yes' if new_pos_allowed else 'no'}")
    if chosen_source:
        regime_bits.append(f"source={chosen_source}")
    table = Table(
        title=f"SMC scan ({SMC_STRATEGY_NAME}) — " + " | ".join(regime_bits)
    )
    for col in (
        "Symbol", "Bucket", "Score", "Sweep", "ChoCH", "FVG", "OB",
        "Entry", "Stop", "T1", "R/R", "Reject reasons", "Chart",
    ):
        table.add_column(col, overflow="fold")

    client: IBKRClient | None = None
    try:
        equity_source = "none"
        equity = account_equity
        cash = available_cash
        if account_equity is None and available_cash is None and use_account_values:
            equity, cash, client, equity_source = _resolve_account_values(
                cfg,
                account_equity=None,
                available_cash=None,
                use_account_values=True,
                client=client,
            )
        elif account_equity is not None or available_cash is not None:
            equity_source = "cli_override"

        for symbol in symbols:
            csv_path: Path | None = None
            if candles_dir is not None:
                candidate = candles_dir / f"{symbol}.csv"
                if candidate.exists():
                    csv_path = candidate

            rows: list[dict[str, object]] = []
            source = "missing"
            if csv_path is not None:
                try:
                    rows = _load_candles_from_csv(csv_path)
                    source = f"csv:{csv_path.name}"
                except (FileNotFoundError, ValueError) as exc:
                    table.add_row(
                        symbol, "-", "-", "-", "-", "-", "-", "-", "-",
                        "-", "-", f"csv_error: {exc}", "-",
                    )
                    continue
            elif use_ibkr:
                if client is None:
                    client = _connect(cfg)
                rows = client.get_bars_for_timeframe(symbol, tf_spec)
                source = (
                    f"ibkr:{tf_spec.bar_size}"
                    if tf_spec.is_intraday
                    else f"ibkr:{tf_spec.bar_size}@{tf_spec.duration}"
                )

            if not rows:
                table.add_row(
                    symbol, "-", "-", "-", "-", "-", "-", "-", "-",
                    "-", "-", "no_candles", "-",
                )
                continue

            latest_close = float(rows[-1].get("close") or 0.0) or None
            evaluation = evaluate_smc_liquidity_reversal(
                symbol=symbol.upper(),
                candles=rows,
                timeframe=timeframe,
                cfg=cfg,
                market_regime=regime,
                account_equity=equity,
                latest_close=latest_close,
            )
            chart_path = (
                _render_chart_for(cfg, evaluation, rows) if chart else None
            )
            row = build_scan_row(
                evaluation,
                cfg_strategy_block=strategy_block,
                candle_source=source,
                chart_path=str(chart_path) if chart_path else None,
            )
            batch.rows.append(row)

            seq = evaluation.sequence
            plan = evaluation.trade_plan or {}
            table.add_row(
                symbol,
                row.bucket,
                str(row.smc_quality_score),
                "Y" if seq["sweep"].get("found") else "n",
                "Y" if seq["choch"].get("found") else "n",
                "Y" if seq["fvg"].get("found") else "n",
                "Y" if seq["order_block"].get("found") else "n",
                f"{plan.get('entry_price'):.2f}" if isinstance(
                    plan.get("entry_price"), (int, float)) else "-",
                f"{plan.get('structural_stop'):.2f}" if isinstance(
                    plan.get("structural_stop"), (int, float)) else "-",
                f"{plan.get('target_1'):.2f}" if isinstance(
                    plan.get("target_1"), (int, float)) else "-",
                f"{plan.get('risk_reward_to_target_1'):.2f}" if isinstance(
                    plan.get("risk_reward_to_target_1"), (int, float)) else "-",
                ", ".join(evaluation.rejection_reasons) or "-",
                str(chart_path) if chart_path else "-",
            )
            if save:
                _save_smc_setup(cfg, evaluation)
            journal.record_event(
                category="smc_research",
                level="INFO",
                message="scan-smc-watchlist row",
                payload={
                    "symbol": evaluation.symbol,
                    "bucket": row.bucket,
                    "smc_quality_score": row.smc_quality_score,
                    "approved_for_dry_run": evaluation.approved_for_dry_run,
                    "execution_allowed": evaluation.execution_allowed,
                    "market_regime": evaluation.market_regime,
                    "candle_source": source,
                    "rejection_reasons": evaluation.rejection_reasons,
                    "chart_path": evaluation.chart_path,
                    "equity_source": equity_source,
                },
            )
    finally:
        if client is not None:
            client.disconnect()

    console.print(table)

    summary_path: Path | None = None
    if save and batch.rows:
        summary_path = save_batch_summary(cfg, batch)
        console.print(f"[green]Saved batch summary:[/green] {summary_path}")

    if telegram and batch.rows:
        from .notifications.telegram import send_telegram_message

        parse_mode = cfg.settings.notifications.telegram.parse_mode
        if chosen_source == "dynamic":
            source_label = "dynamic high-volume / high-beta universe"
        else:
            source_label = "static config/watchlist.yaml"
        digest = format_telegram_digest(
            batch,
            parse_mode=parse_mode,
            source_label=source_label,
        )
        delivered = send_telegram_message(digest, cfg=cfg, journal=journal)
        if delivered:
            console.print("[green]Telegram digest sent.[/green]")
        else:
            console.print(
                "[yellow]Telegram digest was not delivered; see "
                "memory/DAILY-SUMMARY.md for the fallback copy.[/yellow]"
            )

    counts = batch.bucket_counts()
    console.print(
        "[dim]Summary: "
        + "  ".join(f"{b}={counts.get(b, 0)}" for b in BUCKETS)
        + "[/dim]"
    )
    console.print(
        "[dim]Reminder: SMC_LIQUIDITY_REVERSAL_RESEARCH is a "
        "research/dry-run module. Digest and score are research "
        "ranking — not trade approval. Execution is disabled.[/dim]"
    )
    raise typer.Exit(code=0)


# ---------------------------------------------------------------------------
# SMC review queue (Prompt 9 Part A)
# ---------------------------------------------------------------------------
@app.command("smc-review-queue")
def smc_review_queue_cmd(
    date: Optional[str] = typer.Option(
        None, "--date",
        help="YYYY-MM-DD. Defaults to the most recent scan summary on disk.",
    ),
    timeframe: str = typer.Option(
        "daily",
        "--timeframe",
        help=(
            "Timeframe of the scan summary to build the queue from: "
            "'daily' or '30min'. Prompt 10A; research-only."
        ),
    ),
    source: str = typer.Option(
        "latest", "--source",
        help="Currently only 'latest' is supported; kept for future sources.",
    ),
    top: int = typer.Option(
        10, "--top", min=1,
        help="Number of top review items to show in markdown / digest.",
    ),
    include_charts: bool = typer.Option(
        False, "--include-charts",
        help=(
            "Include chart filenames in the review queue output. Paths "
            "are shortened to the filename for readability."
        ),
    ),
    min_score: int = typer.Option(
        0, "--min-score", min=0,
        help="Drop IGNORE_FOR_NOW items below this priority score.",
    ),
    telegram: bool = typer.Option(
        False, "--telegram",
        help="Send the digest to Telegram (falls back to DAILY-SUMMARY.md).",
    ),
    markdown: bool = typer.Option(
        False, "--markdown",
        help="Append a markdown block to memory/SMC-REVIEW-QUEUE.md.",
    ),
    save: bool = typer.Option(
        True, "--save/--no-save",
        help="Save JSON under data/review_queue/.",
    ),
) -> None:
    """Build a research review queue from the latest SMC scan summary.

    Never places orders. Reads
    ``data/smc_setups/YYYY-MM-DD-watchlist-summary.json`` and writes
    ``data/review_queue/YYYY-MM-DD-smc-review-queue.json``. The
    ``execution_allowed`` flag is hard-coded to ``false`` on the
    envelope and every item.
    """
    from .review_queue import (
        DEFAULT_THRESHOLDS,
        append_markdown as append_review_markdown,
        build_review_queue,
        format_telegram_digest as format_review_digest,
        load_latest_summary,
        save_review_queue,
        SummaryNotFoundError,
    )
    from .smc_timeframes import normalise_timeframe
    from .strategy_engine import DEFAULT_STRATEGY_CFG

    cfg, journal = _bootstrap()
    timeframe = normalise_timeframe(timeframe)

    if source != "latest":
        console.print(
            f"[red]Unsupported --source {source!r}; only 'latest' is "
            "implemented.[/red]"
        )
        raise typer.Exit(code=2)

    try:
        summary, summary_path = load_latest_summary(
            cfg, date=date, timeframe=timeframe
        )
    except SummaryNotFoundError as exc:
        console.print(
            f"[red]No SMC scan summary found:[/red] {exc}\n"
            "[dim]Run `python -m bot.cli scan-smc-watchlist --source "
            "dynamic --ibkr` first, then retry.[/dim]"
        )
        raise typer.Exit(code=6)

    rq_cfg = dict(cfg.review_queue or {})
    thresholds = {
        **DEFAULT_THRESHOLDS,
        **(rq_cfg.get("thresholds") or {}),
    }
    max_items = int(rq_cfg.get("max_items") or 50)
    include_categories = rq_cfg.get("include_categories") or None
    effective_min = max(min_score, int(rq_cfg.get("min_review_priority_score") or 0))

    strategy_block = (
        getattr(cfg, "strategies", None) or {}
    ).get(SMC_STRATEGY_NAME) or DEFAULT_STRATEGY_CFG

    now_et_hhmm = _now_et_hhmm()

    queue = build_review_queue(
        summary,
        source_path=summary_path,
        thresholds=thresholds,
        max_items=max_items,
        min_review_priority_score=effective_min,
        include_categories=include_categories,
        timeframe=timeframe,
        now_et_hhmm=now_et_hhmm,
        strategy_block=strategy_block,
    )

    # Console output
    title = (
        f"SMC Review Queue — {queue.date} ({queue.timeframe}) | "
        f"regime={queue.market_regime} "
        f"confidence={queue.regime_confidence}"
        + (" | missing=" + ",".join(queue.regime_missing_fields)
           if queue.regime_missing_fields else "")
    )
    table = Table(title=title)
    for col in (
        "Symbol", "Review Category", "Priority", "Entry", "Stop",
        "T1", "R/R", "Extension", "Reason", "Chart",
    ):
        table.add_column(col, overflow="fold")

    for item in queue.top_items(top):
        tp = item.trade_plan
        chart_cell = "-"
        if include_charts and item.chart_path:
            chart_cell = Path(item.chart_path).name
        table.add_row(
            item.symbol,
            item.review_category,
            str(item.review_priority_score),
            _fmt_num(tp.get("entry_price")),
            _fmt_num(tp.get("structural_stop")),
            _fmt_num(tp.get("target_1")),
            _fmt_num(tp.get("risk_reward_to_target_1")),
            _fmt_num(tp.get("extension_pct_vs_latest_close"), suffix="%"),
            item.human_review_reason,
            chart_cell,
        )
    console.print(table)
    counts = queue.counts()
    console.print(
        "[dim]Counts: "
        + "  ".join(f"{c}={counts.get(c, 0)}" for c in counts)
        + "[/dim]"
    )

    # Tradeable candidates are a privileged subset; call them out
    # explicitly so the reviewer cannot miss them and they are not
    # labelled "trade signals".
    tradeable = queue.tradeable_candidates()
    if tradeable:
        console.print(
            "[bold cyan]ICT/SMC tradeable candidates (manual review only):"
            f"[/bold cyan] {', '.join(i.symbol for i in tradeable)}"
        )
    else:
        console.print(
            "[dim]No ICT/SMC tradeable candidates found. "
            "Research only. No orders placed.[/dim]"
        )

    json_path: Path | None = None
    if save:
        json_path = save_review_queue(cfg, queue)
        console.print(f"[green]Saved JSON:[/green] {json_path}")

    md_path: Path | None = None
    if markdown:
        md_path = append_review_markdown(cfg, queue, top=top)
        console.print(f"[green]Markdown appended:[/green] {md_path}")

    telegram_sent = False
    if telegram:
        parse_mode = cfg.settings.notifications.telegram.parse_mode
        digest = format_review_digest(queue, parse_mode=parse_mode, top=top)
        telegram_sent = send_telegram_message(digest, cfg=cfg, journal=journal)
        if telegram_sent:
            console.print("[green]Telegram digest sent.[/green]")
        else:
            console.print(
                "[yellow]Telegram digest was not delivered; see "
                "memory/DAILY-SUMMARY.md for the fallback copy.[/yellow]"
            )

    journal.record_event(
        category="smc_review_queue",
        level="INFO",
        message="review queue built",
        payload={
            "date": queue.date,
            "source": str(summary_path),
            "counts": counts,
            "tradeable_candidates": [i.symbol for i in tradeable],
            "telegram_sent": telegram_sent,
            "execution_allowed": False,
            "research_only": True,
            "json_path": str(json_path) if json_path else None,
            "markdown_path": str(md_path) if md_path else None,
        },
    )
    console.print(
        "[dim]Reminder: the review queue is research only. "
        "execution_allowed=false. It is not a trade signal.[/dim]"
    )
    raise typer.Exit(code=0)


def _fmt_num(x: object, *, suffix: str = "") -> str:
    if isinstance(x, (int, float)):
        return f"{float(x):.2f}{suffix}"
    return "-"


def _now_et_hhmm() -> str | None:
    """Return the current America/New_York wall-clock as ``'HH:MM'``.

    Used by :command:`smc-review-queue` to drive the 30min session
    guard. Returns ``None`` on any failure so callers fall back to
    "session filter not applied" rather than crashing.
    """
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M")
    except Exception:  # noqa: BLE001 - best-effort clock lookup
        return None


# ---------------------------------------------------------------------------
# MTF SMC/ICT (Prompt 10B) — research; Prompt 10C — optional paper bracket
# ---------------------------------------------------------------------------
def _maybe_mtf_paper_bracket(
    cfg: AppConfig,
    journal: Journal,
    rep: dict,
) -> dict:
    """Connect with ``readonly=False`` and submit a bracket if preconditions pass."""
    from .mtf_paper_execution import connect_and_run_mtf_paper_bracket

    return connect_and_run_mtf_paper_bracket(cfg, journal, rep)


def _mtf_connect_and_fetch(
    symbol: str,
    cfg: AppConfig,
    *,
    include_5min: bool,
    include_daily: bool,
) -> tuple[Any, list[str], IBKRClient | None]:
    """Build :class:`MtfCandleBundle` for ``symbol`` via IBKR. Read-only."""
    from .mtf_smc_engine import MtfCandleBundle
    from .smc_timeframes import resolve_timeframe_spec

    client: IBKRClient | None = None
    w: list[str] = []
    b = MtfCandleBundle()
    try:
        client = _connect(cfg)
    except (IBKRClientError, LiveTradingBlocked) as exc:
        w.append(f"ibkr_connect: {exc}")
        return b, w, None
    try:
        if include_daily:
            ds = resolve_timeframe_spec("daily", cfg)
            b.daily = client.get_bars_for_timeframe(
                symbol, ds, out_warnings=w
            )
        h4s = resolve_timeframe_spec("4h", cfg)
        b.h4 = client.get_bars_for_timeframe(
            symbol, h4s, out_warnings=w
        )
        t30 = resolve_timeframe_spec("30min", cfg)
        b.m30 = client.get_bars_for_timeframe(
            symbol, t30, out_warnings=w
        )
        if include_5min:
            t5 = resolve_timeframe_spec("5min", cfg)
            b.m5 = client.get_bars_for_timeframe(
                symbol, t5, out_warnings=w
            )
        b.warnings = list(w)
    except Exception as exc:  # noqa: BLE001
        w.append(f"fetch: {exc}")
    return b, w, client


def _mtf_save_json(cfg: AppConfig, payload: dict) -> Path:
    out = cfg.absolute("data/mtf_smc")
    out.mkdir(parents=True, exist_ok=True)
    sym = str(payload.get("symbol") or "X").replace("/", "_")
    name = f"{payload.get('date', '2000-01-01')}-{sym}-mtf-smc.json"
    path = out / name
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    return path


def _mtf_save_watchlist_summary(
    cfg: AppConfig, summary: dict
) -> Path:
    out = cfg.absolute("data/mtf_smc")
    out.mkdir(parents=True, exist_ok=True)
    name = f"{summary.get('date', '2000-01-01')}-watchlist-mtf-smc-summary.json"
    path = out / name
    with path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    return path


@app.command("scan-mtf-smc")
def scan_mtf_smc(
    symbol: str = typer.Option(
        ..., "--symbol", "-s", help="Ticker, e.g. AAPL. Research only."
    ),
    use_ibkr: bool = typer.Option(
        False, "--ibkr", help="Fetch all timeframes from IBKR (read-only).",
    ),
    chart: bool = typer.Option(
        False, "--chart", help="Write MTF debug PNGs under data/debug_charts/.",
    ),
    telegram: bool = typer.Option(
        False, "--telegram", help="Send Chinese MTF summary (research only).",
    ),
    save_json: bool = typer.Option(
        True, "--save-json/--no-save-json", help="Write data/mtf_smc/ JSON.",
    ),
    include_5min: bool = typer.Option(
        True, "--include-5min/--no-include-5min", help="Load 5m trigger bars.",
    ),
    include_daily: bool = typer.Option(
        True, "--include-daily/--no-include-daily", help="Load daily bias bars.",
    ),
    market_regime: Optional[str] = typer.Option(
        None, "--market-regime", help="Override regime; else from snapshot.",
    ),
    paper_bracket: bool = typer.Option(
        False,
        "--paper-bracket",
        help="If FULL_ALIGNMENT + config, place paper MTF bracket (requires trading.mtf_paper_bracket_enabled).",
    ),
) -> None:
    """Run multi-timeframe SMC/ICT; optional paper bracket (Prompt 10C)."""
    from .mtf_chart import render_mtf_smc_charts
    from .mtf_smc_engine import run_mtf_smc

    cfg, journal = _bootstrap()
    regime_ctx = _resolve_regime_context(cfg, market_regime)
    regime = str(regime_ctx["market_regime"])
    conf = str(regime_ctx.get("regime_confidence") or "medium")
    w: list[str] = []
    b = None
    client: IBKRClient | None = None
    if not use_ibkr:
        console.print("[red]MTF scan requires --ibkr to load candles.[/red]")
        raise typer.Exit(2)
    if paper_bracket and not cfg.settings.trading.mtf_paper_bracket_enabled:
        console.print(
            "[yellow]--paper-bracket ignored: set trading.mtf_paper_bracket_enabled "
            "and trading.enabled in config/settings.yaml.[/yellow]"
        )
    b, w, client = _mtf_connect_and_fetch(
        symbol.upper(), cfg, include_5min=include_5min, include_daily=include_daily
    )
    if client is not None:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    out_ev: dict = {}
    rep = run_mtf_smc(
        symbol, cfg, b, market_regime=regime, regime_confidence=conf,
        include_5min=include_5min, include_daily=include_daily, out_eval=out_ev,
    )
    rep["warnings"] = list(dict.fromkeys((rep.get("warnings") or []) + w))
    if chart and out_ev:
        charts = render_mtf_smc_charts(
            symbol.upper(),
            {
                "daily": b.daily,
                "4h": b.h4,
                "30min": b.m30,
                "5min": b.m5,
            },
            {k: out_ev.get(k) for k in ("daily", "4h", "30min", "5min")},
            output_dir=cfg.absolute("data/debug_charts"),
        )
        rep["chart_paths"] = charts
    paper: dict = {}
    if paper_bracket and cfg.settings.trading.mtf_paper_bracket_enabled:
        paper = _maybe_mtf_paper_bracket(cfg, journal, rep)
    elif paper_bracket:
        paper = {
            "submitted": False,
            "skipped_reasons": [
                "mtf_paper_bracket_enabled is false; enable in config/settings.yaml",
            ],
        }
    rep["mtf_paper_bracket"] = paper
    console.print(Panel.fit(json.dumps(rep, indent=2, default=str), title="MTF SMC/ICT"))
    path = None
    if save_json:
        path = _mtf_save_json(cfg, rep)
        console.print(f"[green]Saved:[/green] {path}")
    sent = False
    if telegram and cfg.telegram.is_configured:
        from html import escape
        body = f"<b>{escape('【MTF SMC/ICT 多周期识别】')}{escape(symbol.upper())}</b>\n"
        body += "<pre>" + escape(rep.get("human_summary_zh", "")) + "</pre>\n"
        if paper.get("submitted"):
            body += f"<i>{escape('纸面已尝试 bracket 下单；请核对你的订单面板。')}</i>"
        else:
            body += f"<i>{escape('研究扫描；未触发纸面下单或仅记录原因。')}</i>"
        sent = bool(send_telegram_message(body, cfg=cfg, journal=journal))
    journal.record_event(
        category="mtf_smc",
        level="INFO",
        message="scan-mtf-smc",
        payload={
            "symbol": symbol.upper(),
            "path": str(path) if path else None,
            "telegram": sent,
            "mtf_paper_bracket": paper,
        },
    )
    raise typer.Exit(0)


@app.command("scan-mtf-smc-watchlist")
def scan_mtf_smc_watchlist(
    use_ibkr: bool = typer.Option(
        False, "--ibkr", help="Fetch MTF data from IBKR (read-only).",
    ),
    chart: bool = typer.Option(False, "--chart"),
    telegram: bool = typer.Option(False, "--telegram"),
    limit: Optional[int] = typer.Option(
        None, "--limit", min=1, help="Max symbols to scan.",
    ),
    source: Optional[str] = typer.Option(
        None, "--source", help="static or dynamic (default: watchlist config).",
    ),
    save_json: bool = typer.Option(True, "--save-json/--no-save-json"),
    include_5min: bool = typer.Option(True, "--include-5min/--no-include-5min"),
    include_daily: bool = typer.Option(True, "--include-daily/--no-include-daily"),
    paper_bracket: bool = typer.Option(
        False,
        "--paper-bracket",
        help="Run paper bracket (up to --max-paper-trades) on FULL_ALIGNMENT symbols.",
    ),
    max_paper_trades: int = typer.Option(
        1, "--max-paper-trades", min=0, help="Max paper brackets per run (0 = none).",
    ),
) -> None:
    """Scan many symbols; optional paper bracket for FULL_ALIGNMENT (10C)."""
    from .mtf_smc_engine import format_mtf_watchlist_digest_zh, run_mtf_smc
    from .mtf_chart import render_mtf_smc_charts

    cfg, journal = _bootstrap()
    regime_ctx = _resolve_regime_context(cfg, None)
    regime = str(regime_ctx["market_regime"])
    conf = str(regime_ctx.get("regime_confidence") or "medium")
    chosen = (source or str(cfg.watchlist.get("default_source") or "static")).lower()
    if chosen not in {"static", "dynamic"}:
        console.print("[red]--source must be static or dynamic[/red]")
        raise typer.Exit(2)
    symbols: list[str] = []
    if chosen == "dynamic":
        dw = load_dynamic_watchlist(cfg)
        if dw is None:
            console.print("[red]Build dynamic watchlist first (build-watchlist).[/red]")
            raise typer.Exit(3)
        symbols = [r.symbol for r in dw.symbols if not r.blocked]
    else:
        eqs = (cfg.watchlist or {}).get("equities") or []
        symbols = [e.get("symbol") for e in eqs if e.get("symbol")]
        if not symbols:
            symbols = list(cfg.watchlist.get("static_core") or [])
    if not symbols:
        raise typer.Exit(0)
    if limit is not None:
        symbols = symbols[:limit]
    if not use_ibkr:
        console.print("[red]--ibkr is required.[/red]")
        raise typer.Exit(2)
    if paper_bracket and not cfg.settings.trading.mtf_paper_bracket_enabled:
        console.print(
            "[yellow]--paper-bracket ignored: enable trading.mtf_paper_bracket_enabled.[/yellow]"
        )
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
            sym, cfg, b, market_regime=regime, regime_confidence=conf,
            include_5min=include_5min, include_daily=include_daily, out_eval=out_ev,
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
    elif paper_bracket and max_paper_trades > 0 and not cfg.settings.trading.mtf_paper_bracket_enabled:
        paper_runs = [
            {
                "symbol": None,
                "result": {
                    "submitted": False,
                    "skipped_reasons": ["mtf_paper_bracket_enabled is false"],
                },
            }
        ]
    summary = {
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
    p = _mtf_save_watchlist_summary(cfg, summary) if save_json else None
    if p:
        console.print(f"[green]Saved summary:[/green] {p}")
    if telegram and cfg.telegram.is_configured:
        from html import escape
        digest = format_mtf_watchlist_digest_zh(summary)
        body = "<pre>" + escape(digest) + "</pre>"
        send_telegram_message(body, cfg=cfg, journal=journal)
    journal.record_event(
        category="mtf_smc",
        level="INFO",
        message="scan-mtf-smc-watchlist",
        payload={"n": len(symbols), "execution_allowed": False},
    )
    raise typer.Exit(0)


@app.command("mtf-diagnostic-report")
def mtf_diagnostic_report(
    use_latest: bool = typer.Option(
        False,
        "--latest",
        help="Use the most recent date that has mtf_smc JSON under data/mtf_smc/.",
    ),
    report_date: Optional[str] = typer.Option(
        None,
        "--date",
        help="YYYY-MM-DD; default: today in UTC. Ignored if --latest is set.",
    ),
    top: int = typer.Option(10, "--top", min=1, help="How many 'nearest' rows in the JSON top list."),
    min_score: int = typer.Option(
        55, "--min-score", min=0, max=100, help="Min MTF score for near-alignment (THIRTY etc.).",
    ),
    telegram: bool = typer.Option(False, "--telegram", help="Send Chinese digest to Telegram (if configured)."),
) -> None:
    """Build MTF no-trade diagnostic from saved per-symbol mtf_smc JSON (10D, no orders)."""
    from html import escape

    from .mtf_diagnostic import (
        build_diagnostic_report,
        find_latest_mtf_date,
        format_mtf_diagnostic_digest_zh,
        format_mtf_near_alignment_digest_zh,
        list_mtf_smc_per_symbol_jsons,
        load_mtf_json,
    )

    cfg, journal = _bootstrap()
    mtf_dir = cfg.absolute("data/mtf_smc")
    mtf_dir.mkdir(parents=True, exist_ok=True)
    if use_latest:
        d = find_latest_mtf_date(mtf_dir) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    elif report_date:
        d = report_date
    else:
        d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    paths = list_mtf_smc_per_symbol_jsons(mtf_dir, d)
    items: list[dict] = []
    for p in paths:
        try:
            items.append(load_mtf_json(p))
        except (OSError, json.JSONDecodeError) as e:
            console.print(f"[yellow]skip {p.name}: {e}[/yellow]")
    sum_path = mtf_dir / f"{d}-watchlist-mtf-smc-summary.json"
    src = f"per-symbol mtf_smc under data/mtf_smc, n={len(items)}"
    if sum_path.exists():
        try:
            with sum_path.open(encoding="utf-8") as f:
                summ = json.load(f)
            src = f"per-symbol mtf + watchlist 汇总 {d} (n_files={len(items)}); summary symbols={int(summ.get('symbols_scanned') or 0)}"
        except OSError:  # pragma: no cover
            pass
    rep = build_diagnostic_report(
        d, source_summary=src, items=items, top=top, min_score_near=min_score, max_near_alignment=top
    )
    out = mtf_dir / f"{d}-mtf-diagnostic-report.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, default=str, ensure_ascii=False)
    console.print(f"[green]Wrote:[/green] {out}")
    if telegram and cfg.telegram.is_configured:
        dtxt = format_mtf_diagnostic_digest_zh(rep, paper_gate_disabled=True)
        body = f"<b>{escape('【MTF SMC/ICT 未入场诊断】')}{escape(d)}</b>\n<pre>" + escape(dtxt) + "</pre>"
        send_telegram_message(body, cfg=cfg, journal=journal)
        near = rep.get("near_alignment_candidates") or []
        if near:
            ntxt = format_mtf_near_alignment_digest_zh(d, near)
            body2 = f"<b>{escape('【MTF SMC/ICT 接近入场观察】')}{escape(d)}</b>\n<pre>" + escape(ntxt) + "</pre>"
            send_telegram_message(body2, cfg=cfg, journal=journal)
    journal.record_event(
        category="mtf_diagnostic",
        level="INFO",
        message="mtf-diagnostic-report",
        payload={"date": d, "n": len(items), "path": str(out)},
    )
    raise typer.Exit(0)


@app.command("mtf-near-alignment-alert")
def mtf_near_alignment_alert(
    use_latest: bool = typer.Option(
        False,
        "--latest",
        help="Use latest date with a mtf-diagnostic-report.json under data/mtf_smc/.",
    ),
    report_date: Optional[str] = typer.Option(
        None,
        "--date",
        help="YYYY-MM-DD; default today UTC. Ignored if --latest.",
    ),
    top: int = typer.Option(10, "--top", min=1, help="Max candidates to show and send."),
    min_score: int = typer.Option(55, "--min-score", min=0, max=100),
    telegram: bool = typer.Option(False, "--telegram", help="Send near-alignment digest (if configured)."),
) -> None:
    """Prompt 10E: list near-FULL symbols (alert-only, no orders)."""
    from html import escape

    from .mtf_diagnostic import (
        find_latest_mtf_date,
        format_mtf_near_alignment_digest_zh,
        load_mtf_json,
        select_near_alignment_candidates,
    )

    cfg, journal = _bootstrap()
    mtf_dir = cfg.absolute("data/mtf_smc")
    if use_latest:
        d = find_latest_mtf_date(mtf_dir) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    elif report_date:
        d = report_date
    else:
        d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = mtf_dir / f"{d}-mtf-diagnostic-report.json"
    if not path.exists():
        console.print(
            f"[red]Missing {path.name}. Run: python -m bot.cli mtf-diagnostic-report --date {d}[/red]"
        )
        raise typer.Exit(2)
    rep = load_mtf_json(path)
    near = select_near_alignment_candidates(rep, min_score=min_score, max_items=top)
    rep["near_alignment_candidates"] = near
    tbl = Table(title=f"MTF near-alignment ({d}) min_score={min_score}")
    tbl.add_column("symbol", style="cyan")
    tbl.add_column("score", justify="right")
    tbl.add_column("category", max_width=28)
    tbl.add_column("blocking", max_width=18)
    tbl.add_column("next (short)", max_width=40)
    for r in near:
        tbl.add_row(
            str(r.get("symbol", "")),
            str(r.get("mtf_alignment_score", "")),
            str(r.get("alignment_category", ""))[:26],
            str(r.get("blocking_layer", ""))[:16],
            str(r.get("next_condition_to_watch", ""))[:38],
        )
    console.print(tbl)
    console.print(f"[dim]Candidates: {len(near)} (alert-only, no orders)[/dim]")
    if telegram and cfg.telegram.is_configured:
        ntxt = format_mtf_near_alignment_digest_zh(d, near)
        body = f"<b>{escape('【MTF SMC/ICT 接近入场观察】')}{escape(d)}</b>\n<pre>" + escape(ntxt) + "</pre>"
        send_telegram_message(body, cfg=cfg, journal=journal)
    journal.record_event(
        category="mtf_near_alignment",
        level="INFO",
        message="mtf-near-alignment-alert",
        payload={"date": d, "n": len(near)},
    )
    raise typer.Exit(0)


@app.command("mtf-trigger-check")
def mtf_trigger_check_cmd(
    use_latest: bool = typer.Option(
        False,
        "--latest",
        help="Use the newest *-mtf-diagnostic-report.json under data/mtf_smc/.",
    ),
    report_date: Optional[str] = typer.Option(
        None, "--date", help="Report date (YYYY-MM-DD) if not --latest.",
    ),
    top: int = typer.Option(5, "--top", min=1, help="Max watch symbols to refresh."),
    include_premium: bool = typer.Option(
        False, "--include-premium", help="Actively poll PREMIUM_DISCOUNT rows.",
    ),
    symbol: Optional[str] = typer.Option(
        None, "--symbol", help="Only this ticker if present in near-alignment list.",
    ),
    use_ibkr: bool = typer.Option(
        False, "--ibkr", help="Refresh candles from IBKR (read-only).",
    ),
    telegram: bool = typer.Option(False, "--telegram", help="Send trigger Telegram (if configured)."),
    auto_paper_bracket: bool = typer.Option(
        False,
        "--auto-paper-bracket",
        help="10G: if settings allow, run MTF paper bracket when FULL+5m confirmed (same gate as mtf_paper_may_run).",
    ),
) -> None:
    """One-shot 5m trigger refresh (10F); optional 10G auto paper (FULL + 5m confirmed, gated)."""
    from .mtf_trigger_watch import find_latest_diagnostic_report_path, run_mtf_trigger_check
    from .mtf_trigger_watch import RUNTIME_STATE_FILENAME

    cfg, journal = _bootstrap()
    mtf_dir = cfg.absolute("data/mtf_smc")
    mtf_dir.mkdir(parents=True, exist_ok=True)
    if use_latest:
        res = find_latest_diagnostic_report_path(mtf_dir)
        if not res:
            console.print(
                "[red]No *-mtf-diagnostic-report.json. Run mtf-diagnostic-report first.[/red]"
            )
            raise typer.Exit(2)
        d, _path = res
    elif report_date:
        d = report_date
    else:
        d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not (mtf_dir / f"{d}-mtf-diagnostic-report.json").exists():
        console.print(
            f"[red]Missing {d}-mtf-diagnostic-report.json. "
            f"Run: python -m bot.cli mtf-diagnostic-report --date {d}[/red]"
        )
        raise typer.Exit(2)
    if not use_ibkr:
        console.print("[red]5m trigger check needs live data: pass --ibkr.[/red]")
        raise typer.Exit(2)
    if auto_paper_bracket and not cfg.settings.trading.mtf_paper_auto_bracket_enabled:
        console.print(
            "[yellow]--auto-paper-bracket: set trading.mtf_paper_auto_bracket_enabled: true in "
            "config/settings.yaml (and other MTF paper gates) to submit.[/yellow]"
        )
    out, _meta = run_mtf_trigger_check(
        cfg,
        journal,
        mtf_dir=mtf_dir,
        report_date=d,
        use_ibkr=True,
        top=top,
        include_premium=include_premium,
        symbol_filter=symbol,
        telegram=telegram,
        state_path=mtf_dir / RUNTIME_STATE_FILENAME,
        auto_paper_bracket=auto_paper_bracket,
    )
    console.print(
        Panel.fit(
            json.dumps(out, indent=2, default=str, ensure_ascii=False),
            title="mtf-trigger-check",
        )
    )
    journal.record_event(
        category="mtf_trigger_watch",
        level="INFO",
        message="mtf-trigger-check",
        payload={
            "date": d,
            "symbols_checked": out.get("symbols_checked"),
            "auto_paper_bracket": bool(auto_paper_bracket),
        },
    )
    raise typer.Exit(0)


@app.command("mtf-trigger-watch")
def mtf_trigger_watch_cmd(
    use_latest: bool = typer.Option(
        False,
        "--latest",
        help="Use the newest mtf diagnostic report under data/mtf_smc/.",
    ),
    report_date: Optional[str] = typer.Option(
        None, "--date", help="YYYY-MM-DD; ignored with --latest.",
    ),
    top: int = typer.Option(5, "--top", min=1),
    include_premium: bool = typer.Option(
        False, "--include-premium", help="Include premium-discount near rows.",
    ),
    symbol: Optional[str] = typer.Option(
        None, "--symbol", help="Single-symbol filter.",
    ),
    use_ibkr: bool = typer.Option(
        False, "--ibkr", help="Refresh from IBKR each cycle (read-only).",
    ),
    interval_minutes: int = typer.Option(
        5, "--interval-minutes", min=1, help="Minutes between check cycles.",
    ),
    duration_minutes: int = typer.Option(
        120, "--duration-minutes", min=1, help="Total watch window.",
    ),
    telegram: bool = typer.Option(False, "--telegram"),
    auto_paper_bracket: bool = typer.Option(
        False,
        "--auto-paper-bracket",
        help="10G: after each check cycle, may run paper bracket (FULL+5m confirmed) if config allows.",
    ),
) -> None:
    """Loop mtf-trigger-check (10F); optional 10G auto paper on eligible symbols."""
    from .mtf_trigger_watch import (
        RUNTIME_STATE_FILENAME,
        find_latest_diagnostic_report_path,
        run_mtf_trigger_watch_loop,
    )

    cfg, journal = _bootstrap()
    mtf_dir = cfg.absolute("data/mtf_smc")
    mtf_dir.mkdir(parents=True, exist_ok=True)
    if use_latest:
        res = find_latest_diagnostic_report_path(mtf_dir)
        if not res:
            console.print(
                "[red]No mtf diagnostic report. Run mtf-diagnostic-report first.[/red]"
            )
            raise typer.Exit(2)
        d, _ = res
    elif report_date:
        d = report_date
    else:
        d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not (mtf_dir / f"{d}-mtf-diagnostic-report.json").exists():
        console.print(
            f"[red]Missing {d}-mtf-diagnostic-report.json. Run mtf-diagnostic-report.[/red]"
        )
        raise typer.Exit(2)
    if not use_ibkr:
        console.print("[red]Loop requires --ibkr.[/red]")
        raise typer.Exit(2)
    if auto_paper_bracket and not cfg.settings.trading.mtf_paper_auto_bracket_enabled:
        console.print(
            "[yellow]--auto-paper-bracket: enable trading.mtf_paper_auto_bracket_enabled in settings.[/yellow]"
        )
    console.print(
        f"[cyan]mtf-trigger-watch date={d} every {interval_minutes}m "
        f"for {duration_minutes}m; Ctrl+C to stop. Log: "
        f"{d}-trigger-watch.jsonl[/cyan]"
    )
    run_mtf_trigger_watch_loop(
        cfg,
        journal,
        mtf_dir=mtf_dir,
        report_date=d,
        use_ibkr=True,
        top=top,
        include_premium=include_premium,
        symbol_filter=symbol,
        telegram=telegram,
        state_path=mtf_dir / RUNTIME_STATE_FILENAME,
        interval_minutes=interval_minutes,
        duration_minutes=duration_minutes,
        auto_paper_bracket=auto_paper_bracket,
    )
    journal.record_event(
        category="mtf_trigger_watch",
        level="INFO",
        message="mtf-trigger-watch",
        payload={
            "date": d,
            "auto_paper_bracket": bool(auto_paper_bracket),
        },
    )
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# Daily scheduler commands (Prompt 9 Part B)
# ---------------------------------------------------------------------------
@app.command("schedule-status")
def schedule_status_cmd() -> None:
    """Print the configured timezone, jobs, and next run times."""
    from .daily_scheduler import schedule_status

    cfg, _journal = _bootstrap()
    status = schedule_status(cfg)

    console.print(
        Panel.fit(
            "\n".join([
                f"Timezone: {status.timezone}",
                f"Enabled: {status.enabled}",
                f"Reports only: {status.reports_only}",
                "Execution allowed: False (hard-coded)",
            ]),
            title="Scheduler",
            style="cyan",
        )
    )
    table = Table(title="Jobs")
    for col in ("Name", "Enabled", "Time", "Days", "Next Run (NY)"):
        table.add_column(col, overflow="fold")
    for job in status.jobs:
        table.add_row(
            job["name"],
            str(job["enabled"]),
            job["time"],
            ",".join(job["days"]),
            job["next_run"] or "-",
        )
    console.print(table)
    raise typer.Exit(code=0)


@app.command("run-pre-open-report")
def run_pre_open_report_cmd() -> None:
    """Run the 08:30 pre-open news workflow immediately."""
    from .daily_scheduler import run_pre_open_report_now

    cfg, journal = _bootstrap()
    result = run_pre_open_report_now(cfg, journal)
    console.print(Panel.fit(json.dumps(result, indent=2), title="pre_open_news"))
    raise typer.Exit(code=0 if result.get("status") == "success" else 1)


@app.command("run-opening-review")
def run_opening_review_cmd() -> None:
    """Run the 09:45 opening SMC review sequence immediately."""
    from .daily_scheduler import run_opening_review_now

    cfg, journal = _bootstrap()
    result = run_opening_review_now(cfg, journal)
    console.print(
        Panel.fit(json.dumps(result, indent=2, default=str),
                  title="opening_smc_review")
    )
    raise typer.Exit(code=0 if result.get("status") == "success" else 1)


@app.command("telegram-listen")
def telegram_listen_cmd(
    iterations: Optional[int] = typer.Option(
        None,
        "--iterations",
        help=(
            "Stop after N polling iterations (used in tests); "
            "omit for an unbounded foreground loop."
        ),
    ),
) -> None:
    """Start polling Telegram for allowed commands (report-only).

    Requires ``telegram.command_interface.enabled=true`` in
    ``config/telegram.yaml`` and ``allowed_chat_ids`` resolvable via
    ``TELEGRAM_CHAT_ID``. Every incoming message is authorized,
    safety-gated, and logged before any CLI command is dispatched.
    """
    from .telegram_commands import load_command_config, run_polling

    cfg, journal = _bootstrap()
    ci = load_command_config(cfg)
    if not ci.is_usable:
        console.print(
            "[yellow]telegram command interface disabled or no "
            "allowed_chat_ids; nothing to do.[/yellow]"
        )
        raise typer.Exit(code=1)
    console.print(
        f"[cyan]Polling Telegram every {ci.polling_interval_seconds}s. "
        "Reports only. execution_allowed=false. Press Ctrl+C to stop.[/cyan]"
    )
    try:
        run_polling(cfg, journal, max_iterations=iterations)
    except (KeyboardInterrupt, SystemExit):
        console.print("[yellow]telegram-listen stopped.[/yellow]")
    raise typer.Exit(code=0)


@app.command("telegram-test-command")
def telegram_test_command_cmd(
    command: str = typer.Option(
        ...,
        "--command",
        help=(
            "The Telegram command to dispatch, e.g. '/news', '/review', or "
            "any free text (including unsafe examples like 'buy AAPL' to "
            "verify the safety gate)."
        ),
    ),
    chat_id: Optional[str] = typer.Option(
        None,
        "--chat-id",
        help=(
            "Override the chat id used for authorization. Defaults to the "
            "first entry in telegram.command_interface.allowed_chat_ids."
        ),
    ),
) -> None:
    """Run a single Telegram command in-process without polling.

    Useful for acceptance tests (e.g. ``--command '/news'``) and for
    verifying that unsafe commands like ``buy AAPL`` are rejected.
    """
    from .telegram_commands import (
        SAFETY_MESSAGE_ZH,
        load_command_config,
        process_message,
    )

    cfg, journal = _bootstrap()
    ci = load_command_config(cfg)
    if not ci.is_usable:
        console.print(
            "[yellow]telegram command interface disabled or no "
            "allowed_chat_ids; enable it in config/telegram.yaml first.[/yellow]"
        )
        raise typer.Exit(code=1)

    target_chat_id = chat_id or (ci.allowed_chat_ids[0] if ci.allowed_chat_ids else "")
    if not target_chat_id:
        console.print(
            "[yellow]no chat id available; supply --chat-id or set "
            "TELEGRAM_CHAT_ID.[/yellow]"
        )
        raise typer.Exit(code=1)

    result = process_message(
        cfg, journal, ci,
        chat_id=target_chat_id, text=command,
    )
    payload = {
        "command": result.command,
        "status": result.status,
        "parts_sent": result.parts_sent,
        "details": result.details,
        "safety_message": SAFETY_MESSAGE_ZH if result.status == "rejected" else None,
        "execution_allowed": False,
    }
    console.print(Panel.fit(
        json.dumps(payload, ensure_ascii=False, indent=2),
        title="telegram-test-command",
    ))
    console.print(Panel.fit(result.reply_zh, title="reply", style="cyan"))
    exit_code = 0 if result.status == "success" else (
        2 if result.status in {"rejected", "unauthorized"} else 1
    )
    raise typer.Exit(code=exit_code)


@app.command("run-scheduler")
def run_scheduler_cmd() -> None:
    """Block the foreground running the daily 08:30 / 09:45 jobs.

    Use Ctrl+C to exit. The scheduler is report-only: it never places
    orders and rejects any sequence step that could.
    """
    from .daily_scheduler import build_daily_scheduler, schedule_status

    cfg, journal = _bootstrap()
    status = schedule_status(cfg)
    console.print(
        f"[cyan]Starting scheduler (timezone={status.timezone}). "
        "Reports only. execution_allowed=false. Press Ctrl+C to stop.[/cyan]"
    )
    scheduler = build_daily_scheduler(cfg, journal)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        console.print("[yellow]Scheduler stopped.[/yellow]")
    raise typer.Exit(code=0)


# Backwards-compatible alias used by tests and shell scripts.
send_telegram_message = send_telegram_message  # noqa: PLW0127 - explicit re-export


if __name__ == "__main__":  # pragma: no cover
    app()
