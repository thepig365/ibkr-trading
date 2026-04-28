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
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .broker import Broker
from .config import AppConfig, load_config
from .ibkr_client import IBKRClient, IBKRClientError, LiveTradingBlocked
from .ibkr_connection import PUBLIC_COLLISION_HINT, connect_readonly_roster_retry
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


def _connect(
    cfg: AppConfig,
    *,
    readonly: bool = True,
    for_cli: bool = True,
    roster: str = "broker_readonly",
) -> IBKRClient:
    """Connect to IBKR for read-only CLI flows.

    Routes to non-default ``client_id`` buckets (:mod:`bot.ibkr_connection`)
    and retries transient **client-id collision** signatures (Ib 326 /
    ``already in use``) up to three times — unlike long-lived paper-engine
    clients that intentionally keep ``IBKR_CLIENT_ID`` from the environment.

    * ``for_cli=True`` (default): print a panel and use ``typer.Exit``
      when the connection ultimately fails or live trading is blocked.
    * ``for_cli=False``: renders to *stderr*, then callers may propagate.
    """

    err_console = Console(stderr=True)

    if not readonly:
        client = IBKRClient(cfg)
        try:
            client.connect(readonly=False)
        except LiveTradingBlocked as exc:
            if for_cli:
                console.print(
                    Panel.fit(
                        f"[bold red]Live trading blocked:[/bold red] {exc}",
                        style="red",
                    )
                )
                raise typer.Exit(code=2)
            err_console.print(
                Panel.fit(
                    f"[bold red]Live trading blocked:[/bold red] {exc}",
                    style="red",
                )
            )
            raise
        except IBKRClientError as exc:
            if for_cli:
                console.print(
                    Panel.fit(
                        f"[bold red]Connection error:[/bold red] {exc}",
                        style="red",
                    )
                )
                raise typer.Exit(code=1)
            err_console.print(
                Panel.fit(
                    f"[bold red]Connection error:[/bold red] {exc}",
                    style="red",
                )
            )
            raise
        return client

    outcome = connect_readonly_roster_retry(cfg, roster)
    if outcome.live_blocked is not None:
        exc = outcome.live_blocked
        if for_cli:
            console.print(
                Panel.fit(
                    f"[bold red]Live trading blocked:[/bold red] {exc}",
                    style="red",
                )
            )
            raise typer.Exit(code=2)
        err_console.print(
            Panel.fit(
                f"[bold red]Live trading blocked:[/bold red] {exc}",
                style="red",
            )
        )
        raise exc

    if outcome.client is None:
        body = outcome.fatal_message or PUBLIC_COLLISION_HINT
        if for_cli:
            console.print(Panel.fit(f"[yellow]{body}[/yellow]", title="IBKR"))
            raise typer.Exit(code=1)
        err_console.print(Panel.fit(f"[yellow]{body}[/yellow]", title="IBKR"))
        raise IBKRClientError(body)

    if outcome.log_lines and for_cli:
        blob = (
            "\n".join(outcome.log_lines)
            if len(outcome.log_lines) > 1
            else outcome.log_lines[-1]
        )
        console.print(f"[dim]{blob}[/dim]")
    elif outcome.log_lines:
        blob = (
            "\n".join(outcome.log_lines)
            if len(outcome.log_lines) > 1
            else outcome.log_lines[-1]
        )
        err_console.print(f"[dim]{blob}[/dim]")

    assert outcome.client is not None  # narrowed
    return outcome.client


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


@app.command("ibkr-session-status")
def ibkr_session_status_cmd() -> None:
    """Read-only: connect, print session metadata, disconnect. No orders."""
    cfg, _journal = _bootstrap()
    client = _connect(cfg)
    exit_code = 0
    try:
        snap = client.session_status_snapshot()
        console.print(
            Panel.fit(
                json.dumps(snap, indent=2, ensure_ascii=False, default=str),
                title="ibkr-session-status",
                style="cyan",
            )
        )
    except Exception as exc:  # noqa: BLE001 - operator diagnostics
        exit_code = 2
        console.print(
            Panel.fit(
                f"[red]{type(exc).__name__}:[/red] {exc!s}",
                title="ibkr-session-status",
                style="red",
            )
        )
    finally:
        client.disconnect()
    raise typer.Exit(exit_code)


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
        aid = summaries[0].account_id if summaries else ""
        journal.record_positions_snapshot([], account_id=aid)
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
    aid = positions[0].account if positions else (summaries[0].account_id if summaries else "")
    journal.record_positions_snapshot([p.to_dict() for p in positions], account_id=aid)


@app.command("refresh-paper-account-state")
def refresh_paper_account_state() -> None:
    """Read-only: write latest account + positions snapshots (paper). No orders.

    When the portfolio is empty, still records a flat placeholder row so
    reconciliation does not use a stale prior snapshot.
    """
    cfg, journal = _bootstrap()
    if (cfg.settings.account.mode or "").lower() != "paper":
        console.print("[red]Refusing: settings account.mode must be paper.[/red]")
        raise typer.Exit(2)
    if (cfg.ibkr.account_mode or "").lower() not in ("paper", "demo", "test"):
        console.print(
            "[red]Refusing: IBKR_ACCOUNT_MODE must be paper (or demo/test).[/red]"
        )
        raise typer.Exit(2)
    client = _connect(cfg)
    try:
        broker = Broker(cfg, client, journal)
        summaries = broker.get_account_summary()
        pos_rows = broker.get_positions()
        open_list = broker.get_open_orders()
        for s in summaries:
            journal.record_account_snapshot(s.to_dict())
        aid = summaries[0].account_id if summaries else ""
        journal.record_positions_snapshot(
            [p.to_dict() for p in pos_rows], account_id=aid
        )
        journal.record_event(
            category="account_refresh",
            level="INFO",
            message="refresh-paper-account-state",
            payload={
                "open_positions": len(pos_rows),
                "open_orders_seen": len(open_list),
                "account_snapshots": len(summaries),
            },
        )
    finally:
        client.disconnect()
    console.print(
        "[green]Recorded account + position snapshots (read-only; no orders).[/green]"
    )
    raise typer.Exit(0)


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
        try:
            from .reports.operational_hints import (  # noqa: PLC0415
                write_open_orders_count,
            )

            write_open_orders_count(cfg.project_root, 0)
        except (OSError, TypeError, ValueError):
            pass
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
    try:
        from .reports.operational_hints import (  # noqa: PLC0415
            write_open_orders_count,
        )

        write_open_orders_count(cfg.project_root, len(orders))
    except (OSError, TypeError, ValueError):
        pass


def _reconcile_failure_body(report: ReconciliationReport) -> str:
    lines = [
        f"positions_without_stops: {report.positions_without_stops}",
        f"unknown_open_orders count: {len(report.unknown_open_orders)}",
        f"missing_local_records (broker open, not in local snapshot): {report.missing_local_records}",
        f"stale_local_position_records (info): {getattr(report, 'stale_local_position_records', [])}",
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
            f"missing_local_records (broker open, not in journal): {report.missing_local_records}\n"
            f"stale_local_position_records (local snapshot only): "
            f"{getattr(report, 'stale_local_position_records', [])}\n"
            f"notes: {report.notes}",
            style=style,
        )
    )

    try:
        from .reports.operational_hints import (  # noqa: PLC0415
            write_reconcile_status,
        )

        write_reconcile_status(cfg.project_root, bool(report.passed))
    except (OSError, TypeError, ValueError):
        pass

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
        outcome = connect_readonly_roster_retry(cfg, "watchlist")
        notes.extend(outcome.log_lines)
        if outcome.live_blocked is not None:
            notes.append(f"IBKR connect refused: {outcome.live_blocked}")
        elif outcome.client is not None:
            client = outcome.client
        else:
            notes.append(outcome.fatal_message or "IBKR unavailable for watchlist.")

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
            client = _connect(cfg, roster="candles")
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
                    client = _connect(cfg, roster="candles")
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
        client = _connect(cfg, for_cli=False, roster="candles")
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
    from .mtf_smc_batch import run_mtf_smc_watchlist_scan

    cfg, journal = _bootstrap()
    chosen = (source or str(cfg.watchlist.get("default_source") or "static")).lower()
    if chosen not in {"static", "dynamic"}:
        console.print("[red]--source must be static or dynamic[/red]")
        raise typer.Exit(2)
    if not use_ibkr:
        console.print("[red]--ibkr is required.[/red]")
        raise typer.Exit(2)
    if paper_bracket and not cfg.settings.trading.mtf_paper_bracket_enabled:
        console.print(
            "[yellow]--paper-bracket ignored: enable trading.mtf_paper_bracket_enabled.[/yellow]"
        )
    try:
        summary = run_mtf_smc_watchlist_scan(
            cfg,
            journal,
            use_ibkr=use_ibkr,
            chart=chart,
            telegram=telegram,
            limit=limit,
            source=source,
            save_json=save_json,
            include_5min=include_5min,
            include_daily=include_daily,
            paper_bracket=paper_bracket,
            max_paper_trades=max_paper_trades,
        )
    except FileNotFoundError:
        console.print(
            "[red]Build dynamic watchlist first (build-watchlist).[/red]"
        )
        raise typer.Exit(3)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)
    p = summary.get("_saved_summary_path")
    if p:
        console.print(f"[green]Saved summary:[/green] {p}")
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# Prompt 13D — ICT/SMC Intraday Liquidity Reversal V1 scan commands.
#
# Strict invariants (also enforced by the scanner module):
#   * Research/scan only. No orders. No paper execution.
#   * IBKR is touched lazily inside the scanner functions, never at
#     CLI module load.
#   * --ibkr is required so a noisy CI default cannot accidentally
#     wake a TWS connection.
# ---------------------------------------------------------------------------
@app.command("scan-intraday-smc")
def scan_intraday_smc_cmd(
    symbol: str = typer.Option(
        ..., "--symbol", "-s", help="Ticker, e.g. AAPL. Research only.",
    ),
    use_ibkr: bool = typer.Option(
        False, "--ibkr", help="Fetch 4h/30m/5m/1m bars from IBKR (read-only).",
    ),
    chart: bool = typer.Option(
        False, "--chart", help="Write 30m/5m/1m PNGs under data/debug_charts/.",
    ),
    telegram: bool = typer.Option(
        False,
        "--telegram",
        help="Send Chinese single-symbol digest if Telegram is configured.",
    ),
    save_json: bool = typer.Option(
        True, "--save-json/--no-save-json",
        help="Write data/intraday_smc/<date>-<SYMBOL>-intraday-smc.json",
    ),
    direction_hint: str = typer.Option(
        "auto", "--direction-hint",
        help="auto | long | short — restrict scan direction.",
    ),
    mode: str = typer.Option(
        "strict_and_aggressive",
        "--mode",
        help="Reserved for future filtering; default 'strict_and_aggressive'.",
    ),
) -> None:
    """ICT/SMC Intraday Liquidity Reversal V1 — single-symbol scan (13D).

    Research-only. No orders. No paper execution.
    """
    from .strategies.ict_smc_intraday import (
        IntradayRiskConfig,
        format_intraday_telegram_zh,
        save_intraday_evaluation,
        scan_symbol_with_ibkr,
    )

    if not use_ibkr:
        console.print("[red]--ibkr is required for scan-intraday-smc.[/red]")
        raise typer.Exit(2)
    if direction_hint not in {"auto", "long", "short"}:
        console.print("[red]--direction-hint must be auto|long|short[/red]")
        raise typer.Exit(2)
    if mode not in {"strict_and_aggressive", "strict_only", "aggressive_only"}:
        console.print(
            "[red]--mode must be strict_and_aggressive|strict_only|aggressive_only[/red]"
        )
        raise typer.Exit(2)

    cfg, journal = _bootstrap()
    chart_dir = cfg.absolute("data/debug_charts") if chart else None

    risk_cfg = IntradayRiskConfig()
    eval_obj = scan_symbol_with_ibkr(
        symbol.upper(),
        cfg,
        journal,
        risk_cfg=risk_cfg,
        direction_hint=direction_hint,
        chart=chart,
        chart_dir=chart_dir,
    )

    saved_path: Path | None = None
    if save_json:
        out_dir = cfg.absolute("data/intraday_smc")
        saved_path = save_intraday_evaluation(out_dir, eval_obj)

    payload = eval_obj.to_dict()
    payload["_saved_path"] = str(saved_path) if saved_path else None
    console.print(
        Panel.fit(
            json.dumps(payload, indent=2, default=str, ensure_ascii=False),
            title=f"ICT/SMC Intraday — {symbol.upper()}",
            style="cyan",
        )
    )

    sent = False
    if telegram and cfg.telegram.is_configured:
        try:
            single_summary = {
                "date": eval_obj.date,
                "strategy_id": eval_obj.strategy_id,
                "source": "single",
                "symbols_scanned": 1,
                "counts": {eval_obj.signal_category: 1},
                "ready_strict_symbols": (
                    [eval_obj.symbol] if eval_obj.signal_category
                    == "DAY_TRADE_READY_STRICT" else []
                ),
                "ready_aggressive_symbols": (
                    [eval_obj.symbol] if eval_obj.signal_category
                    == "DAY_TRADE_READY_AGGRESSIVE" else []
                ),
                "watch_symbols": (
                    [eval_obj.symbol] if eval_obj.signal_category
                    == "WATCH_ONLY" else []
                ),
                "invalid_symbols": (
                    [eval_obj.symbol] if eval_obj.signal_category
                    == "INVALID_RISK" else []
                ),
            }
            text = format_intraday_telegram_zh(single_summary)
            sent = bool(send_telegram_message(text, cfg=cfg, journal=journal))
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]telegram send skipped: {exc}[/yellow]")

    journal.record_event(
        category="ict_smc_intraday",
        level="INFO",
        message="scan-intraday-smc",
        payload={
            "symbol": symbol.upper(),
            "signal_category": eval_obj.signal_category,
            "direction": eval_obj.direction,
            "telegram": sent,
            "execution_allowed": False,
            "saved_path": str(saved_path) if saved_path else None,
        },
    )
    raise typer.Exit(0)


@app.command("scan-intraday-smc-watchlist")
def scan_intraday_smc_watchlist_cmd(
    use_ibkr: bool = typer.Option(
        False, "--ibkr", help="Fetch 4h/30m/5m/1m bars from IBKR (read-only).",
    ),
    chart: bool = typer.Option(False, "--chart"),
    telegram: bool = typer.Option(False, "--telegram"),
    limit: Optional[int] = typer.Option(
        20, "--limit", min=1, help="Max symbols to scan (default 20).",
    ),
    source: Optional[str] = typer.Option(
        "dynamic", "--source",
        help="static | dynamic | manual (alias of static).",
    ),
    save_json: bool = typer.Option(True, "--save-json/--no-save-json"),
    mode: str = typer.Option(
        "strict_and_aggressive",
        "--mode",
        help="Reserved for future filtering; default 'strict_and_aggressive'.",
    ),
) -> None:
    """ICT/SMC Intraday Liquidity Reversal V1 — watchlist scan (13D).

    Research-only. No orders. No paper execution. Writes:

    * data/intraday_smc/<date>-<SYMBOL>-intraday-smc.json (per-symbol)
    * data/intraday_smc/<date>-watchlist-intraday-smc-summary.json (summary)
    """
    from .strategies.ict_smc_intraday import (
        IntradayRiskConfig,
        scan_watchlist_with_ibkr,
    )

    if not use_ibkr:
        console.print(
            "[red]--ibkr is required for scan-intraday-smc-watchlist.[/red]"
        )
        raise typer.Exit(2)
    if mode not in {"strict_and_aggressive", "strict_only", "aggressive_only"}:
        console.print(
            "[red]--mode must be strict_and_aggressive|strict_only|aggressive_only[/red]"
        )
        raise typer.Exit(2)

    cfg, journal = _bootstrap()
    risk_cfg = IntradayRiskConfig()
    try:
        summary = scan_watchlist_with_ibkr(
            cfg,
            journal,
            use_ibkr=use_ibkr,
            chart=chart,
            telegram=telegram,
            limit=limit,
            source=source,
            save_json=save_json,
            risk_cfg=risk_cfg,
        )
    except FileNotFoundError:
        console.print(
            "[red]Build dynamic watchlist first (build-watchlist).[/red]"
        )
        raise typer.Exit(3)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)

    counts = dict(summary.get("counts") or {})
    tbl = Table(
        title=f"ICT/SMC Intraday watchlist — {summary.get('date', '?')} "
              f"(source={summary.get('source') or '-'})"
    )
    tbl.add_column("category")
    tbl.add_column("count", justify="right")
    for cat, n in counts.items():
        tbl.add_row(str(cat), str(n))
    console.print(tbl)

    p = summary.get("_saved_summary_path")
    if p:
        console.print(f"[green]Saved summary:[/green] {p}")
    if summary.get("ready_strict_symbols"):
        console.print(
            "[green]STRICT:[/green] "
            + ", ".join(summary["ready_strict_symbols"])
        )
    if summary.get("ready_aggressive_symbols"):
        console.print(
            "[cyan]AGGRESSIVE:[/cyan] "
            + ", ".join(summary["ready_aggressive_symbols"])
        )
    if summary.get("watch_symbols"):
        console.print(
            "[yellow]WATCH:[/yellow] "
            + ", ".join(summary["watch_symbols"])
        )

    journal.record_event(
        category="ict_smc_intraday",
        level="INFO",
        message="scan-intraday-smc-watchlist",
        payload={
            "source": summary.get("source"),
            "symbols_scanned": summary.get("symbols_scanned"),
            "counts": counts,
            "execution_allowed": False,
            "telegram_sent": bool(summary.get("_telegram_sent")),
            "saved_summary_path": p,
        },
    )
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# Prompt 13E — backtest engine commands
#
# These are RESEARCH-ONLY. They never connect to IBKR (except
# ``fetch-candles`` which explicitly fetches read-only history) and
# never place orders. Every output payload carries
# ``execution_allowed=False`` and ``paper_only=True``.
# ---------------------------------------------------------------------------
_BACKTEST_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BACKTEST_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")
_BACKTEST_SYMBOLS_RE = re.compile(r"^[A-Z]{1,5}(?:,[A-Z]{1,5})*$")
_BACKTEST_MODES = ("strict_only", "aggressive_only", "strict_and_aggressive")
_BACKTEST_DIRECTIONS = ("long_only", "short_only", "both")


def _parse_backtest_symbols(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    s = raw.strip().upper()
    if not _BACKTEST_SYMBOLS_RE.match(s):
        raise typer.BadParameter(
            f"--symbols must match {_BACKTEST_SYMBOLS_RE.pattern} "
            "(uppercase, comma-separated, 1–5 chars each)."
        )
    return s.split(",")


def _validate_backtest_date(label: str, value: str) -> str:
    if not _BACKTEST_DATE_RE.match(value or ""):
        raise typer.BadParameter(f"{label} must be YYYY-MM-DD; got {value!r}.")
    return value


@app.command("fetch-candles")
def fetch_candles_cmd(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Ticker, e.g. CRM."),
    timeframe: str = typer.Option(
        "1min", "--timeframe", "-t",
        help="One of: 1min, 5min, 30min, 4h, daily.",
    ),
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD inclusive."),
    end: str = typer.Option(..., "--end", help="YYYY-MM-DD inclusive."),
    use_ibkr: bool = typer.Option(
        False, "--ibkr",
        help="Required: enables IBKR read-only historical fetch.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Overwrite existing day files instead of merging.",
    ),
    use_rth: bool = typer.Option(
        True, "--use-rth/--no-use-rth",
        help="Restrict to Regular Trading Hours (default true).",
    ),
) -> None:
    """Fetch candles from IBKR and save to data/candles/{SYMBOL}/{TF}/{DATE}.csv.

    READ-ONLY. No orders. Trading is never touched. The cache is the
    sole input for ``backtest-intraday-smc[_watchlist]``.

    Notes
    -----
    * IBKR's ``reqHistoricalData`` returns the most recent ``duration``
      window, not an arbitrary date range. We request a window large
      enough to cover [start, end] and then slice into per-day CSVs.
    * Days for which IBKR returned zero rows are reported as gaps in
      the CLI output; we never invent fake candles.
    """
    from .backtests.candle_cache import (
        CandleCacheError,
        save_candles_csv,
    )
    from .smc_timeframes import resolve_timeframe_spec

    if not use_ibkr:
        console.print("[red]--ibkr is required for fetch-candles.[/red]")
        raise typer.Exit(2)

    try:
        sym = symbol.strip().upper()
        if not _BACKTEST_SYMBOL_RE.match(sym):
            raise typer.BadParameter(
                f"--symbol {symbol!r} must match {_BACKTEST_SYMBOL_RE.pattern}."
            )
        _validate_backtest_date("--start", start)
        _validate_backtest_date("--end", end)
        if start > end:
            raise typer.BadParameter("--start must be <= --end.")
    except typer.BadParameter as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)

    cfg = load_config()
    spec = resolve_timeframe_spec(timeframe, cfg)
    # Compute a duration that covers the requested window. We always
    # add a small buffer so IBKR returns the full inclusive range.
    try:
        d0 = datetime.strptime(start, "%Y-%m-%d")
        d1 = datetime.strptime(end, "%Y-%m-%d")
    except ValueError:
        console.print("[red]Invalid date.[/red]")
        raise typer.Exit(2)
    days = max((d1 - d0).days + 2, 2)
    duration = f"{days} D"
    bar_size = str(spec.bar_size)
    use_rth_flag = bool(use_rth) if use_rth is not None else bool(spec.use_rth)

    console.print(
        f"[cyan]fetch-candles[/cyan] {sym} {timeframe} "
        f"{start}..{end} duration='{duration}' bar_size='{bar_size}' "
        f"use_rth={use_rth_flag}  [yellow]READ-ONLY · NO ORDERS[/yellow]"
    )

    journal = Journal(cfg)
    bars: list[dict] = []
    client = None
    try:
        client = _connect(cfg, roster="candles")
        try:
            bars = client.get_intraday_bars(
                sym,
                duration=duration,
                bar_size=bar_size,
                what_to_show=str(spec.what_to_show or "TRADES"),
                use_rth=use_rth_flag,
            ) or []
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]IBKR fetch error: {exc}[/yellow]")
            bars = []
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    try:
        stats = save_candles_csv(
            project_root=Path(cfg.absolute("")),
            symbol=sym,
            timeframe=timeframe,
            bars=bars,
            start=start,
            end=end,
            force=force,
        )
    except CandleCacheError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)

    payload = stats.to_dict()
    payload["paper_only"] = True
    payload["execution_allowed"] = False
    console.print(
        Panel.fit(
            json.dumps(payload, indent=2, ensure_ascii=False),
            title=f"fetch-candles — {sym} {timeframe}",
            style="cyan",
        )
    )
    if stats.gaps:
        console.print(
            f"[yellow]Warning:[/yellow] {len(stats.gaps)} day(s) had no bars "
            "(weekend / holiday / no IBKR data)."
        )

    journal.record_event(
        category="backtest",
        level="INFO",
        message="fetch-candles",
        payload={
            "symbol": sym,
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "days_written": stats.days_written,
            "rows_written": stats.rows_written,
            "rows_deduped": stats.rows_deduped,
            "execution_allowed": False,
            "paper_only": True,
        },
    )
    raise typer.Exit(0)


def _run_backtest_intraday_smc(
    cfg: AppConfig,
    journal: Journal,
    *,
    symbols: list[str],
    start: str,
    end: str,
    mode: str,
    direction: str,
    rth_only: bool,
    chart: bool,
    source: str | None = None,
) -> dict:
    """Shared driver for ``backtest-intraday-smc`` and the watchlist variant."""
    from .backtests import (
        BacktestConfig,
        backtest_intraday_smc,
        save_backtest_artifacts,
    )
    from .strategies.ict_smc_intraday import IntradayRiskConfig

    if not symbols:
        console.print("[red]No symbols supplied for backtest.[/red]")
        raise typer.Exit(2)

    bcfg = BacktestConfig(
        symbols=tuple(symbols),
        start=start,
        end=end,
        mode=mode,
        direction=direction,
        rth_only=rth_only,
        risk_cfg=IntradayRiskConfig(),
    )
    run = backtest_intraday_smc(Path(cfg.absolute("")), bcfg)
    paths = save_backtest_artifacts(Path(cfg.absolute("")), run, chart=chart)

    summary = {
        "strategy_id": "ict_smc_intraday_v1",
        "paper_only": True,
        "execution_allowed": False,
        "symbols": symbols,
        "start": start,
        "end": end,
        "mode": mode,
        "direction": direction,
        "rth_only": rth_only,
        "source": source,
        "metrics": run.metrics.to_dict(),
        "trade_count": len(run.trades),
        "notes": list(run.notes),
        "artifacts": paths,
    }

    tbl = Table(
        title=f"Backtest summary — {start}..{end} (symbols={','.join(symbols)})"
    )
    tbl.add_column("metric")
    tbl.add_column("value", justify="right")
    m = run.metrics
    tbl.add_row("total_signals", str(m.total_signals))
    tbl.add_row("total_filled_trades", str(m.total_filled_trades))
    tbl.add_row("total_not_filled", str(m.total_not_filled))
    tbl.add_row(
        "win_rate", f"{m.win_rate*100:.1f}%" if m.win_rate is not None else "-"
    )
    tbl.add_row("average_r", f"{m.average_r:.3f}" if m.average_r is not None else "-")
    tbl.add_row("total_r", f"{m.total_r:.3f}")
    tbl.add_row("max_drawdown_r", f"{m.max_drawdown_r:.3f}")
    tbl.add_row(
        "profit_factor",
        f"{m.profit_factor:.3f}" if (m.profit_factor not in (None, float('inf'))) else "-",
    )
    console.print(tbl)
    console.print(f"[green]Saved summary:[/green] {paths.get('summary_json')}")
    if run.notes:
        for n in run.notes:
            console.print(f"[yellow]note:[/yellow] {n}")

    journal.record_event(
        category="backtest",
        level="INFO",
        message="backtest-intraday-smc",
        payload={
            "symbols": symbols,
            "start": start,
            "end": end,
            "mode": mode,
            "direction": direction,
            "filled_trades": m.total_filled_trades,
            "not_filled": m.total_not_filled,
            "win_rate": m.win_rate,
            "total_r": m.total_r,
            "execution_allowed": False,
            "paper_only": True,
        },
    )
    return summary


@app.command("candle-coverage")
def candle_coverage_cmd(
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD inclusive."),
    end: str = typer.Option(..., "--end", help="YYYY-MM-DD inclusive."),
    timeframe: str = typer.Option("1min", "--timeframe", help="Only 1min (default)."),
    symbols: str | None = typer.Option(
        None,
        "--symbols",
        help="Comma-separated UPPER tickers (1–5 letters), e.g. AAPL,CRM",
    ),
    core_basket: bool = typer.Option(
        False, "--core-basket", help="15-name core symbol basket.",
    ),
    watchlist: str | None = typer.Option(
        None, "--watchlist", help="Use `latest` for newest *-dynamic-watchlist.json",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit JSON only (no Rich panel / table).",
    ),
) -> None:
    """Check local 1m candle file coverage; read-only, no IBKR, no backtest run."""
    from .backtests.candle_coverage import (  # noqa: PLC0415
        CORE_BASKET,
        check_candle_coverage,
        load_latest_watchlist_symbols,
    )

    cfg, _journal = _bootstrap()
    root = Path(cfg.absolute(""))
    n_sources = int(bool(symbols and symbols.strip())) + int(core_basket) + int(
        bool((watchlist or "").strip())
    )
    if n_sources != 1:
        if as_json:
            console.print(
                json.dumps(
                    {
                        "error": "specify exactly one of --symbols, --core-basket, or --watchlist",
                    }
                )
            )
        else:
            console.print(
                "[red]candle-coverage: specify exactly one of "
                "--symbols, --core-basket, or --watchlist.[/red]"
            )
        raise typer.Exit(1)

    tf = (timeframe or "1min").strip().lower()
    if tf not in ("1min", "1m"):
        if as_json:
            console.print_json(data={"error": "only --timeframe 1min is supported"})
        else:
            console.print("[red]candle-coverage: only 1min is supported.[/red]")
        raise typer.Exit(1)

    sym_list: list[str] = []
    wl_path: str | None = None
    wl_err: str | None = None
    source_tag = "symbols"
    if core_basket:
        sym_list = list(CORE_BASKET)
        source_tag = "core_basket"
    elif (watchlist or "").strip():
        w = (watchlist or "").strip().lower()
        if w != "latest":
            if as_json:
                console.print_json(
                    data={"error": "only --watchlist latest is supported", "value": w}
                )
            else:
                console.print(
                    f"[red]candle-coverage: only `latest` is valid for --watchlist (got {w!r}).[/red]"
                )
            raise typer.Exit(1)
        sym_list, pth, err = load_latest_watchlist_symbols(root)
        wl_path = pth
        wl_err = err
        source_tag = "watchlist"
        if err is not None:
            payload: dict[str, Any] = {
                "source": source_tag,
                "requested_start": (start or "").strip(),
                "requested_end": (end or "").strip(),
                "timeframe": "1min",
                "total_symbols": 0,
                "ready_count": 0,
                "partial_count": 0,
                "missing_count": 0,
                "symbols_ready": [],
                "symbols_partial": [],
                "symbols_missing": [],
                "overall_status": "missing",
                "per_symbol": {},
                "trading_day_count": 0,
                "will_backtest_be_complete": False,
                "backtest_completeness_note": f"Watchlist not loaded: {err}",
                "watchlist_path": pth,
                "watchlist_error": err,
            }
            if as_json:
                print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
            else:
                console.print(Panel(json.dumps(payload, indent=2, default=str), title="candle-coverage", style="yellow"))
            raise typer.Exit(0)
    else:
        sym_list = [s.strip().upper() for s in (symbols or "").split(",") if s.strip()]
    try:
        rep = check_candle_coverage(
            sym_list, start, end, timeframe="1min", project_root=root
        )
    except ValueError as exc:
        if as_json:
            console.print_json(data={"error": str(exc)})
        else:
            console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    rep["source"] = source_tag
    if wl_path is not None:
        rep["watchlist_path"] = wl_path
    if as_json:
        print(json.dumps(rep, indent=2, ensure_ascii=False, default=str))
    else:
        console.print(
            Panel(
                json.dumps(rep, indent=2, ensure_ascii=False, default=str),
                title="candle-coverage (local 1m cache check)",
                style="cyan",
            )
        )
    raise typer.Exit(0)


@app.command("backtest-oneclick")
def backtest_oneclick_cmd(
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD inclusive."),
    end: str = typer.Option(..., "--end", help="YYYY-MM-DD inclusive."),
    symbols: Optional[str] = typer.Option(
        None, "--symbols", help="Comma-separated UPPER tickers (1–5 letters).",
    ),
    core_basket: bool = typer.Option(
        False, "--core-basket", help="15-name core symbol basket.",
    ),
    watchlist: Optional[str] = typer.Option(
        None, "--watchlist", help="Only `latest` (newest *-dynamic-watchlist.json).",
    ),
    timeframe: str = typer.Option("1min", "--timeframe", help="Only 1min."),
    strategy: str = typer.Option(
        "ict_smc_intraday_v1", "--strategy", help="Must be ict_smc_intraday_v1 and backtest_enabled.",
    ),
    mode: str = typer.Option(
        "strict_and_aggressive",
        "--mode",
        help="strict_only | aggressive_only | strict_and_aggressive.",
    ),
    direction: str = typer.Option("both", "--direction", help="long_only | short_only | both."),
    rth_only: bool = typer.Option(True, "--rth-only/--no-rth-only", help="RTH for backtest (default on)."),
    chart: bool = typer.Option(False, "--chart", help="Equity / R / hour PNGs."),
    allow_partial: bool = typer.Option(
        False, "--allow-partial",
        help="If set, backtest on available cache when fetch cannot complete coverage.",
    ),
    as_json: bool = typer.Option(False, "--json", help="JSON only to stdout (no Rich)."),
) -> None:
    """Check 1m coverage, fetch gaps from IBKR if needed, then run intraday backtest.

    READ-ONLY market data for fetch; no orders. Stops before backtest if coverage
    is incomplete and ``--allow-partial`` is not set (e.g. TWS offline)."""
    from .backtests.candle_coverage import (  # noqa: PLC0415
        CORE_BASKET,
        load_latest_watchlist_symbols,
    )
    from .backtests.oneclick_workflow import run_backtest_oneclick  # noqa: PLC0415

    cfg, _ = _bootstrap()
    if as_json:
        import logging as _log_mod  # noqa: PLC0415

        for _name in (
            "bot",
            "bot.ibkr_client",
        ) + _NOISY_LOGGERS:
            _log_mod.getLogger(_name).setLevel(_log_mod.CRITICAL)
    root = Path(cfg.absolute(""))
    n_sources = int(bool(symbols and symbols.strip())) + int(core_basket) + int(
        bool((watchlist or "").strip())
    )
    if n_sources != 1:
        if as_json:
            print(  # noqa: T201
                json.dumps(
                    {"error": "specify exactly one of --symbols, --core-basket, or --watchlist"},
                    ensure_ascii=False,
                )
            )
        else:
            console.print(
                "[red]backtest-oneclick: exactly one of --symbols, --core-basket, --watchlist.[/red]"
            )
        raise typer.Exit(1)

    tf = (timeframe or "1min").strip().lower()
    if tf not in ("1min", "1m"):
        if as_json:
            print(json.dumps({"error": "only --timeframe 1min is supported"}))  # noqa: T201
        else:
            console.print("[red]backtest-oneclick: only 1min.[/red]")
        raise typer.Exit(1)

    try:
        _validate_backtest_date("--start", start)
        _validate_backtest_date("--end", end)
    except typer.BadParameter as exc:
        if as_json:
            print(json.dumps({"error": str(exc)}))  # noqa: T201
        else:
            console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    if start > end:
        if as_json:
            print(json.dumps({"error": "--start must be on or before --end"}))  # noqa: T201
        else:
            console.print("[red]--start must be <= --end.[/red]")
        raise typer.Exit(2)

    if mode not in _BACKTEST_MODES:
        if as_json:
            print(
                json.dumps(  # noqa: T201
                    {"error": f"--mode must be one of {_BACKTEST_MODES}"},
                )
            )
        else:
            console.print(f"[red]--mode must be one of {_BACKTEST_MODES}[/red]")
        raise typer.Exit(2)
    if direction not in _BACKTEST_DIRECTIONS:
        if as_json:
            print(
                json.dumps(  # noqa: T201
                    {"error": f"--direction must be one of {_BACKTEST_DIRECTIONS}"},
                )
            )
        else:
            console.print(f"[red]--direction must be one of {_BACKTEST_DIRECTIONS}[/red]")
        raise typer.Exit(2)

    sym_list: list[str] = []
    source_tag = "symbols"
    if core_basket:
        sym_list = list(CORE_BASKET)
        source_tag = "core_basket"
    elif (watchlist or "").strip():
        w = (watchlist or "").strip().lower()
        if w != "latest":
            if as_json:
                print(
                    json.dumps(  # noqa: T201
                        {"error": "only --watchlist latest is supported", "value": w},
                    )
                )
            else:
                console.print("[red]backtest-oneclick: only --watchlist latest.[/red]")
            raise typer.Exit(1)
        sym_list, pth, err = load_latest_watchlist_symbols(root)
        source_tag = "watchlist"
        if err is not None:
            rep = {
                "error": "watchlist_not_loaded",
                "watchlist_path": pth,
                "watchlist_error": err,
                "complete_result": False,
            }
            if as_json:
                print(json.dumps(rep, indent=2, ensure_ascii=False, default=str))  # noqa: T201
            else:
                console.print(Panel(json.dumps(rep, indent=2, default=str), style="yellow"))
            raise typer.Exit(0)
    else:
        try:
            sym_list = _parse_backtest_symbols(symbols or "")
        except typer.BadParameter as exc:
            if as_json:
                print(json.dumps({"error": str(exc)}))  # noqa: T201
            else:
                console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc

    rep = run_backtest_oneclick(
        root,
        cfg,
        symbols=sym_list,
        start=start,
        end=end,
        source=source_tag,
        strategy=strategy,
        mode=mode,
        direction=direction,
        rth_only=bool(rth_only),
        chart=bool(chart),
        allow_partial=bool(allow_partial),
        timeframe="1min",
    )
    if as_json:
        print(json.dumps(rep, indent=2, ensure_ascii=False, default=str))  # noqa: T201
    else:
        console.print(
            Panel(
                json.dumps(rep, indent=2, ensure_ascii=False, default=str),
                title="backtest-oneclick (coverage → optional fetch → backtest)",
                style="cyan",
            )
        )
    # Exit 1 only for hard validation / strategy errors (message in rep["error"] from workflow).
    if rep.get("error") and "strategy" in str(rep["error"]).lower() and not rep.get(
        "backtest_ran"
    ):
        raise typer.Exit(1)
    if rep.get("error") == "no symbols after normalisation":
        raise typer.Exit(1)
    raise typer.Exit(0)


@app.command("backtest-intraday-smc")
def backtest_intraday_smc_cmd(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Single ticker, e.g. CRM."),
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD inclusive."),
    end: str = typer.Option(..., "--end", help="YYYY-MM-DD inclusive."),
    mode: str = typer.Option(
        "strict_and_aggressive",
        "--mode",
        help="strict_only | aggressive_only | strict_and_aggressive.",
    ),
    direction: str = typer.Option(
        "both", "--direction",
        help="long_only | short_only | both.",
    ),
    rth_only: bool = typer.Option(
        True, "--rth-only/--no-rth-only",
        help="Restrict simulation to RTH bars (default true).",
    ),
    chart: bool = typer.Option(
        False, "--chart",
        help="Render equity / R-distribution / by-hour PNGs.",
    ),
) -> None:
    """Run the no-lookahead backtest for ``ict_smc_intraday_v1`` on one symbol."""
    sym = symbol.strip().upper()
    if not _BACKTEST_SYMBOL_RE.match(sym):
        console.print(
            f"[red]--symbol {symbol!r} must match {_BACKTEST_SYMBOL_RE.pattern}.[/red]"
        )
        raise typer.Exit(2)
    try:
        _validate_backtest_date("--start", start)
        _validate_backtest_date("--end", end)
    except typer.BadParameter as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)
    if mode not in _BACKTEST_MODES:
        console.print(f"[red]--mode must be one of {_BACKTEST_MODES}.[/red]")
        raise typer.Exit(2)
    if direction not in _BACKTEST_DIRECTIONS:
        console.print(f"[red]--direction must be one of {_BACKTEST_DIRECTIONS}.[/red]")
        raise typer.Exit(2)

    cfg, journal = _bootstrap()
    _run_backtest_intraday_smc(
        cfg,
        journal,
        symbols=[sym],
        start=start,
        end=end,
        mode=mode,
        direction=direction,
        rth_only=rth_only,
        chart=chart,
        source="single",
    )
    raise typer.Exit(0)


@app.command("backtest-intraday-smc-watchlist")
def backtest_intraday_smc_watchlist_cmd(
    symbols: Optional[str] = typer.Option(
        None, "--symbols",
        help="Comma-separated tickers, e.g. CRM,AMZN,AAPL.",
    ),
    source: Optional[str] = typer.Option(
        None, "--source",
        help="static | dynamic | manual (alias of static). Mutually exclusive with --symbols.",
    ),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    mode: str = typer.Option("strict_and_aggressive", "--mode"),
    direction: str = typer.Option("both", "--direction"),
    rth_only: bool = typer.Option(True, "--rth-only/--no-rth-only"),
    chart: bool = typer.Option(False, "--chart"),
    limit: Optional[int] = typer.Option(
        None, "--limit", min=1, help="Cap total symbols (default no cap).",
    ),
) -> None:
    """Backtest ``ict_smc_intraday_v1`` over a list of symbols (no IBKR call).

    Symbol selection precedence:

    1. ``--symbols CRM,AMZN`` (explicit list).
    2. ``--source dynamic`` reads the latest dynamic watchlist.
    3. ``--source static`` (or ``manual``) reads the static config.
    """
    cfg, journal = _bootstrap()

    chosen: list[str] = []
    src_label = ""
    if symbols:
        chosen = _parse_backtest_symbols(symbols)
        src_label = "manual_list"
    elif source:
        s = source.strip().lower()
        if s in {"manual"}:
            s = "static"
        if s not in {"static", "dynamic"}:
            console.print("[red]--source must be static, dynamic, or manual.[/red]")
            raise typer.Exit(2)
        src_label = s
        if s == "dynamic":
            from .watchlist_builder import load_dynamic_watchlist
            dw = load_dynamic_watchlist(cfg)
            if dw is None:
                console.print(
                    "[red]Dynamic watchlist not built; run "
                    "'python -m bot.cli build-watchlist' first.[/red]"
                )
                raise typer.Exit(3)
            chosen = [r.symbol for r in dw.symbols if not getattr(r, "blocked", False)]
        else:
            eqs = (getattr(cfg, "watchlist", {}) or {}).get("equities") or []
            for e in eqs:
                if isinstance(e, dict) and e.get("symbol"):
                    chosen.append(str(e["symbol"]).upper())
                elif isinstance(e, str):
                    chosen.append(e.upper())
            if not chosen:
                chosen = list((getattr(cfg, "watchlist", {}) or {}).get("static_core") or [])
            chosen = [s.upper() for s in chosen]
    else:
        console.print("[red]Provide either --symbols or --source.[/red]")
        raise typer.Exit(2)

    if not chosen:
        console.print("[red]Watchlist is empty; nothing to backtest.[/red]")
        raise typer.Exit(2)

    if limit is not None and limit > 0:
        chosen = chosen[: int(limit)]

    try:
        _validate_backtest_date("--start", start)
        _validate_backtest_date("--end", end)
    except typer.BadParameter as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)
    if mode not in _BACKTEST_MODES:
        console.print(f"[red]--mode must be one of {_BACKTEST_MODES}.[/red]")
        raise typer.Exit(2)
    if direction not in _BACKTEST_DIRECTIONS:
        console.print(f"[red]--direction must be one of {_BACKTEST_DIRECTIONS}.[/red]")
        raise typer.Exit(2)

    _run_backtest_intraday_smc(
        cfg,
        journal,
        symbols=chosen,
        start=start,
        end=end,
        mode=mode,
        direction=direction,
        rth_only=rth_only,
        chart=chart,
        source=src_label,
    )
    raise typer.Exit(0)


@app.command("backtest-report")
def backtest_report_cmd(
    latest: bool = typer.Option(
        False, "--latest",
        help="Print the latest backtest summary written under data/backtests/intraday/.",
    ),
    summary_path: Optional[str] = typer.Option(
        None, "--path",
        help="Print a specific summary JSON path instead of the latest.",
    ),
    email: bool = typer.Option(
        False,
        "--email",
        help="Send metrics summary by email (Resend) if configured.",
    ),
) -> None:
    """Print a saved backtest summary (read-only, never connects to IBKR)."""
    from .backtests import REPORT_DIRNAME

    cfg = load_config()
    out_dir = Path(cfg.absolute(REPORT_DIRNAME))

    target: Path | None = None
    if summary_path:
        target = Path(summary_path)
    elif latest or True:  # default behaviour: latest
        if not out_dir.exists():
            console.print(
                "[yellow]No backtest reports yet — run "
                "'backtest-intraday-smc' first.[/yellow]"
            )
            raise typer.Exit(0)
        candidates = sorted(out_dir.glob("*-backtest-summary.json"))
        if not candidates:
            console.print(
                "[yellow]No backtest reports yet — run "
                "'backtest-intraday-smc' first.[/yellow]"
            )
            raise typer.Exit(0)
        target = candidates[-1]

    if not target or not target.exists():
        console.print(f"[red]Backtest summary not found: {target}[/red]")
        raise typer.Exit(2)

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]Could not read {target}: {exc}[/red]")
        raise typer.Exit(2)

    metrics = data.get("metrics") or {}
    cfg_block = data.get("config") or {}
    tbl = Table(title=f"Backtest report — {target.name}")
    tbl.add_column("metric")
    tbl.add_column("value", justify="right")
    for k in (
        "total_signals", "total_filled_trades", "total_not_filled",
        "win_rate", "average_r", "median_r", "total_r", "max_drawdown_r",
        "profit_factor", "average_bars_held", "strict_count",
        "aggressive_count", "strict_win_rate", "aggressive_win_rate",
        "long_win_rate", "short_win_rate",
    ):
        v = metrics.get(k)
        if isinstance(v, float) and "rate" in k:
            tbl.add_row(k, f"{v*100:.1f}%")
        else:
            tbl.add_row(k, "-" if v is None else str(v))
    console.print(tbl)
    console.print(
        f"[cyan]symbols=[/cyan] {','.join(cfg_block.get('symbols') or [])} "
        f"[cyan]range=[/cyan] {cfg_block.get('start')}..{cfg_block.get('end')} "
        f"[cyan]mode=[/cyan] {cfg_block.get('mode')} "
        f"[cyan]direction=[/cyan] {cfg_block.get('direction')}"
    )
    by_sym = metrics.get("by_symbol") or []
    if by_sym:
        st = Table(title="By symbol")
        for col in ("symbol", "trades", "wins", "losses", "win_rate", "average_r", "total_r"):
            st.add_column(col, justify="right" if col != "symbol" else "left")
        for row in by_sym:
            wr = row.get("win_rate")
            wr_s = "-" if wr is None else f"{wr*100:.1f}%"
            st.add_row(
                str(row.get("symbol")),
                str(row.get("trades", 0)),
                str(row.get("wins", 0)),
                str(row.get("losses", 0)),
                wr_s,
                "-" if row.get("average_r") is None else f"{row['average_r']:.3f}",
                "-" if row.get("total_r") is None else f"{row['total_r']:.3f}",
            )
        console.print(st)

    console.print(f"[green]Source:[/green] {target}")
    if email:
        try:
            from .reports.report_email import send_report_email  # noqa: PLC0415
            from .reports.report_email_status import (  # noqa: PLC0415
                record_email_outcome,
            )

            subj = f"[Strategy Lab] Backtest {target.name}"
            mlines = [
                f"total_signals: {metrics.get('total_signals')}",
                f"win_rate: {metrics.get('win_rate')}",
                f"total_r: {metrics.get('total_r')}",
                f"max_dd_r: {metrics.get('max_drawdown_r')}",
                f"pf: {metrics.get('profit_factor')}",
            ]
            btxt = "Strategy Lab — Backtest summary\n" + "\n".join(
                mlines
            ) + f"\nfile: {target}"
            ob = send_report_email(
                to_cfg=cfg.settings.reports.email_to,
                subject=subj[:200],
                text_body=btxt[:20_000],
            )
            record_email_outcome(
                cfg.project_root,
                "backtest",
                status=ob.status,
                to_addr=cfg.settings.reports.email_to,
                report_key=target.stem,
                detail=ob.detail,
            )
            console.print(f"[cyan]email:[/cyan] {ob.status}")
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            console.print(f"[yellow]email error (non-fatal): {exc}[/yellow]")
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# Prompt 13L-alt: ticker edge profiles (research-only; no order placement)
# ---------------------------------------------------------------------------


@app.command("build-edge-profile")
def build_edge_profile_cmd(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Ticker, e.g. CRM."),
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="YYYY-MM-DD"),
    strategy: str = typer.Option("ict_smc_intraday_v1", "--strategy"),
    mode: str = typer.Option(
        "strict_and_aggressive", "--mode",
        help="strict_and_aggressive | strict_only | aggressive_only",
    ),
    direction: str = typer.Option("both", "--direction", help="long_only | short_only | both"),
    fetch: bool = typer.Option(
        False, "--fetch",
        help="If cache missing, fetch 1m candles from IBKR (read-only).",
    ),
    min_trades: int = typer.Option(30, "--min-trades", min=1),
) -> None:
    """Run one backtest and write today's edge profile batch JSON + Markdown."""
    from .edge.build_batch import build_edges_for_symbols  # noqa: PLC0415

    cfg, _journal = _bootstrap()
    _validate_backtest_date("--start", start)
    _validate_backtest_date("--end", end)
    if start > end:
        console.print("[red]--start must be <= --end[/red]")
        raise typer.Exit(2)
    mstrong = min_trades * 2
    profs, notes, meta = build_edges_for_symbols(
        cfg,
        [symbol],
        start=start,
        end=end,
        strategy_id=strategy,
        mode=mode,
        direction=direction,
        fetch=fetch,
        min_trades_moderate=int(min_trades),
        min_trades_strong=int(mstrong),
        top_n=None,
    )
    if notes:
        for n in notes:
            console.print(f"[dim]{n}[/dim]")
    console.print(
        Panel.fit(
            json.dumps(
                {**meta, "profiles": [p.to_dict() for p in profs]},
                indent=2,
                default=str,
                ensure_ascii=False,
            ),
            title="build-edge-profile",
            style="cyan",
        )
    )
    raise typer.Exit(0)


@app.command("build-edge-profiles")
def build_edge_profiles_cmd(
    symbols: str = typer.Option(
        ..., "--symbols", help="Comma-separated tickers, e.g. AAPL,AMD,NVDA",
    ),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    strategy: str = typer.Option("ict_smc_intraday_v1", "--strategy"),
    mode: str = typer.Option("strict_and_aggressive", "--mode"),
    direction: str = typer.Option("both", "--direction"),
    fetch: bool = typer.Option(False, "--fetch"),
    min_trades: int = typer.Option(30, "--min-trades", min=1),
    top: Optional[int] = typer.Option(None, "--top", help="Top N in report only."),
) -> None:
    """Backtest a basket, compute edge profiles, save under data/edge_profiles/."""
    from .edge.build_batch import build_edges_for_symbols  # noqa: PLC0415

    cfg, _journal = _bootstrap()
    _validate_backtest_date("--start", start)
    _validate_backtest_date("--end", end)
    syms = [x.strip().upper() for x in (symbols or "").split(",") if x.strip()]
    if not syms:
        console.print("[red]No symbols.[/red]")
        raise typer.Exit(2)
    mstrong = min_trades * 2
    profs, notes, meta = build_edges_for_symbols(
        cfg,
        syms,
        start=start,
        end=end,
        strategy_id=strategy,
        mode=mode,
        direction=direction,
        fetch=fetch,
        min_trades_moderate=int(min_trades),
        min_trades_strong=int(mstrong),
        top_n=top,
    )
    for n in notes:
        console.print(f"[dim]{n}[/dim]")
    console.print(
        Panel.fit(
            json.dumps(
                {**meta, "count": len(profs)},
                indent=2,
                default=str,
                ensure_ascii=False,
            ),
            title="build-edge-profiles",
            style="green",
        )
    )
    if meta.get("written"):
        console.print(f"[green]Wrote:[/green] {meta['written']}")
    raise typer.Exit(0)


@app.command("edge-profile-report")
def edge_profile_report_cmd(
    latest: bool = typer.Option(True, "--latest", help="Show newest edge-profiles.json."),
) -> None:
    """Read-only: print the latest edge profile JSON path + summary (no IBKR)."""
    from .edge.reports import latest_edge_profiles_path  # noqa: PLC0415

    if not latest:
        console.print("[yellow]Only --latest is supported for now.[/yellow]")
    cfg = load_config()
    p = latest_edge_profiles_path(Path(cfg.absolute("")))
    if p is None or not p.is_file():
        console.print(
            "[yellow]No edge profiles yet — run build-edge-profiles.[/yellow]"
        )
        raise typer.Exit(0)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]Could not read {p}: {exc}[/red]")
        raise typer.Exit(2)
    n = len(data.get("profiles") or [])
    console.print(Panel.fit(f"{p}\nprofiles={n}", title="edge-profile-report", style="cyan"))
    console.print_json(data={"path": str(p), "date": data.get("date"), "n": n})
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
# Automatic MTF paper (Prompt 10H, PAPER only)
# ---------------------------------------------------------------------------


@app.command("paper-reconcile")
def paper_reconcile_cmd() -> None:
    """Same as [bold]reconcile[/bold]: read-only broker check (PAPER, no new orders)."""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "bot.cli", "reconcile"],
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    raise typer.Exit(int(r.returncode or 0))


@app.command("auto-paper-mtf")
def auto_paper_mtf_cmd(
    source: str = typer.Option("dynamic", "--source", help="static or dynamic watchlist source."),
    limit: int = typer.Option(20, "--limit", min=1),
    max_paper_trades: int = typer.Option(
        1, "--max-paper-trades", min=0, help="Max bracket submissions this pass."
    ),
    telegram: bool = typer.Option(False, "--telegram"),
    chart: bool = typer.Option(False, "--chart", help="Render MTF debug charts."),
    bypass_runtime_guard: bool = typer.Option(
        False,
        "--bypass-runtime-guard",
        help="Ignore data/runtime/mtf_auto_paper_enabled (operator override).",
    ),
) -> None:
    """One pass: preflight, MTF watchlist scan, PAPER bracket only if FULL+eligible (config + runtime)."""
    from dataclasses import asdict

    from .auto_paper_mtf import run_auto_paper_mtf

    cfg, journal = _bootstrap()
    r = run_auto_paper_mtf(
        cfg,
        journal,
        source=source,
        limit=limit,
        max_paper_trades=max_paper_trades,
        telegram=telegram,
        chart=chart,
        bypass_runtime_guard=bypass_runtime_guard,
    )
    d = asdict(r)
    console.print(
        Panel.fit(
            json.dumps(d, indent=2, default=str, ensure_ascii=False),
            title="auto-paper-mtf",
        )
    )
    raise typer.Exit(0 if r.ok else 2)


@app.command("run-auto-paper-mtf-loop")
def run_auto_paper_mtf_loop_cmd(
    source: str = typer.Option("dynamic", "--source"),
    limit: int = typer.Option(20, "--limit", min=1),
    max_paper_trades: int = typer.Option(1, "--max-paper-trades", min=0),
    interval_minutes: int = typer.Option(5, "--interval-minutes", min=1),
    market_hours_only: bool = typer.Option(
        True, "--market-hours-only/--all-hours", help="US RTH 09:45-15:30 NY for trading steps.",
    ),
    telegram: bool = typer.Option(False, "--telegram"),
    once: bool = typer.Option(
        False, "--once", help="Single cycle and exit (health check / dry).",
    ),
    stop_after_minutes: Optional[float] = typer.Option(
        None, "--stop-after-minutes", help="Stop the loop after this many minutes.",
    ),
) -> None:
    """Background-style loop: regime, watchlist, auto-paper, diagnostic. PAPER only."""
    from .auto_paper_loop import run_auto_paper_mtf_loop

    cfg, journal = _bootstrap()
    console.print(
        "[cyan]run-auto-paper-mtf-loop: PAPER only; block_live_trading must stay true. "
        "Ctrl+C to stop.[/cyan]"
    )
    run_auto_paper_mtf_loop(
        cfg,
        journal,
        source=source,
        limit=limit,
        max_paper_trades=max_paper_trades,
        interval_minutes=interval_minutes,
        market_hours_only=market_hours_only,
        telegram=telegram,
        once=once,
        stop_after_minutes=stop_after_minutes,
    )
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# 13F: ICT/SMC intraday paper bracket commands (PAPER only).
# ---------------------------------------------------------------------------
@app.command("auto-paper-intraday-smc")
def auto_paper_intraday_smc_cmd(
    source: str = typer.Option(
        "dynamic", "--source", help="static | dynamic | manual.",
    ),
    limit: int = typer.Option(20, "--limit", min=1),
    telegram: bool = typer.Option(False, "--telegram"),
    chart: bool = typer.Option(False, "--chart"),
) -> None:
    """One ICT/SMC intraday paper bracket pass (paper account only).

    Hard rules: paper-only, every order is a LIMIT bracket, kill switch +
    runtime intraday flag honoured, reconciliation gated by config, no
    duplicate same-symbol entries. See PROMPT 13F.
    """
    from dataclasses import asdict

    from .execution.intraday_paper_execution import (
        run_intraday_paper_pass,
        serialize_paper_submission,
    )

    if source not in {"static", "dynamic", "manual"}:
        console.print("[red]--source must be static|dynamic|manual[/red]")
        raise typer.Exit(2)

    from .execution.intraday_paper_sizing import ledger_snapshot_for_status  # noqa: PLC0415

    cfg, journal = _bootstrap()
    result = run_intraday_paper_pass(
        cfg,
        journal,
        source=source,
        limit=limit,
        telegram=telegram,
        chart=chart,
    )
    ip = cfg.settings.trading.intraday_paper
    payload = asdict(result)
    payload["submissions"] = [serialize_paper_submission(s) for s in result.submissions]
    payload["paper_sizing_ledger"] = ledger_snapshot_for_status(cfg, ip)
    payload["tif"] = str(ip.tif)
    payload["max_notional_per_order_usd"] = float(ip.max_notional_per_order_usd)
    payload["max_daily_notional_usd"] = float(ip.max_daily_notional_usd)
    payload["max_equity_per_position_pct"] = float(ip.max_equity_per_position_pct)
    payload["max_quantity_per_order"] = int(ip.max_quantity_per_order)
    console.print(
        Panel.fit(
            json.dumps(payload, indent=2, default=str, ensure_ascii=False),
            title="auto-paper-intraday-smc",
            style="cyan",
        )
    )
    for s in result.submissions:
        if s.submitted_to_broker and not s.submitted:
            console.print(
                "[bold red]Paper order reached broker, but bracket protection is incomplete. "
                "Verify/cancel in TWS.[/bold red]"
            )
            break
    journal.record_event(
        category="ict_smc_intraday_paper",
        level="INFO",
        message="auto-paper-intraday-smc",
        payload={
            "orders_submitted": result.orders_submitted,
            "strict_ready_count": result.strict_ready_count,
            "aggressive_ready_count": result.aggressive_ready_count,
            "last_status": result.last_status,
            "last_reason": result.last_reason,
            "paper_only": True,
            "live_trading_allowed": False,
        },
    )
    raise typer.Exit(0 if result.last_status not in {"failed", "error"} else 2)


@app.command("run-auto-paper-intraday-loop")
def run_auto_paper_intraday_loop_cmd(
    source: str = typer.Option("dynamic", "--source"),
    limit: int = typer.Option(20, "--limit", min=1),
    interval_seconds: int = typer.Option(60, "--interval-seconds", min=5),
    market_hours_only: bool = typer.Option(
        True,
        "--market-hours-only/--ignore-market-hours",
        help="US RTH 09:45-15:30 NY for intraday paper submissions.",
    ),
    telegram: bool = typer.Option(False, "--telegram"),
    once: bool = typer.Option(False, "--once"),
    stop_after_minutes: Optional[float] = typer.Option(
        None, "--stop-after-minutes", help="Stop the loop after this many minutes.",
    ),
    heartbeat_minutes: int = typer.Option(
        30, "--heartbeat-minutes", min=1,
        help="Minimum minutes between Telegram heartbeats (no spam every cycle).",
    ),
    session: str = typer.Option(
        "full",
        "--session",
        help="RTH gating: 'full' 09:45–15:30 NY, 'morning' 09:45–11:30 NY (smoke; future use).",
    ),
) -> None:
    """ICT/SMC intraday paper bracket loop. PAPER only. Ctrl+C to stop."""
    from .auto_paper_intraday_loop import run_auto_paper_intraday_loop

    if source not in {"static", "dynamic", "manual"}:
        console.print("[red]--source must be static|dynamic|manual[/red]")
        raise typer.Exit(2)
    sess = (session or "full").strip().lower()
    if sess not in {"full", "morning"}:
        console.print("[red]--session must be full|morning[/red]")
        raise typer.Exit(2)

    cfg, journal = _bootstrap()
    console.print(
        "[cyan]run-auto-paper-intraday-loop: PAPER only; block_live_trading must stay true. "
        "Ctrl+C to stop.[/cyan]"
    )
    if sess == "morning":
        console.print(
            "[dim]--session morning: new entries only 09:45–11:30 America/New_York.[/dim]"
        )
    try:
        run_auto_paper_intraday_loop(
            cfg,
            journal,
            source=source,
            limit=limit,
            interval_seconds=interval_seconds,
            market_hours_only=market_hours_only,
            telegram=telegram,
            once=once,
            stop_after_minutes=stop_after_minutes,
            heartbeat_minutes=heartbeat_minutes,
            session=sess,
        )
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted by user.[/yellow]")
    raise typer.Exit(0)


@app.command("automatic-paper-engine-readiness")
def automatic_paper_engine_readiness_cmd(
    as_json: bool = typer.Option(
        True,
        "--json/--no-json",
        help="Print machine-readable preflight (default on).",
    ),
    probe_ibkr: bool = typer.Option(
        False,
        "--probe-ibkr",
        help="Reconcile with broker; requires TWS and journal.",
    ),
) -> None:
    """Read-only automatic paper engine gates (file + optional IBKR probe)."""
    from .automatic_paper_preflight import (  # noqa: PLC0415
        build_automatic_paper_engine_preflight,
    )

    cfg, journal = _bootstrap()
    j = journal if probe_ibkr else None
    payload = build_automatic_paper_engine_preflight(
        cfg, j, probe_ibkr=probe_ibkr
    )
    if as_json:
        console.print(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        )
    else:
        st = "PASS" if payload.get("ok") else "BLOCKED"
        bl = payload.get("blockers") or []
        body = "\n".join(str(x) for x in bl) if bl else "(no blockers)"
        console.print(
            Panel.fit(
                f"{st}\n{body}",
                title="automatic-paper-engine-readiness",
                style="green" if payload.get("ok") else "red",
            )
        )
    raise typer.Exit(0 if payload.get("ok") else 2)


@app.command("run-automatic-paper-engine")
def run_automatic_paper_engine_cmd(
    session: str = typer.Option(
        "full",
        "--session",
        help="morning: 09:45–11:30 NY; full: 09:45–15:30 NY (new entries).",
    ),
    source: str = typer.Option("dynamic", "--source", help="static | dynamic | manual."),
    limit: int = typer.Option(20, "--limit", min=1),
    interval_seconds: int = typer.Option(60, "--sleep-seconds", min=5),
    market_hours_only: bool = typer.Option(
        True,
        "--market-hours-only/--ignore-market-hours",
    ),
    telegram: bool = typer.Option(
        False, "--telegram", help="Meaningful Telegram only (no per-cycle noise)."
    ),
    report_on_exit: bool = typer.Option(
        True, "--report-on-exit/--no-report-on-exit"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Validate preflight and exit; no runtime ON; no loop."
    ),
    json_out: bool = typer.Option(False, "--json"),
    max_cycles: Optional[int] = typer.Option(
        None, "--max-cycles", help="Stop after this many pass iterations (smoke/CI)."
    ),
    once: bool = typer.Option(False, "--once", help="One pass and exit (alias for -max-cycles 1)."),
    stop_after_minutes: Optional[float] = typer.Option(
        None, "--stop-after-minutes", help="Stop the loop after N minutes."
    ),
    probe_ibkr: bool = typer.Option(
        True, "--probe-ibkr/--no-probe-ibkr", help="Full preflight with broker (default on)."
    ),
    no_runtime: bool = typer.Option(
        False, "--no-runtime-on", help="Do not write intraday runtime ON (engine may skip trades)."
    ),
) -> None:
    """ICT/SMC automatic paper engine — PAPER only, LIMIT brackets, market-hours gated."""
    from .automatic_paper_engine import (  # noqa: PLC0415
        run_automatic_paper_engine,
    )

    sess = (session or "full").strip().lower()
    if sess not in {"full", "morning"}:
        console.print("[red]--session must be full|morning[/red]")
        raise typer.Exit(2)
    if source not in {"static", "dynamic", "manual"}:
        console.print("[red]--source must be static|dynamic|manual[/red]")
        raise typer.Exit(2)

    eff_probe = bool(probe_ibkr) and not bool(dry_run)
    m_cycles = int(max_cycles) if max_cycles is not None else None
    if once and m_cycles is None:
        m_cycles = 1

    cfg, journal = _bootstrap()
    out = run_automatic_paper_engine(
        cfg,
        journal,
        session=sess,
        source=source,
        limit=limit,
        interval_seconds=interval_seconds,
        market_hours_only=market_hours_only,
        telegram=telegram,
        report_on_exit=report_on_exit and not dry_run,
        dry_run=dry_run,
        max_cycles=m_cycles,
        once=False,
        stop_after_minutes=stop_after_minutes,
        turn_runtime_on=not no_runtime and not dry_run,
        preflight_probe_ibkr=eff_probe,
    )
    if json_out or dry_run:
        console.print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    else:
        if out.get("blockers"):
            console.print(
                Panel.fit(
                    "\n".join(str(b) for b in out["blockers"]) or "blocked",
                    title="run-automatic-paper-engine: BLOCKED",
                    style="red",
                )
            )
        else:
            console.print(
                Panel.fit(
                    json.dumps(out, indent=2, ensure_ascii=False, default=str),
                    title="run-automatic-paper-engine",
                    style="cyan",
                )
            )
    raise typer.Exit(0 if not out.get("blockers") else 2)


@app.command("full-auto-paper-readiness")
def full_auto_paper_readiness_cmd(
    as_json: bool = typer.Option(
        True,
        "--json/--no-json",
        help="Machine-readable readiness (default on).",
    ),
    probe_ibkr: bool = typer.Option(
        False,
        "--probe-ibkr",
        help="Optional broker reconcile (TWS + journal).",
    ),
    session: str = typer.Option(
        "full",
        "--session",
        help="full | morning — which trading window label to use.",
    ),
) -> None:
    """Read-only full-auto paper supervisor gates (UI-safe without --probe-ibkr)."""
    from pathlib import Path as _Path  # noqa: PLC0415

    from .full_auto_paper_readiness import build_full_auto_paper_readiness  # noqa: PLC0415

    cfg, journal = _bootstrap()
    root = _Path(cfg.project_root)
    sess = (session or "full").strip().lower()
    if sess not in {"full", "morning"}:
        console.print("[red]--session must be full|morning[/red]")
        raise typer.Exit(2)
    payload = build_full_auto_paper_readiness(
        root,
        cfg,
        journal if probe_ibkr else None,
        probe_ibkr=probe_ibkr,
        session=sess,
    )
    if as_json:
        console.print(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        )
    else:
        st = "OK" if payload.get("ok") else "BLOCKED"
        bl = payload.get("blockers") or []
        body = "\n".join(str(x) for x in bl) if bl else "(no blockers)"
        console.print(
            Panel.fit(
                f"{st}\n{body}",
                title="full-auto-paper-readiness",
                style="green" if payload.get("ok") else "red",
            )
        )
    raise typer.Exit(0 if payload.get("ok") else 2)


@app.command("run-full-auto-paper-supervisor")
def run_full_auto_paper_supervisor_cmd(
    session: str = typer.Option(
        "full",
        "--session",
        help="morning | full (default full).",
    ),
    telegram: Optional[bool] = typer.Option(
        None,
        "--telegram/--no-telegram",
        help="Default: on if TELEGRAM_* configured.",
    ),
    report_on_exit: bool = typer.Option(
        True,
        "--report-on-exit/--no-report-on-exit",
    ),
    once: bool = typer.Option(False, "--once", help="Single supervisor iteration or one news pass."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Readiness + state only; no engine, no broker trading path.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Print JSON result to stdout."),
    sleep_seconds: float = typer.Option(
        60.0,
        "--sleep-seconds",
        min=5.0,
        help="Poll interval when waiting for session or when blocked.",
    ),
    market_open_check_only: bool = typer.Option(
        False,
        "--market-open-check-only",
        help="Print readiness and exit (no loop).",
    ),
    no_trade: bool = typer.Option(
        False,
        "--no-trade",
        help="Supervise / alert only; never start the automatic paper engine.",
    ),
    news_only: bool = typer.Option(
        False,
        "--news-only",
        help="Run market-news-check loop only (no engine).",
    ),
    max_runtime_minutes: Optional[float] = typer.Option(
        None,
        "--max-runtime-minutes",
        help="Stop supervisor after N minutes (optional).",
    ),
) -> None:
    """Outer full-auto paper supervisor — gates, Telegram blockers, ICT/SMC engine (paper)."""
    from .full_auto_paper_supervisor import run_full_auto_paper_supervisor  # noqa: PLC0415

    sess = (session or "full").strip().lower()
    if sess not in {"full", "morning"}:
        console.print("[red]--session must be full|morning[/red]")
        raise typer.Exit(2)

    cfg, journal = _bootstrap()
    eff_telegram = (
        bool(cfg.telegram.is_configured) if telegram is None else bool(telegram)
    )
    out = run_full_auto_paper_supervisor(
        cfg,
        journal,
        session=sess,
        telegram=eff_telegram,
        report_on_exit=report_on_exit,
        once=once,
        dry_run=dry_run,
        sleep_seconds=float(sleep_seconds),
        market_open_check_only=market_open_check_only,
        no_trade=no_trade,
        news_only=news_only,
        max_runtime_minutes=max_runtime_minutes,
    )
    if json_out or dry_run or market_open_check_only:
        console.print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    else:
        console.print(
            Panel.fit(
                json.dumps(out, indent=2, ensure_ascii=False, default=str)[:12_000],
                title="run-full-auto-paper-supervisor",
                style="cyan",
            )
        )
    raise typer.Exit(0)


@app.command("intraday-paper-status")
def intraday_paper_status_cmd(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Print intraday paper config + runtime + last loop state (read-only).

    Never connects to IBKR. Safe to run from any context.
    """
    from .execution.intraday_paper_execution import (
        INTRADAY_AUTO_PAPER_ENABLED_RELPATH,
        INTRADAY_LOOP_STATE_RELPATH,
        KILL_SWITCH_RELPATH,
        PAPER_ORDERS_DIR,
        is_intraday_paper_runtime_enabled,
        is_kill_switch_active,
    )
    from .execution.intraday_paper_sizing import ledger_snapshot_for_status  # noqa: PLC0415

    cfg, _journal = _bootstrap()
    ip = cfg.settings.trading.intraday_paper
    runtime_on, runtime_explicit_off = is_intraday_paper_runtime_enabled(cfg)
    kill = is_kill_switch_active(cfg)
    state_path = Path(cfg.absolute(INTRADAY_LOOP_STATE_RELPATH))
    audit_dir = Path(cfg.absolute(PAPER_ORDERS_DIR))
    latest_audit: Path | None = None
    if audit_dir.exists():
        cands = sorted(audit_dir.glob("*-intraday-paper-orders.jsonl"))
        latest_audit = cands[-1] if cands else None
    state_payload: dict[str, Any] = {}
    if state_path.exists():
        try:
            state_payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state_payload = {}
    payload: dict[str, Any] = {
        "config_enabled": bool(ip.enabled),
        "fully_automatic": bool(ip.fully_automatic),
        "allow_strict_entries": bool(ip.allow_strict_entries),
        "allow_aggressive_entries": bool(ip.allow_aggressive_entries),
        "risk_per_trade_pct": float(ip.risk_per_trade_pct),
        "max_concurrent_positions": int(ip.max_concurrent_positions),
        "max_one_position_per_symbol": bool(ip.max_one_position_per_symbol),
        "require_reconciliation_pass": bool(ip.require_reconciliation_pass),
        "no_new_entries_before": ip.no_new_entries_before,
        "no_new_entries_after": ip.no_new_entries_after,
        "exit_open_positions_at": ip.exit_open_positions_at,
        "paper_only": True,
        "live_trading_allowed": False,
        "market_orders_allowed": False,
        "bracket_required": True,
        "stop_required": True,
        "target_required": True,
        "dry_run": bool(ip.dry_run),
        "min_rr": float(ip.min_rr),
        "tif": str(ip.tif),
        "max_notional_per_order_usd": float(ip.max_notional_per_order_usd),
        "max_daily_notional_usd": float(ip.max_daily_notional_usd),
        "max_equity_per_position_pct": float(ip.max_equity_per_position_pct),
        "max_quantity_per_order": int(ip.max_quantity_per_order),
        "edge_profile_enabled": bool(getattr(ip, "edge_profile_enabled", True)),
        "unknown_edge_policy": str(
            getattr(ip, "unknown_edge_policy", "allow_strict_small_risk")
        ),
        "unknown_edge_risk_multiplier": float(
            getattr(ip, "unknown_edge_risk_multiplier", 0.25)
        ),
        "allow_aggressive_without_edge_profile": bool(
            getattr(ip, "allow_aggressive_without_edge_profile", False)
        ),
        "kill_switch": kill,
        "runtime_intraday_on": runtime_on,
        "runtime_intraday_off_explicit": runtime_explicit_off,
        "runtime_flag_path": str(cfg.absolute(INTRADAY_AUTO_PAPER_ENABLED_RELPATH)),
        "kill_switch_path": str(cfg.absolute(KILL_SWITCH_RELPATH)),
        "loop_state_path": str(state_path),
        "loop_state_exists": state_path.exists(),
        "loop_state": state_payload,
        "latest_audit_log_path": str(latest_audit) if latest_audit else None,
        "last_worst_bracket_integrity": state_payload.get("last_worst_bracket_integrity"),
        "last_bracket_incomplete": bool(
            state_payload.get("last_bracket_incomplete", False)
        ),
        "paper_sizing_ledger": ledger_snapshot_for_status(cfg, ip),
    }
    if as_json:
        console.print_json(data=payload)
    else:
        console.print(
            Panel.fit(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                title="intraday-paper-status",
                style="cyan",
            )
        )
    raise typer.Exit(0)


@app.command("auto-loop-readiness")
def auto_loop_readiness_cmd(
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON.",
    ),
    probe_ibkr: bool = typer.Option(
        False,
        "--probe-ibkr",
        help="If set, may connect to TWS/IB for reconciliation (read-only).",
    ),
) -> None:
    """Read-only checklist before run-auto-paper-intraday-loop. Does not start the loop or place orders."""
    from .auto_loop_readiness import build_auto_loop_readiness  # noqa: PLC0415

    cfg, journal = _bootstrap()
    payload = build_auto_loop_readiness(
        cfg.project_root, cfg, journal, probe_ibkr=probe_ibkr
    )
    if as_json:
        console.print_json(data=payload)
    else:
        console.print(
            Panel.fit(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                title="auto-loop-readiness",
                style="cyan",
            )
        )
    raise typer.Exit(0)


@app.command("eod-paper-checklist")
def eod_paper_checklist_cmd() -> None:
    """Read-only EOD US paper review steps. No IBKR, no email, no orders.

    After market close, run these from a terminal (TWS paper logged in) in order;
    for file-only daily summary without live broker, use paper-daily-report with --latest.
    """
    payload: dict[str, Any] = {
        "description": (
            "End-of-day paper checklist (this command only prints; it does not run subcommands)"
        ),
        "paper_only": True,
        "no_orders": True,
        "default_morning_window_ny": "09:45–11:30 (forward-test smoke, CLI --session morning)",
        "rth_new_entries_ny": "default 09:45–15:30 from intraday_paper config",
        "sequence": [
            {
                "step": 1,
                "cli": "python3 -m bot.cli open-orders",
                "note": "Read-only; requires TWS/IB if you want live broker state.",
            },
            {
                "step": 2,
                "cli": "python3 -m bot.cli portfolio",
                "note": "Read-only account + positions when connected.",
            },
            {
                "step": 3,
                "cli": "python3 -m bot.cli paper-reconcile",
                "note": "Reconcile local journal vs paper broker.",
            },
            {
                "step": 4,
                "cli": "python3 -m bot.cli paper-daily-report --latest --email",
                "note": (
                    "Writes data/reports/paper; --email uses Resend when configured, "
                    "else status skipped_missing_credentials (no crash)."
                ),
            },
        ],
        "alternate_file_only": "python3 -m bot.cli paper-daily-report --latest (no TWS; audit files only)",
    }
    console.print(
        Panel.fit(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            title="eod-paper-checklist (read-only)",
            style="cyan",
        )
    )
    raise typer.Exit(0)


@app.command("news-monitor-readiness")
def news_monitor_readiness_cmd(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Read-only: env + config for market-moving news monitor. Does not fetch news."""
    from .reports.news_monitor_readiness import (  # noqa: PLC0415
        build_news_monitor_readiness,
    )

    cfg, _journal = _bootstrap()
    payload = build_news_monitor_readiness(cfg.project_root, cfg)
    if as_json:
        console.print_json(data=payload)
    else:
        console.print(
            Panel.fit(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                title="news-monitor-readiness",
                style="cyan",
            )
        )
    raise typer.Exit(0)


@app.command("email-config-status")
def email_config_status_cmd(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Read-only: Resend + recipient readiness. Booleans and missing field names only."""
    from .config import get_dotenv_load_warning
    from .reports.email_config_status import (  # noqa: PLC0415
        build_email_config_status,
    )

    cfg, _journal = _bootstrap()
    payload: dict[str, object] = {**build_email_config_status(cfg), "dotenv_load_warning": get_dotenv_load_warning()}
    if as_json:
        console.print_json(data=payload)
    else:
        console.print(
            Panel.fit(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                title="email-config-status",
                style="cyan",
            )
        )
    raise typer.Exit(0)


@app.command("market-news-check")
def market_news_check_cmd(
    symbols: str | None = typer.Option(
        None, "--symbols", help="Comma-separated tickers, e.g. AAPL,NVDA",
    ),
    watchlist: str | None = typer.Option(
        None,
        "--watchlist",
        help="Use `latest` to load the newest *-dynamic-watchlist.json",
    ),
    core_basket: bool = typer.Option(
        False, "--core-basket", help="15-name core coverage basket (local cache / lab).",
    ),
    market_moving_only: bool = typer.Option(
        True,
        "--market-moving-only/--all-scored",
        help="Only consider items at or above min score (default on).",
    ),
    lookback_minutes: int = typer.Option(90, "--lookback-minutes", min=1),
    min_score: int | None = typer.Option(
        None, "--min-score", help="Override config news_reporting.min_market_moving_score",
    ),
    telegram: bool = typer.Option(
        False,
        "--telegram",
        help="Send a short HTML Telegram if not --dry-run and creds present.",
    ),
    email: bool = typer.Option(
        False,
        "--email",
        help="Reserved; email for breaking news is off by default in config.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Default --dry-run: never send Telegram. Use --no-dry-run to allow send.",
    ),
    as_json: bool = typer.Option(False, "--json", help="JSON only to stdout."),
) -> None:
    """Score recent headlines (Finnhub/FMP) for market-moving keywords. Never trades."""
    from pathlib import Path as _Path  # noqa: PLC0415

    from .backtests.candle_coverage import (  # noqa: PLC0415
        CORE_BASKET,
        load_latest_watchlist_symbols,
    )
    from .reports.market_news_check import run_market_news_check  # noqa: PLC0415

    cfg, journal = _bootstrap()
    root = _Path(cfg.project_root)
    nsrc = int(bool(symbols and symbols.strip())) + int(
        bool((watchlist or "").strip())
    ) + int(bool(core_basket))
    if nsrc != 1:
        err = "specify exactly one of --symbols, --watchlist latest, or --core-basket"
        if as_json:
            console.print_json(data={"error": err})
        else:
            console.print(f"[red]market-news-check: {err}[/red]")
        raise typer.Exit(1)
    if (watchlist or "").strip() and (watchlist or "").strip().lower() != "latest":
        if as_json:
            console.print_json(
                data={"error": "only --watchlist latest is supported", "value": watchlist}
            )
        else:
            console.print("[red]market-news-check: only --watchlist latest.[/red]")
        raise typer.Exit(1)

    sym_list: list[str] = []
    if core_basket:
        sym_list = list(CORE_BASKET)
    elif (watchlist or "").strip().lower() == "latest":
        sym_list, _p, err = load_latest_watchlist_symbols(root)
        if err is not None:
            if as_json:
                console.print_json(data={"error": err, "symbols": []})
            else:
                console.print(f"[red]{err}[/red]")
            raise typer.Exit(1)
    else:
        sym_list = [s.strip().upper() for s in (symbols or "").split(",") if s.strip()]
    if not sym_list:
        if as_json:
            console.print_json(data={"error": "no symbols", "symbols": []})
        else:
            console.print("[red]market-news-check: no symbols resolved.[/red]")
        raise typer.Exit(1)

    nr = cfg.settings.news_reporting
    thr = int(min_score) if min_score is not None else int(nr.min_market_moving_score)
    if not market_moving_only:
        pass  # min_score still applies for Telegram winner

    result = run_market_news_check(
        root,
        cfg,
        journal,
        symbols=sym_list,
        market_moving_only=bool(market_moving_only),
        lookback_minutes=lookback_minutes,
        min_score=thr,
        want_telegram=bool(telegram),
        want_email=bool(email),
        dry_run=bool(dry_run),
    )
    if as_json:
        console.print_json(data=result)
    else:
        console.print(
            Panel.fit(
                json.dumps(result, indent=2, ensure_ascii=False, default=str)[:8000],
                title="market-news-check",
                style="cyan",
            )
        )
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# 13I: Local paper activation (settings.local + runtime; PAPER, bracket only).
# ---------------------------------------------------------------------------


@app.command("paper-activation-status")
def paper_activation_status_cmd(
    probe_ibkr: bool = typer.Option(
        False,
        "--probe-ibkr",
        help="If set, run reconciliation (read-only broker check).",
    ),
) -> None:
    """Local paper activation snapshot. Connects IBKR only with --probe-ibkr."""
    from .paper_activation import build_paper_activation_status  # noqa: PLC0415

    cfg, journal = _bootstrap()
    p = build_paper_activation_status(cfg, probe_ibkr=probe_ibkr, journal=journal)
    console.print(
        Panel.fit(
            json.dumps(p, indent=2, ensure_ascii=False, default=str),
            title="paper-activation-status",
            style="cyan",
        )
    )
    raise typer.Exit(0)


@app.command("write-paper-local-config")
def write_paper_local_config_cmd(
    write: bool = typer.Option(
        False,
        "--write",
        help="Write config/settings.local.yaml (creates timestamped backup if present).",
    ),
) -> None:
    """Merge PAPER-only keys into config/settings.local.yaml. Never modifies settings.yaml."""
    from .paper_activation import write_paper_local_config_file  # noqa: PLC0415

    cfg = load_config()
    r = write_paper_local_config_file(
        cfg.project_root, dry_run=not write, write=write
    )
    if not r.get("ok"):
        console.print(f"[red]{r.get('error')}[/red]")
        raise typer.Exit(2)
    if r.get("proposed_yaml"):
        console.print(
            Panel.fit(
                r["proposed_yaml"],
                title="proposed config/settings.local.yaml",
                style="cyan",
            )
        )
    if r.get("wrote"):
        console.print(f"[green]Wrote {r['path']}[/green]")
    if r.get("backup_path"):
        console.print(f"[dim]Backup: {r['backup_path']}[/dim]")
    if not write:
        console.print("[dim]Dry-run. Pass --write to create/merge settings.local.yaml.[/dim]")
    raise typer.Exit(0)


@app.command("intraday-paper-on")
def intraday_paper_on_cmd() -> None:
    """Set data/runtime/intraday_auto_paper_enabled = 1. No IBKR. No orders."""
    from .paper_activation import set_intraday_runtime_flag  # noqa: PLC0415

    cfg, _j = _bootstrap()
    p = set_intraday_runtime_flag(cfg, on=True)
    console.print(
        Panel.fit(
            f"intraday auto-paper runtime: ON\npath: {p}",
            title="intraday-paper-on",
            style="green",
        )
    )
    raise typer.Exit(0)


@app.command("intraday-paper-off")
def intraday_paper_off_cmd() -> None:
    """Set data/runtime/intraday_auto_paper_enabled = 0. No IBKR. No orders."""
    from .paper_activation import set_intraday_runtime_flag  # noqa: PLC0415

    cfg, _j = _bootstrap()
    p = set_intraday_runtime_flag(cfg, on=False)
    console.print(
        Panel.fit(
            f"intraday auto-paper runtime: explicit OFF (0)\npath: {p}",
            title="intraday-paper-off",
            style="yellow",
        )
    )
    raise typer.Exit(0)


@app.command("paper-readiness-check")
def paper_readiness_check_cmd(
    use_intraday: bool = typer.Option(
        True,
        "--intraday/--no-intraday",
        help="Intraday paper forward-test check (default on).",
    ),
    probe_ibkr: bool = typer.Option(
        False,
        "--probe-ibkr",
        help="Reconcile with broker (read-only).",
    ),
    scan: bool = typer.Option(
        False,
        "--scan",
        help="Run a fresh intraday watchlist scan (read-only; needs TWS).",
    ),
    source: str = typer.Option("dynamic", "--source", help="static | dynamic | manual"),
    limit: int = typer.Option(20, "--limit", min=1),
) -> None:
    """Pre-flight for intraday paper. Does not place orders."""
    from .paper_activation import run_paper_readiness_check  # noqa: PLC0415

    cfg, journal = _bootstrap()
    rr = run_paper_readiness_check(
        cfg,
        journal,
        intraday=use_intraday,
        probe_ibkr=probe_ibkr,
        run_scan=scan,
        source=source,
        limit=limit,
    )
    console.print(
        Panel.fit(
            json.dumps(rr.payload, indent=2, ensure_ascii=False, default=str),
            title="paper-readiness-check",
            style="green" if rr.passed else "red",
        )
    )
    raise typer.Exit(0 if rr.passed else 2)


@app.command("first-paper-pass")
def first_paper_pass_cmd(
    source: str = typer.Option("dynamic", "--source", help="static | dynamic | manual"),
    limit: int = typer.Option(20, "--limit", min=1),
    telegram: bool = typer.Option(False, "--telegram"),
) -> None:
    """Status → readiness (scan+recon) → one auto-paper-intraday-smc. No loop."""
    from .paper_activation import run_first_paper_pass  # noqa: PLC0415

    cfg, journal = _bootstrap()
    out = run_first_paper_pass(
        cfg, journal, source=source, limit=limit, telegram=telegram
    )
    console.print(
        Panel.fit(
            json.dumps(out, indent=2, default=str, ensure_ascii=False),
            title="first-paper-pass",
            style="cyan",
        )
    )
    ex = out.get("execution") or {}
    for row in ex.get("submissions") or []:
        if row.get("submitted_to_broker") and not row.get("submitted"):
            console.print(
                "[bold red]Paper order reached broker, but bracket protection is incomplete. "
                "Verify/cancel in TWS.[/bold red]"
            )
            break
    if out.get("result") in {"failed", "error"}:
        raise typer.Exit(2)
    raise typer.Exit(0)


@app.command("strategy-lab-engine-status")
def strategy_lab_engine_status_cmd(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout (machine-readable)."),
) -> None:
    """Read-only snapshot of the Strategy Lab engine and config (legacy shape).

    Prefer ``engine-status`` for the full payload including latest artifacts
    and UI process hints. This command omits ``artifacts`` / ``ui_process``.
    """
    from .engine_status import build_engine_status_payload  # noqa: PLC0415

    cfg = load_config()
    payload: dict[str, Any] = dict(build_engine_status_payload(cfg, probe_ui=False))
    payload.pop("artifacts", None)
    payload.pop("ui_process", None)
    if as_json:
        sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
    else:
        console.print(
            Panel.fit(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                title="strategy-lab-engine-status",
                style="cyan",
            )
        )
    raise typer.Exit(0)


@app.command("engine-status")
def engine_status_cmd(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    probe_ui: bool = typer.Option(
        False,
        "--probe-ui",
        help="If the Strategy Lab UI PID file exists, probe GET /healthz (no IBKR).",
    ),
) -> None:
    """Read-only end-to-end lab snapshot: config, latest artifacts, optional UI /healthz.

    Never connects to IBKR/TWS. Never places orders. Safe for daily use.
    """
    from .engine_status import run_engine_status_cli  # noqa: PLC0415

    raise typer.Exit(run_engine_status_cli(as_json=as_json, probe_ui=probe_ui))


@app.command("data-status")
def data_status_cmd(
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON sizes on stdout.",
    ),
) -> None:
    """Show disk usage for local data/ categories (read-only, no deletions)."""
    from .data_lifecycle import data_status  # noqa: PLC0415

    cfg, _j = _bootstrap()
    st = data_status(cfg.project_root)
    if as_json:
        d = {
            "project_root": st.project_root,
            "total_bytes": st.total_bytes,
            "dirs": [
                {"path": s.relpath, "bytes": s.bytes, "file_count": s.file_count}
                for s in st.dirs
            ],
        }
        print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        t = Table(title="data/ sizes (read-only)", show_lines=True)
        t.add_column("path")
        t.add_column("bytes", justify="right")
        t.add_column("files", justify="right")
        for s in st.dirs:
            t.add_row(s.relpath, str(s.bytes), str(s.file_count))
        t.add_row("— total —", str(st.total_bytes), "")
        console.print(t)
    raise typer.Exit(0)


@app.command("data-cleanup")
def data_cleanup_cmd(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Delete eligible files. Without this flag, dry-run only.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run",
        help="List files that would be removed (default).",
    ),
) -> None:
    """Remove old ephemeral report/chart/log files. Never touches audit or runtime/."""
    from .data_lifecycle import data_cleanup  # noqa: PLC0415

    cfg, _j = _bootstrap()
    do_apply = bool(apply)
    if dry_run and not apply:
        do_apply = False
    res = data_cleanup(cfg.project_root, apply=do_apply)
    lines = res.would_delete if not do_apply else res.deleted
    body = "\n".join(lines[:200]) if lines else "(nothing eligible)"
    console.print(
        Panel.fit(
            body,
            title="data-cleanup (dry-run)" if not do_apply else "data-cleanup (apply)",
        )
    )
    if res.skipped_protected:
        console.print(
            f"[dim]skipped_protected: {len(res.skipped_protected)} paths[/dim]"
        )
    console.print(res.message)
    raise typer.Exit(0)


@app.command("paper-daily-report")
def paper_daily_report_cmd(
    report_date: Optional[str] = typer.Option(
        None,
        "--date",
        help="Report date YYYY-MM-DD (file-based; no IBKR).",
    ),
    today: bool = typer.Option(False, "--today", help="Use today's UTC date."),
    latest: bool = typer.Option(
        False,
        "--latest",
        help="Pick the latest date from local paper-order / scan artifacts.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Print only JSON to stdout (still writes files unless --no-save).",
    ),
    write_markdown: bool = typer.Option(
        True,
        "--markdown/--no-markdown",
        help="Write companion .md (default on).",
    ),
    save: bool = typer.Option(True, "--save/--no-save", help="Write JSON/Markdown under output-dir."),
    telegram: bool = typer.Option(
        False,
        "--telegram",
        help="Send a short Chinese digest via Telegram if configured (no crash if missing).",
    ),
    output_dir: str = typer.Option(
        "data/reports/paper",
        "--output-dir",
        help="Directory for report files (under project root).",
    ),
    email: bool = typer.Option(
        False,
        "--email",
        help="Send summary via Resend if RESEND_API_KEY is set (no crash if missing).",
    ),
) -> None:
    """File-based daily paper report. Does not connect to IBKR or place orders."""
    from pathlib import Path as _Path  # noqa: PLC0415

    from .reports.paper_daily import build_daily_paper_report  # noqa: PLC0415
    from .reports.render_markdown import (  # noqa: PLC0415
        format_paper_daily_telegram_zh,
        render_paper_daily_markdown,
    )
    from .reports.report_paths import (  # noqa: PLC0415
        infer_latest_report_date,
        utc_today_str,
    )

    cfg, _journal = _bootstrap()
    root = _Path(cfg.project_root)
    if latest:
        d = infer_latest_report_date(root)
    elif today:
        d = utc_today_str()
    elif report_date:
        d = report_date.strip()[:10]
    else:
        d = utc_today_str()

    payload = build_daily_paper_report(root, d)
    md = render_paper_daily_markdown(payload)
    out_abs = cfg.absolute(output_dir)
    stem = f"{d}-paper-daily-report"
    mp = None
    if save:
        outd = _Path(out_abs)
        outd.mkdir(parents=True, exist_ok=True)
        jp = outd / f"{stem}.json"
        jp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        if write_markdown:
            mp = outd / f"{stem}.md"
            mp.write_text(md, encoding="utf-8")
        else:
            mp = None
        if not as_json:
            lines = [f"json: {jp}", f"markdown: {mp or '(skipped)'}"]
            console.print(Panel.fit("\n".join(lines), title="paper-daily-report", style="cyan"))
    if telegram:
        try:
            send_telegram_message(
                format_paper_daily_telegram_zh(payload),
                cfg=cfg,
                journal=None,
            )
        except (OSError, RuntimeError, ValueError, TypeError):
            if not as_json:
                console.print("[yellow]Telegram optional send failed or skipped.[/yellow]")

    want_email = email or (cfg.settings.reports.email_enabled and save)
    if want_email and not as_json and save:
        try:
            from .reports.report_email import send_report_email  # noqa: PLC0415
            from .reports.report_email_status import (  # noqa: PLC0415
                record_email_outcome,
            )

            if mp and mp.exists():
                text_body = (
                    f"Strategy Lab — Paper Daily ({d})\n\n"
                    + mp.read_text(encoding="utf-8")[:20_000]
                )
            else:
                text_body = f"Strategy Lab — Paper Daily ({d})\n\n" + md[:20_000]
            out = send_report_email(
                to_cfg=cfg.settings.reports.email_to,
                subject=f"[Strategy Lab] Paper daily {d}",
                text_body=text_body,
            )
            record_email_outcome(
                cfg.project_root,
                "paper_daily",
                status=out.status,
                to_addr=cfg.settings.reports.email_to,
                report_key=d,
                detail=out.detail,
            )
            if not as_json:
                console.print(
                    f"[cyan]email:[/cyan] {out.status} ({out.detail[:80]})"
                )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            if not as_json:
                console.print(f"[yellow]email error (non-fatal): {exc}[/yellow]")
    if as_json:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")
    elif not save:
        console.print(Panel.fit(json.dumps(payload, indent=2, ensure_ascii=False, default=str), title="paper-daily-report"))
    raise typer.Exit(0)


@app.command("paper-weekly-report")
def paper_weekly_report_cmd(
    week_start: Optional[str] = typer.Option(
        None, "--week-start", help="Week start YYYY-MM-DD (inclusive)."
    ),
    week_end: Optional[str] = typer.Option(
        None, "--week-end", help="Week end YYYY-MM-DD (inclusive)."
    ),
    latest: bool = typer.Option(
        False,
        "--latest",
        help="Rolling last 7 UTC days ending today (no IBKR).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print only JSON to stdout."),
    write_markdown: bool = typer.Option(
        True,
        "--markdown/--no-markdown",
        help="Write companion .md (default on).",
    ),
    save: bool = typer.Option(True, "--save/--no-save"),
    output_dir: str = typer.Option("data/reports/paper", "--output-dir"),
    email: bool = typer.Option(
        False,
        "--email",
        help="Send summary email via Resend if configured (no crash if missing).",
    ),
) -> None:
    """Aggregate daily file-based reports into a weekly summary. No IBKR."""
    from pathlib import Path as _Path  # noqa: PLC0415

    from .reports.paper_weekly import build_weekly_latest, build_weekly_paper_report  # noqa: PLC0415
    from .reports.render_markdown import render_paper_weekly_markdown  # noqa: PLC0415

    cfg, _journal = _bootstrap()
    root = _Path(cfg.project_root)
    if latest:
        payload = build_weekly_latest(root)
    elif week_start and week_end:
        payload = build_weekly_paper_report(root, week_start.strip()[:10], week_end.strip()[:10])
    elif not week_start and not week_end:
        payload = build_weekly_latest(root)
    else:
        console.print("[red]Provide both --week-start and --week-end, or use --latest.[/red]")
        raise typer.Exit(2)

    md = render_paper_weekly_markdown(payload)
    ws = str(payload.get("week_start", ""))
    we = str(payload.get("week_end", ""))
    stem = f"{ws}_to_{we}-paper-weekly-report"
    out_abs = cfg.absolute(output_dir)
    mp2 = None
    if save:
        outd = _Path(out_abs)
        outd.mkdir(parents=True, exist_ok=True)
        jp = outd / f"{stem}.json"
        jp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        if write_markdown:
            mp2 = outd / f"{stem}.md"
            mp2.write_text(md, encoding="utf-8")
        else:
            mp2 = None
        if not as_json:
            console.print(
                Panel.fit(
                    f"json: {jp}\nmarkdown: {mp2 or '(skipped)'}",
                    title="paper-weekly-report",
                    style="cyan",
                )
            )
    if as_json:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")
    elif not save:
        console.print(
            Panel.fit(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                title="paper-weekly-report",
            )
        )

    want_wemail = email or (cfg.settings.reports.email_enabled and save)
    if want_wemail and not as_json and save:
        try:
            from .reports.report_email import send_report_email  # noqa: PLC0415
            from .reports.report_email_status import (  # noqa: PLC0415
                record_email_outcome,
            )

            if write_markdown and mp2 and mp2.exists():
                wbody = f"Strategy Lab — Paper weekly {ws} → {we}\n\n" + mp2.read_text(
                    encoding="utf-8"
                )[:20_000]
            else:
                wbody = f"Strategy Lab — Paper weekly {ws} → {we}\n\n" + md[:20_000]
            outw = send_report_email(
                to_cfg=cfg.settings.reports.email_to,
                subject=f"[Strategy Lab] Paper weekly {ws} to {we}",
                text_body=wbody,
            )
            record_email_outcome(
                cfg.project_root,
                "paper_weekly",
                status=outw.status,
                to_addr=cfg.settings.reports.email_to,
                report_key=f"{ws}_{we}",
                detail=outw.detail,
            )
            if not as_json:
                console.print(
                    f"[cyan]email:[/cyan] {outw.status} ({outw.detail[:80]})"
                )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            if not as_json:
                console.print(f"[yellow]email error (non-fatal): {exc}[/yellow]")
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


@app.command("telegram-command-listener")
def telegram_command_listener_cmd(
    once: bool = typer.Option(
        False,
        "--once",
        help="Fetch one getUpdates batch, process, persist offset, then exit.",
    ),
    poll_interval_seconds: Optional[int] = typer.Option(
        None,
        "--poll-interval-seconds",
        help="Override config/telegram.yaml polling_interval_seconds for long poll.",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Print one JSON object per cycle to stdout (no secrets)."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Do not contact Telegram; print listener state and exit.",
    ),
    allow_all_chats: bool = typer.Option(
        False,
        "--allow-all-chats",
        help="DEBUG ONLY: process messages from any chat id (disables allowlist).",
    ),
) -> None:
    """Long-poll Telegram getUpdates, dispatch read-only /status, /news, /reports, etc.

    Persists update offset in data/runtime/telegram_command_listener_state.json.
    """
    from .telegram_commands import run_telegram_command_listener_main
    from rich import print as rprint

    try:
        out = run_telegram_command_listener_main(
            once=once,
            json_mode=json_out,
            dry_run=dry_run,
            poll_interval_seconds=poll_interval_seconds,
            allow_all=allow_all_chats,
        )
    except RuntimeError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=1)
    if dry_run and isinstance(out, dict):
        print(
            json.dumps(out, ensure_ascii=False, indent=2 if json_out else None)
        )
        raise typer.Exit(0)
    if once and isinstance(out, dict):
        if not json_out:
            console.print(
                Panel.fit(
                    json.dumps(out, ensure_ascii=False, indent=2),
                    title="telegram-command-listener",
                )
            )
        raise typer.Exit(0)
    # Unbounded run never returns; once/dry only paths exit above.


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


# ---------------------------------------------------------------------------
# Research Intelligence Layer v2 (Prompt 13B)
# ---------------------------------------------------------------------------
#
# Commands here NEVER place orders, NEVER enable live trading, and only
# connect to IBKR when explicitly invoked (ibkr-news-status /
# ibkr-news-fetch / research-report). All other research commands are
# pure-disk readers.
#
# UI buttons hit these via the LocalCommandRunner allowlist
# (bot_ui/services/safety.py); see also docs/deployment-architecture.md.
def _latest_dynamic_watchlist_symbols(cfg: AppConfig) -> tuple[list[str], str | None]:
    root = cfg.absolute("data/watchlists")
    if not root.exists():
        return [], None
    files = sorted(root.glob("*-dynamic-watchlist.json"))
    if not files:
        return [], None
    try:
        with files[-1].open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return [], str(files[-1])
    symbols: list[str] = []
    if isinstance(data, dict):
        items = data.get("symbols") or data.get("items") or data.get("watchlist") or []
        if isinstance(items, list):
            for it in items:
                if isinstance(it, str):
                    symbols.append(it.upper())
                elif isinstance(it, dict) and it.get("symbol"):
                    symbols.append(str(it["symbol"]).upper())
    return symbols, str(files[-1])


def _latest_market_regime(cfg: AppConfig) -> dict[str, object]:
    root = cfg.absolute("data/market_regime")
    if not root.exists():
        return {}
    files = sorted(root.glob("*.json"))
    if not files:
        return {}
    try:
        with files[-1].open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _latest_smc_summary(cfg: AppConfig) -> dict[str, object]:
    root = cfg.absolute("data/mtf_smc")
    if not root.exists():
        return {}
    files = sorted(root.glob("*-watchlist-mtf-smc-summary.json"))
    if not files:
        return {}
    try:
        with files[-1].open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


@app.command("macro-calendar")
def macro_calendar_cmd(
    today: bool = typer.Option(
        False, "--today", help="Show today's macro events (US/Eastern)."
    ),
    date: Optional[str] = typer.Option(
        None, "--date", help="Show macro events for YYYY-MM-DD (overrides --today)."
    ),
) -> None:
    """Print the manual macro calendar (Chinese-friendly).

    Reads ``config/macro_calendar.yaml``. Never connects to IBKR. Never
    places orders. Macro events default to ``soft_flag``; ``hard_block``
    must be set explicitly per row in the YAML.
    """
    from .research_providers.manual_macro_calendar import (
        load_macro_calendar,
        render_calendar_zh,
    )

    cfg, _journal = _bootstrap()
    cal = load_macro_calendar(cfg)

    if date:
        events = cal.for_date(date)
        label = date
    elif today:
        events = cal.for_today_et()
        label = "今日 (US/Eastern)"
    else:
        events = list(cal.events)
        label = "全部条目"

    text = render_calendar_zh(events, target_label=label)
    console.print(Panel.fit(text, title="macro-calendar", style="cyan"))
    if cal.notes:
        for n in cal.notes:
            console.print(f"[dim]note: {n}[/dim]")
    console.print(
        "[dim]execution_allowed=false. This CLI never places orders "
        "and never modifies broker state.[/dim]"
    )
    raise typer.Exit(code=0)


@app.command("ibkr-news-status")
def ibkr_news_status_cmd() -> None:
    """Probe IBKR for news provider entitlements.

    Connects to IBKR read-only, calls reqNewsProviders, prints/returns
    a structured status, then disconnects. Never places orders.
    """
    from .research_providers.ibkr_news_provider import (
        connect_for_news,
        get_provider_status,
    )

    cfg, journal = _bootstrap()
    client = None
    try:
        try:
            client = connect_for_news(cfg)
        except (IBKRClientError, LiveTradingBlocked) as exc:
            status_payload = {
                "ibkr_news_available": False,
                "providers_detected": [],
                "missing_entitlements": [],
                "notes": [f"connect failed: {exc!r}"],
            }
            console.print(
                Panel.fit(
                    json.dumps(status_payload, ensure_ascii=False, indent=2),
                    title="ibkr-news-status",
                    style="yellow",
                )
            )
            journal.record_event(
                category="research_news",
                level="WARNING",
                message="ibkr-news-status connect failed",
                payload=status_payload,
            )
            raise typer.Exit(code=2)

        status = get_provider_status(cfg, client=client)
        console.print(
            Panel.fit(
                json.dumps(status.to_dict(), ensure_ascii=False, indent=2),
                title="ibkr-news-status",
                style="cyan" if status.ibkr_news_available else "yellow",
            )
        )
        journal.record_event(
            category="research_news",
            level="INFO",
            message="ibkr-news-status",
            payload=status.to_dict(),
        )
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
    raise typer.Exit(code=0)


_TICKER_LIST_RE = re.compile(r"^[A-Z]{1,5}(?:,[A-Z]{1,5})*$")


def _parse_symbols_arg(raw: str) -> list[str]:
    """Strict comma-separated UPPER ticker parser.

    Mirrors the validation done by the UI command queue, so a hostile
    operator cannot smuggle shell metacharacters via this CLI.
    """
    s = (raw or "").strip().upper()
    if not s:
        raise typer.BadParameter("symbols must be a non-empty comma-separated list")
    if not _TICKER_LIST_RE.match(s):
        raise typer.BadParameter(
            "symbols must match ^[A-Z]{1,5}(,[A-Z]{1,5})*$ (e.g. AAPL,TSLA,NVDA)"
        )
    return s.split(",")


@app.command("ibkr-news-fetch")
def ibkr_news_fetch_cmd(
    symbols: str = typer.Option(
        ...,
        "--symbols",
        help="Comma-separated UPPER tickers, e.g. AAPL,TSLA,NVDA",
    ),
    limit: int = typer.Option(
        50, "--limit", min=1, max=200, help="Max headlines per symbol (1-200)."
    ),
) -> None:
    """Fetch IBKR historical news for ``symbols`` and cache the result.

    Writes ``data/research/cache/ibkr_news/YYYY-MM-DD-news.json``. Never
    places orders. Connects to IBKR only for the duration of this call.
    """
    from .research_providers.ibkr_news_provider import (
        connect_for_news,
        fetch_ibkr_news,
        write_news_cache,
    )

    cfg, journal = _bootstrap()
    syms = _parse_symbols_arg(symbols)

    client = None
    try:
        try:
            client = connect_for_news(cfg)
        except (IBKRClientError, LiveTradingBlocked) as exc:
            console.print(
                f"[yellow]ibkr-news-fetch: connect failed ({exc!r}); "
                "writing empty cache for the day.[/yellow]"
            )
            journal.record_event(
                category="research_news",
                level="WARNING",
                message="ibkr-news-fetch connect failed",
                payload={"error": repr(exc), "symbols": syms},
            )
            from .research_providers.ibkr_news_provider import (
                IBKRNewsProviderStatus,
            )

            status = IBKRNewsProviderStatus(
                ibkr_news_available=False,
                providers_detected=[],
                missing_entitlements=[],
                notes=[f"connect failed: {exc!r}"],
                checked_at_utc=datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            )
            cache_path = write_news_cache(cfg, catalysts=[], status=status)
            console.print(f"[green]Cache written:[/green] {cache_path}")
            raise typer.Exit(code=2)

        catalysts, status = fetch_ibkr_news(
            cfg, symbols=syms, client=client, limit_per_symbol=limit
        )
        cache_path = write_news_cache(cfg, catalysts=catalysts, status=status)
        console.print(
            Panel.fit(
                f"providers={status.providers_detected or '[]'}\n"
                f"available={status.ibkr_news_available}\n"
                f"symbols={','.join(syms)}\n"
                f"headlines={len(catalysts)}\n"
                f"cache={cache_path}",
                title="ibkr-news-fetch",
                style="cyan" if status.ibkr_news_available else "yellow",
            )
        )
        journal.record_event(
            category="research_news",
            level="INFO",
            message="ibkr-news-fetch",
            payload={
                "symbols": syms,
                "limit": limit,
                "headline_count": len(catalysts),
                "available": status.ibkr_news_available,
                "providers": status.providers_detected,
                "cache": str(cache_path),
            },
        )
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
    raise typer.Exit(code=0)


def _build_research_report(
    cfg: AppConfig,
    *,
    use_ibkr: bool = True,
):
    """Pure function: assemble a ResearchReport from cached/local state.

    Returns the freshly-built :class:`ResearchReport`. Does not write to
    disk and does not send Telegram. The CLI handler does both.
    """
    from .research_intelligence import (
        ResearchReport,
        aggregate_symbol_profiles,
        build_instruction,
        classify_news_catalysts,
        detect_themes,
    )
    from .research_providers.ibkr_news_provider import (
        IBKRNewsProviderStatus,
        connect_for_news,
        fetch_ibkr_news,
        get_provider_status,
        read_latest_news_cache,
        write_news_cache,
    )
    from .research_providers.manual_macro_calendar import load_macro_calendar

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    notes: list[str] = []

    market_regime = _latest_market_regime(cfg)
    if not market_regime:
        notes.append("no data/market_regime/*.json found; market_regime=unknown")

    cal = load_macro_calendar(cfg)
    macro_for_today = cal.for_today_et()
    macro_events = [m.to_research_event() for m in macro_for_today]
    notes.extend(cal.notes)

    watchlist_symbols, wl_path = _latest_dynamic_watchlist_symbols(cfg)
    if not watchlist_symbols:
        notes.append(
            "no data/watchlists/*-dynamic-watchlist.json found; "
            "skipping symbol-specific news fetch"
        )

    smc_summary = _latest_smc_summary(cfg)

    # IBKR news: try connecting only if asked AND watchlist is non-empty.
    catalysts = []
    status = IBKRNewsProviderStatus(
        ibkr_news_available=False,
        providers_detected=[],
        missing_entitlements=[],
        notes=["IBKR news fetch skipped"],
    )
    if use_ibkr and watchlist_symbols:
        client = None
        try:
            try:
                client = connect_for_news(cfg)
            except (IBKRClientError, LiveTradingBlocked) as exc:
                notes.append(f"IBKR connect failed: {exc!r}")
                client = None
            if client is not None:
                catalysts, status = fetch_ibkr_news(
                    cfg,
                    symbols=watchlist_symbols[:30],
                    client=client,
                    limit_per_symbol=20,
                )
                write_news_cache(cfg, catalysts=catalysts, status=status)
        finally:
            if client is not None:
                try:
                    client.disconnect()
                except Exception:  # noqa: BLE001
                    pass

    # If we couldn't fetch fresh news, fall back to today's cache.
    if not catalysts:
        cached = read_latest_news_cache(cfg)
        if cached and isinstance(cached.get("catalysts"), list):
            from .research_intelligence import NewsCatalyst  # noqa: PLC0415

            for row in cached["catalysts"]:
                if not isinstance(row, dict):
                    continue
                catalysts.append(
                    NewsCatalyst(
                        timestamp=str(row.get("timestamp") or ""),
                        provider=str(row.get("provider") or ""),
                        article_id=str(row.get("article_id") or ""),
                        symbol=(str(row.get("symbol")) if row.get("symbol") else None),
                        headline=str(row.get("headline") or ""),
                    )
                )
            notes.append("used cached IBKR news (no fresh fetch)")
            cached_status = cached.get("provider_status") or {}
            if isinstance(cached_status, dict):
                status = IBKRNewsProviderStatus(
                    ibkr_news_available=bool(
                        cached_status.get("ibkr_news_available")
                    ),
                    providers_detected=list(
                        cached_status.get("providers_detected") or []
                    ),
                    missing_entitlements=list(
                        cached_status.get("missing_entitlements") or []
                    ),
                    notes=list(cached_status.get("notes") or []),
                    checked_at_utc=str(cached_status.get("checked_at_utc") or ""),
                )

    classified_news = classify_news_catalysts(catalysts)
    classified_all = list(macro_events) + list(classified_news)
    themes = detect_themes(
        classified_events=classified_all,
        watchlist_symbols=watchlist_symbols,
    )
    profiles = aggregate_symbol_profiles(
        classified_events=classified_all,
        themes=themes,
        watchlist_symbols=watchlist_symbols,
    )
    instruction = build_instruction(
        date=today,
        market_regime=market_regime,
        macro_events=macro_events,
        ibkr_news_provider_status=status.to_dict(),
        symbol_profiles=profiles,
        watchlist_symbols=watchlist_symbols,
        smc_summary=smc_summary,
        extra_notes=notes,
    )

    if wl_path:
        notes.append(f"watchlist source: {wl_path}")

    return ResearchReport(
        date=today,
        generated_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        market_regime=market_regime,
        macro_events=macro_events,
        ibkr_news=classified_news,
        earnings=[],
        analyst_ratings=[],
        themes=themes,
        symbol_profiles=profiles,
        watchlist_today=watchlist_symbols,
        smc_summary=smc_summary,
        ibkr_news_provider_status=status.to_dict(),
        instruction=instruction,
        notes=notes,
        paper_only=True,
        block_live_trading=True,
    )


@app.command("research-report")
def research_report_cmd(
    telegram: bool = typer.Option(
        False,
        "--telegram",
        help="Also send a short Chinese digest via Telegram (graceful if creds missing).",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Send the full Markdown report via Telegram (split into Part i/N).",
    ),
    use_ibkr: bool = typer.Option(
        True,
        "--ibkr/--no-ibkr",
        help="Fetch fresh IBKR news (default). Use --no-ibkr to skip the connect.",
    ),
    email: bool = typer.Option(
        False,
        "--email",
        help="Send digest email via Resend if configured (no crash if missing).",
    ),
) -> None:
    """Build the v2 Research Report (JSON + instructions JSON + Markdown).

    Never places orders, never enables live trading. IBKR connection is
    opened only when ``--ibkr`` is on AND the watchlist is non-empty.
    """
    from .news_report_zh import split_for_telegram
    from .notifications.telegram import send_telegram_message
    from .research_intelligence import (
        render_markdown_report,
        render_telegram_digest,
        write_research_artifacts,
    )

    cfg, journal = _bootstrap()

    report = _build_research_report(cfg, use_ibkr=use_ibkr)

    paths = write_research_artifacts(
        report,
        research_dir=cfg.absolute("data/research"),
        memory_path=cfg.absolute("memory/RESEARCH-REPORT.md"),
    )

    summary = (
        f"date={report.date}\n"
        f"watchlist={len(report.watchlist_today)}\n"
        f"macro_events={len(report.macro_events)}\n"
        f"ibkr_news_items={len(report.ibkr_news)}\n"
        f"themes={len(report.themes)}\n"
        f"priority={len(report.instruction.priority_watchlist)}\n"
        f"blocked={len(report.instruction.blocked_symbols)}\n"
        f"manual_review={len(report.instruction.manual_review_symbols)}\n"
        f"soft_flagged={len(report.instruction.soft_flag_symbols)}\n"
        f"auto_paper_allowed={report.instruction.auto_paper_allowed}\n"
        f"paper_only={report.paper_only}\n"
        f"report_json={paths['report_json']}\n"
        f"instruction_json={paths['instruction_json']}\n"
        f"markdown={paths['markdown']}"
    )
    console.print(
        Panel.fit(summary, title="research-report", style="cyan")
    )
    journal.record_event(
        category="research_report",
        level="INFO",
        message="research-report generated",
        payload={
            "date": report.date,
            "paths": paths,
            "ibkr_news_available": bool(
                report.ibkr_news_provider_status.get("ibkr_news_available")
            ),
            "headline_count": len(report.ibkr_news),
            "auto_paper_allowed": report.instruction.auto_paper_allowed,
        },
    )

    if telegram or full:
        text = (
            render_markdown_report(report)
            if full
            else render_telegram_digest(report)
        )
        limit = int(
            (cfg.telegram_cfg or {}).get("max_message_length", 3500) or 3500
        )
        parts = split_for_telegram(text, limit=limit)
        for part in parts:
            send_telegram_message(part, cfg=cfg, journal=journal)
        console.print(
            f"[cyan]Telegram parts queued: {len(parts)} "
            f"(limit={limit}, full={full})[/cyan]"
        )

    if email or cfg.settings.reports.email_enabled:
        try:
            from .reports.report_email import send_report_email  # noqa: PLC0415
            from .reports.report_email_status import (  # noqa: PLC0415
                record_email_outcome,
            )

            rbody = "Strategy Lab — Research\n\n" + (
                render_telegram_digest(report)[:20_000]
            )
            outr = send_report_email(
                to_cfg=cfg.settings.reports.email_to,
                subject=f"[Strategy Lab] Research {report.date}",
                text_body=rbody,
            )
            record_email_outcome(
                cfg.project_root,
                "research",
                status=outr.status,
                to_addr=cfg.settings.reports.email_to,
                report_key=str(report.date),
                detail=outr.detail,
            )
            console.print(
                f"[cyan]email:[/cyan] {outr.status} ({outr.detail[:80]})"
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            console.print(f"[yellow]email error (non-fatal): {exc}[/yellow]")

    console.print(
        "[dim]execution_allowed=false. This CLI never places orders "
        "and never modifies broker state.[/dim]"
    )
    raise typer.Exit(code=0)


@app.command("research-status")
def research_status_cmd() -> None:
    """Show the freshness/health of the latest research report."""
    cfg, _journal = _bootstrap()
    research_dir = cfg.absolute("data/research")
    report_files = (
        sorted(research_dir.glob("*-research-report.json"))
        if research_dir.exists()
        else []
    )
    inst_files = (
        sorted(research_dir.glob("*-research-instructions.json"))
        if research_dir.exists()
        else []
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    status_payload: dict[str, object] = {
        "today_utc": today,
        "research_dir": str(research_dir),
        "report_files": [str(p) for p in report_files[-5:]],
        "instruction_files": [str(p) for p in inst_files[-5:]],
    }

    if report_files:
        latest = report_files[-1]
        status_payload["latest_report"] = str(latest)
        try:
            with latest.open("r", encoding="utf-8") as f:
                data = json.load(f)
            report_date = str(data.get("date") or "")
            status_payload["latest_date"] = report_date
            status_payload["stale"] = report_date != today
            status_payload["paper_only"] = bool(data.get("paper_only", True))
            ip = data.get("instruction") or {}
            status_payload["auto_paper_allowed"] = bool(
                ip.get("auto_paper_allowed", False)
            )
            status_payload["priority_count"] = len(ip.get("priority_watchlist") or [])
            status_payload["blocked_count"] = len(ip.get("blocked_symbols") or [])
        except (OSError, json.JSONDecodeError) as exc:
            status_payload["error"] = f"could not parse latest report: {exc!r}"
    else:
        status_payload["latest_report"] = None
        status_payload["stale"] = True
        status_payload["notes"] = [
            "No research report yet. Run `python -m bot.cli research-report` first."
        ]

    console.print(
        Panel.fit(
            json.dumps(status_payload, ensure_ascii=False, indent=2, default=str),
            title="research-status",
            style="cyan" if not status_payload.get("stale") else "yellow",
        )
    )
    raise typer.Exit(code=0)


@app.command("research-instructions")
def research_instructions_cmd(
    latest: bool = typer.Option(
        True, "--latest/--all", help="Show the latest instruction packet (default) or all."
    ),
) -> None:
    """Print the machine-readable research instruction JSON."""
    cfg, _journal = _bootstrap()
    research_dir = cfg.absolute("data/research")
    files = (
        sorted(research_dir.glob("*-research-instructions.json"))
        if research_dir.exists()
        else []
    )
    if not files:
        console.print(
            "[yellow]No research instructions found. Run "
            "`python -m bot.cli research-report` first.[/yellow]"
        )
        raise typer.Exit(code=2)

    targets = files[-1:] if latest else files
    for path in targets:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            console.print(f"[red]could not read {path}: {exc!r}[/red]")
            continue
        console.print(
            Panel.fit(
                json.dumps(data, ensure_ascii=False, indent=2, default=str),
                title=f"research-instructions: {path.name}",
                style="cyan",
            )
        )
    raise typer.Exit(code=0)


# ---------------------------------------------------------------------------
# Strategy Registry / Multi-Strategy Engine (Prompt 13C)
# ---------------------------------------------------------------------------
# These commands are intentionally research-only. They never call
# ``broker.place_order`` and never enable live trading. The
# ``MultiStrategyEngine`` enforces the ``execution_allowed=False``
# invariant from inside :mod:`bot.strategies.engine`.

_STRATEGY_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


def _strategies_resolve_dir(cfg: AppConfig) -> Path:
    out = cfg.absolute("data/strategies")
    out.mkdir(parents=True, exist_ok=True)
    return out


def _strategies_load_runtime(cfg: AppConfig):  # type: ignore[no-untyped-def]
    """Locate ``config/strategies.yaml`` next to settings.yaml and load it."""
    from .strategies import load_strategies_config

    candidate = cfg.absolute("config/strategies.yaml")
    if candidate.exists():
        return load_strategies_config(candidate)
    return load_strategies_config(Path("config/strategies.yaml"))


def _build_strategy_context(
    cfg: AppConfig,
    journal: Journal,
    *,
    extras: dict | None = None,
):  # type: ignore[no-untyped-def]
    from .strategies import StrategyContext

    symbols, _ = _latest_dynamic_watchlist_symbols(cfg)
    regime = _latest_market_regime(cfg)
    return StrategyContext(
        cfg=cfg,
        journal=journal,
        symbols=tuple(symbols),
        market_regime=str(regime.get("market_regime") or "neutral"),
        regime_confidence=str(regime.get("regime_confidence") or "medium"),
        paper_only=True,
        paper_execution_allowed=False,
        extras=dict(extras or {}),
    )


@app.command("strategy-list")
def strategy_list_cmd(
    json_out: bool = typer.Option(
        False, "--json", help="Print machine-readable JSON instead of a table."
    ),
) -> None:
    """List every registered strategy with metadata + enable status.

    Reads :file:`config/strategies.yaml` for the per-strategy
    ``enabled`` flag. Never connects to IBKR.
    """
    from .strategies import default_registry

    runtime = _strategies_load_runtime(load_config())
    rows: list[dict] = []
    for meta in default_registry().list_metadata():
        entry = runtime.get(meta.key)
        rows.append(
            {
                **meta.to_dict(),
                "enabled": entry.enabled,
                "params": dict(entry.params),
            }
        )

    if json_out:
        console.print_json(
            data={
                "strategies": rows,
                "defaults": runtime.defaults.to_dict(),
                "source_path": runtime.source_path,
                "notes": runtime.notes,
            }
        )
        return

    table = Table(title="Strategy Registry", show_lines=False)
    table.add_column("Key", style="cyan")
    table.add_column("Name")
    table.add_column("Version")
    table.add_column("Horizon")
    table.add_column("Status")
    table.add_column("Enabled")
    table.add_column("Requires IBKR")
    for r in rows:
        table.add_row(
            str(r["key"]),
            str(r["name"]),
            str(r["version"]),
            str(r["horizon"]),
            str(r["status"]),
            "yes" if r["enabled"] else "no",
            "yes" if r["requires_ibkr"] else "no",
        )
    console.print(table)
    if runtime.notes:
        console.print(Panel.fit("\n".join(runtime.notes), title="notes", style="yellow"))
    raise typer.Exit(code=0)


@app.command("strategy-info")
def strategy_info_cmd(
    key: str = typer.Argument(..., help="Strategy key (e.g. mtf_smc)."),
) -> None:
    """Print full metadata + runtime config for one strategy."""
    if not _STRATEGY_KEY_RE.match(key):
        console.print(
            Panel.fit(
                f"Invalid strategy key {key!r}. Must match {_STRATEGY_KEY_RE.pattern}.",
                title="strategy-info",
                style="red",
            )
        )
        raise typer.Exit(code=2)
    from .strategies import default_registry

    reg = default_registry()
    if not reg.has(key):
        console.print(
            Panel.fit(
                f"Strategy {key!r} is not registered. Known: {reg.keys()}",
                title="strategy-info",
                style="red",
            )
        )
        raise typer.Exit(code=2)
    runtime = _strategies_load_runtime(load_config())
    entry = runtime.get(key)
    payload = {
        "metadata": reg.get(key).metadata.to_dict(),
        "runtime": entry.to_dict(),
        "defaults": runtime.defaults.to_dict(),
        "source_path": runtime.source_path,
    }
    console.print_json(data=payload)
    raise typer.Exit(code=0)


@app.command("strategy-status")
def strategy_status_cmd(
    json_out: bool = typer.Option(
        False, "--json", help="Print machine-readable JSON."
    ),
) -> None:
    """Report freshness of per-strategy scan files under data/strategies/."""
    from .strategies import default_registry

    cfg = load_config()
    out_dir = _strategies_resolve_dir(cfg)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    multi_path: Path | None = None
    multi_files = sorted(out_dir.glob("*-multi-strategy-scan.json"))
    if multi_files:
        multi_path = multi_files[-1]

    per_strategy: dict[str, dict] = {}
    for meta in default_registry().list_metadata():
        scans = sorted(out_dir.glob(f"*-{meta.key}-scan.json"))
        latest = scans[-1] if scans else None
        per_strategy[meta.key] = {
            "latest_path": str(latest) if latest else None,
            "latest_date": latest.name.split("-" + meta.key + "-scan.json")[0] if latest else None,
            "stale": (latest is None)
            or (latest.name.split("-" + meta.key + "-scan.json")[0] != today),
            "status": meta.status,
        }

    # Surface the latest intraday backtest summary alongside scan
    # freshness — so ``strategy-status`` is the one place to see
    # whether the strategy has both a recent scan AND a recent backtest.
    from .backtests import REPORT_DIRNAME  # noqa: PLC0415

    backtest_dir = Path(cfg.absolute(REPORT_DIRNAME))
    latest_bt: Path | None = None
    if backtest_dir.exists():
        candidates = sorted(backtest_dir.glob("*-backtest-summary.json"))
        latest_bt = candidates[-1] if candidates else None
    bt_payload: dict[str, object] | None = None
    if latest_bt is not None:
        try:
            bt_data = json.loads(latest_bt.read_text(encoding="utf-8"))
            cfg_block = bt_data.get("config") or {}
            metrics_block = bt_data.get("metrics") or {}
            bt_payload = {
                "summary_path": str(latest_bt),
                "strategy_id": bt_data.get("strategy_id") or "ict_smc_intraday_v1",
                "start": cfg_block.get("start"),
                "end": cfg_block.get("end"),
                "mode": cfg_block.get("mode"),
                "direction": cfg_block.get("direction"),
                "symbols": cfg_block.get("symbols") or [],
                "total_filled_trades": metrics_block.get("total_filled_trades"),
                "total_signals": metrics_block.get("total_signals"),
                "win_rate": metrics_block.get("win_rate"),
                "average_r": metrics_block.get("average_r"),
                "total_r": metrics_block.get("total_r"),
                "max_drawdown_r": metrics_block.get("max_drawdown_r"),
                "profit_factor": metrics_block.get("profit_factor"),
                "finished_at_utc": bt_data.get("finished_at_utc"),
                "paper_only": bt_data.get("paper_only", True),
                "execution_allowed": bt_data.get("execution_allowed", False),
            }
        except (OSError, ValueError, json.JSONDecodeError):
            bt_payload = {"summary_path": str(latest_bt), "error": "failed_to_parse"}

    if "ict_smc_intraday_v1" in per_strategy:
        per_strategy["ict_smc_intraday_v1"]["backtest"] = bt_payload or {
            "summary_path": None,
            "note": "no backtest run yet (use 'backtest-intraday-smc' or '/backtest' UI).",
        }
        per_strategy["ict_smc_intraday_v1"]["modes"] = {
            "research": "active",
            "backtest": "active" if bt_payload else "ready",
            "paper": "planned_disabled",
        }

    payload = {
        "today_utc": today,
        "data_dir": str(out_dir),
        "multi_strategy_scan_path": str(multi_path) if multi_path else None,
        "multi_strategy_scan_stale": (multi_path is None)
        or (multi_path.name.split("-multi-strategy-scan.json")[0] != today),
        "per_strategy": per_strategy,
        "latest_backtest": bt_payload,
        "paper_only": True,
        "execution_allowed": False,
    }
    if json_out:
        console.print_json(data=payload)
    else:
        console.print(
            Panel.fit(
                json.dumps(payload, indent=2, ensure_ascii=False),
                title="strategy-status",
                style="cyan",
            )
        )
    raise typer.Exit(code=0)


@app.command("strategy-select")
def strategy_select_cmd(
    area: str = typer.Option(
        ...,
        "--area",
        help="scan | backtest | edge | paper",
    ),
    strategy: str = typer.Option(
        ...,
        "--strategy",
        help="Strategy id (e.g. ict_smc_intraday_v1).",
    ),
) -> None:
    """Update local UI selection file (data/runtime/selected_strategy.json). No IBKR, no orders."""
    from .strategy_ui import (  # noqa: PLC0415
        load_strategy_selection,
        load_strategy_ui_catalog,
        save_strategy_selection,
        selection_from_mapping,
        validate_per_area,
    )

    cfg = load_config()
    root = cfg.project_root
    cat = load_strategy_ui_catalog(root)
    cur = load_strategy_selection(root, catalog=cat)
    ok, err = validate_per_area(cat, area, strategy)
    if not ok:
        console.print(Panel.fit(err, title="strategy-select", style="red"))
        raise typer.Exit(code=2)
    a = (area or "").strip().lower()
    patch: dict[str, str] = {}
    if a == "scan":
        patch["active_scan_strategy"] = strategy
    elif a in {"backtest", "bt"}:
        patch["active_backtest_strategy"] = strategy
    elif a in {"edge", "edge_profile"}:
        patch["active_edge_strategy"] = strategy
    elif a == "paper":
        patch["active_paper_strategy"] = strategy
    else:
        console.print(
            Panel.fit(
                f"Unknown --area {area!r} (use scan, backtest, edge, or paper).",
                title="strategy-select",
                style="red",
            )
        )
        raise typer.Exit(code=2)
    st, _ = selection_from_mapping(cat, patch, current=cur)
    path = save_strategy_selection(root, st, catalog=cat)
    out = {**st.to_dict(), "path": str(path), "warnings": st.last_warnings}
    console.print_json(data=out)
    raise typer.Exit(code=0)


@app.command("strategy-selection-status")
def strategy_selection_status_cmd(
    json_out: bool = typer.Option(
        True,
        "--json/--no-json",
        help="Print JSON (default: on).",
    ),
) -> None:
    """Show merged strategy selection from data/runtime/selected_strategy.json (read-only; no TWS)."""
    from .strategy_ui import load_strategy_selection, load_strategy_ui_catalog  # noqa: PLC0415

    cfg = load_config()
    root = cfg.project_root
    cat = load_strategy_ui_catalog(root)
    st = load_strategy_selection(root, catalog=cat)
    payload = {
        "file": str(root / "data/runtime/selected_strategy.json"),
        "selection": st.to_dict(),
        "warnings": st.last_warnings,
        "catalog": cat.source_path,
    }
    if json_out:
        console.print_json(data=payload)
    else:
        console.print(
            Panel.fit(
                json.dumps(payload, indent=2, ensure_ascii=False),
                title="strategy-selection-status",
                style="cyan",
            )
        )
    raise typer.Exit(code=0)


@app.command("strategy-scan")
def strategy_scan_cmd(
    strategy: str = typer.Option(
        ..., "--strategy", help="Strategy key to scan (e.g. mtf_smc)."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Also dump the result JSON to stdout."
    ),
) -> None:
    """Run a single strategy scan via the engine. Research-only.

    Forces ``include_disabled=True`` for the chosen key, so disabled
    strategies (and stubs) can still be exercised explicitly.
    """
    if not _STRATEGY_KEY_RE.match(strategy):
        console.print(
            Panel.fit(
                f"Invalid --strategy {strategy!r}.",
                title="strategy-scan",
                style="red",
            )
        )
        raise typer.Exit(code=2)
    from .strategies import (
        MultiStrategyEngine,
        default_registry,
        write_single_scan,
    )

    cfg, journal = _bootstrap()
    if not default_registry().has(strategy):
        console.print(
            Panel.fit(
                f"Strategy {strategy!r} not registered. Known: {default_registry().keys()}",
                title="strategy-scan",
                style="red",
            )
        )
        raise typer.Exit(code=2)

    runtime = _strategies_load_runtime(cfg)
    engine = MultiStrategyEngine(runtime_config=runtime)
    ctx = _build_strategy_context(cfg, journal)
    summary = engine.run(ctx, only=(strategy,), include_disabled=True)
    if not summary.results:
        console.print(
            Panel.fit(
                f"No scan executed for {strategy!r} (skipped: {summary.skipped_keys}).",
                title="strategy-scan",
                style="yellow",
            )
        )
        raise typer.Exit(code=0)
    result = summary.results[0]
    out_dir = _strategies_resolve_dir(cfg)
    path = write_single_scan(result, output_dir=out_dir)
    journal.record_event(
        category="strategy",
        level="INFO",
        message="strategy-scan",
        payload={
            "strategy": strategy,
            "status": result.status,
            "symbol_count": result.symbol_count,
            "signal_count": result.signal_count,
            "path": str(path),
        },
    )
    if json_out:
        console.print_json(data=result.to_dict())
    else:
        console.print(
            Panel.fit(
                f"strategy={strategy} status={result.status} "
                f"symbols={result.symbol_count} signals={result.signal_count}\n"
                f"saved={path}\n"
                f"notes={result.notes or '[]'}",
                title="strategy-scan",
                style="cyan" if result.status in {"ok", "not_implemented"} else "red",
            )
        )
    raise typer.Exit(code=0)


@app.command("multi-strategy-scan")
def multi_strategy_scan_cmd(
    include_disabled: bool = typer.Option(
        False,
        "--include-disabled",
        help="Also run disabled / not_implemented strategies.",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Print machine-readable JSON to stdout."
    ),
) -> None:
    """Run every enabled strategy via the engine and write a snapshot.

    Writes ``data/strategies/<YYYY-MM-DD>-multi-strategy-scan.json``.
    Research-only: never places orders, never enables live trading.
    """
    from .strategies import MultiStrategyEngine, render_summary_zh, write_run_summary

    cfg, journal = _bootstrap()
    runtime = _strategies_load_runtime(cfg)
    engine = MultiStrategyEngine(runtime_config=runtime)
    ctx = _build_strategy_context(cfg, journal)
    summary = engine.run(ctx, include_disabled=include_disabled)
    out_dir = _strategies_resolve_dir(cfg)
    path = write_run_summary(summary, output_dir=out_dir)
    journal.record_event(
        category="strategy",
        level="INFO",
        message="multi-strategy-scan",
        payload={
            "enabled_keys": summary.enabled_keys,
            "skipped_keys": summary.skipped_keys,
            "total_signals": summary.total_signals,
            "path": str(path),
        },
    )
    if json_out:
        # Machine-readable only: one JSON object on stdout (no Rich panels).
        sys.stdout.write(json.dumps(summary.to_dict(), default=str) + "\n")
    else:
        console.print(
            Panel.fit(
                render_summary_zh(summary) + f"\n\n  saved={path}",
                title="multi-strategy-scan",
                style="cyan",
            )
        )
    raise typer.Exit(code=0)


@app.command("premarket-brief")
def premarket_brief_cmd(
    report_date: Optional[str] = typer.Option(
        None,
        "--date",
        help="Trading day YYYY-MM-DD (US/Eastern calendar context).",
    ),
    today: bool = typer.Option(
        False,
        "--today",
        help="Use today's calendar date in America/New_York (same as default when no --date).",
    ),
    latest: bool = typer.Option(
        False,
        "--latest",
        help="Print the most recent saved brief JSON on disk.",
    ),
    email: bool = typer.Option(
        False,
        "--email",
        help="Send by email (Resend) if RESEND_API_KEY is set; never crashes if missing.",
    ),
    telegram: bool = typer.Option(
        False,
        "--telegram",
        help="Reserved for future Telegram digest (v1: prints a note only).",
    ),
) -> None:
    """Human pre-market brief: macro (manual YAML) + optional news APIs. Never trades."""
    from datetime import date as date_cls
    from datetime import datetime as dt_mod
    from zoneinfo import ZoneInfo

    from .premarket.brief import build_premarket_brief
    from .premarket.storage import find_latest_premarket_brief

    cfg, _journal = _bootstrap()
    root = cfg.project_root

    if latest:
        j = find_latest_premarket_brief(root)
        if not j:
            console.print(
                Panel.fit(
                    "No pre-market brief JSON under data/premarket_briefs/ yet.",
                    title="premarket-brief --latest",
                )
            )
            raise typer.Exit(0)
        p = j.get("date_ny", "")
        console.print(
            Panel.fit(
                json.dumps(j, ensure_ascii=False, indent=2)[:12000],
                title=f"premarket-brief latest ({p})",
            )
        )
        raise typer.Exit(0)

    ny = ZoneInfo("America/New_York")
    if report_date:
        day = date_cls.fromisoformat(report_date)
    elif today:
        day = dt_mod.now(ny).date()
    else:
        # Default: "today" in New York (avoids off-by-one vs UTC)
        day = dt_mod.now(ny).date()

    data = build_premarket_brief(
        cfg, trading_day=day, email=email, email_to=cfg.settings.reports.email_to
    )
    if telegram:
        console.print(
            "[dim]telegram: optional digest not wired in this version — use research-report --telegram[/dim]"
        )
    lines = [
        f"date_ny={data.date_ny}",
        f"market_tone={data.market_tone}",
        "",
        "summary:",
        *[f"  - {s}" for s in data.summary_lines],
        "",
        "providers:",
        *[f"  {k}: {v}" for k, v in sorted(data.provider_status.items())],
    ]
    console.print(Panel.fit("\n".join(lines), title="premarket-brief"))
    raise typer.Exit(0)


# Backwards-compatible alias used by tests and shell scripts.
send_telegram_message = send_telegram_message  # noqa: PLW0127 - explicit re-export


if __name__ == "__main__":  # pragma: no cover
    app()
