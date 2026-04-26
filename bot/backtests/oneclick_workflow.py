"""One-click: local coverage → optional IBKR 1m fetch → intraday backtest (Prompt 13BT-ONECLICK).

This module may import :mod:`bot.ibkr_client` **only** for read-only historical
candles (same contract as ``fetch-candles``). It must **not** import
:mod:`bot.broker` or any order-placement / execution-surface code.

Backtest uses :func:`bot.backtests.intraday_engine.backtest_intraday_smc` only
(cache files; no orders, no live trading).
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bot.config import AppConfig

# --- runtime snapshot for Strategy Lab (gitignored under data/runtime/) ---
_LAST_ONECLICK_NAME = "last_backtest_oneclick.json"


def _write_last_oneclick(project_root: Path, payload: dict[str, Any]) -> Path | None:
    out = project_root / "data" / "runtime" / _LAST_ONECLICK_NAME
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return out
    except OSError:
        return None


def _parse_skipped_from_notes(notes: list[str]) -> list[str]:
    out: list[str] = []
    for n in notes:
        if ": no cached 1min candles" in n:
            sym = n.split(":", 1)[0].strip()
            if sym and sym not in out:
                out.append(sym)
    return out


def _fetch_1m_from_ibkr(
    cfg: AppConfig,
    project_root: Path,
    symbol: str,
    start: str,
    end: str,
    *,
    use_rth: bool,
    force: bool,
) -> dict[str, Any]:
    """Read-only historical request + write CSV; mirrors ``fetch-candles`` logic."""
    from bot.backtests.candle_cache import CandleCacheError, save_candles_csv
    from bot.ibkr_client import IBKRClient, IBKRClientError, LiveTradingBlocked
    from bot.smc_timeframes import resolve_timeframe_spec

    tf = "1min"
    sym = symbol.strip().upper()
    out: dict[str, Any] = {
        "symbol": sym,
        "ok": False,
        "error": None,
        "tws_unavailable": False,
        "days_written": 0,
        "rows_written": 0,
    }
    spec = resolve_timeframe_spec(tf, cfg)
    try:
        d0 = datetime.strptime(start, "%Y-%m-%d")
        d1 = datetime.strptime(end, "%Y-%m-%d")
    except ValueError as exc:
        out["error"] = str(exc)
        return out
    days = max((d1 - d0).days + 2, 2)
    duration = f"{days} D"
    bar_size = str(spec.bar_size)
    use_rth_flag = bool(use_rth)
    wts = str(spec.what_to_show or "TRADES")
    client: IBKRClient | None = None
    try:
        client = IBKRClient(cfg)
        try:
            client.connect(readonly=True, timeout=12.0)
        except LiveTradingBlocked as exc:
            out["error"] = str(exc)
            return out
        except (IBKRClientError, OSError, ConnectionError) as exc:
            out["tws_unavailable"] = True
            out["error"] = f"could not connect to TWS/IB Gateway: {exc}"
            return out
        except Exception as exc:  # noqa: BLE001
            out["tws_unavailable"] = True
            out["error"] = str(exc)
            return out
        try:
            bars = client.get_intraday_bars(
                sym,
                duration=duration,
                bar_size=bar_size,
                what_to_show=wts,
                use_rth=use_rth_flag,
            ) or []
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"get_intraday_bars: {exc}"
            return out
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
    try:
        stats = save_candles_csv(
            project_root=project_root,
            symbol=sym,
            timeframe=tf,
            bars=bars,
            start=start,
            end=end,
            force=force,
        )
    except CandleCacheError as exc:
        out["error"] = str(exc)
        return out
    out["ok"] = True
    out["days_written"] = int(stats.days_written)
    out["rows_written"] = int(stats.rows_written)
    out["gaps"] = list(stats.gaps) if stats.gaps else []
    return out


def _validate_strategy_for_backtest(project_root: Path, strategy: str) -> str | None:
    from bot.strategy_ui.catalog import load_strategy_ui_catalog

    cat = load_strategy_ui_catalog(project_root)
    e = cat.strategies.get(strategy)
    if e is None:
        return f"unknown strategy {strategy!r}; known: {', '.join(sorted(cat.strategies))}"
    if not e.backtest_enabled:
        return f"strategy {strategy!r} is not enabled for backtest in strategy_ui catalog"
    if strategy != "ict_smc_intraday_v1":
        return "one-click backtest only supports ict_smc_intraday_v1 in this build"
    return None


def run_backtest_oneclick(
    project_root: Path,
    cfg: AppConfig,
    *,
    symbols: list[str],
    start: str,
    end: str,
    source: str,
    strategy: str = "ict_smc_intraday_v1",
    mode: str = "strict_and_aggressive",
    direction: str = "both",
    rth_only: bool = True,
    chart: bool = False,
    allow_partial: bool = False,
    timeframe: str = "1min",
    use_rth_for_fetch: bool = True,
    force_fetch: bool = False,
    fetch_pacing_seconds: float = 0.5,
) -> dict[str, Any]:
    """Run coverage → optional per-symbol fetch → backtest. Returns a JSON-serialisable report."""
    from bot.backtests.candle_coverage import check_candle_coverage
    from bot.backtests import BacktestConfig, backtest_intraday_smc, save_backtest_artifacts
    from bot.journal import Journal
    from bot.strategies.ict_smc_intraday import IntradayRiskConfig

    root = project_root.resolve()
    warnings: list[str] = []
    err = _validate_strategy_for_backtest(root, strategy)
    if err:
        rep = {
            "error": err,
            "complete_result": False,
            "warnings": [err],
        }
        _write_last_oneclick(root, rep)
        return rep

    if (timeframe or "").lower() not in ("1min", "1m"):
        rep = {
            "error": "only 1min timeframe is supported for one-click",
            "complete_result": False,
            "warnings": ["only 1min timeframe is supported for one-click"],
        }
        _write_last_oneclick(root, rep)
        return rep

    syms = [s.strip().upper() for s in symbols if s and s.strip()]
    syms = list(dict.fromkeys(syms))  # stable unique

    report: dict[str, Any] = {
        "symbols_requested": list(syms),
        "source": source,
        "requested_start": start,
        "requested_end": end,
        "timeframe": "1min",
        "strategy": strategy,
        "mode": mode,
        "direction": direction,
        "rth_only": rth_only,
        "allow_partial": allow_partial,
        "fetch_failed_tws_unavailable": False,
        "notes": [
            "Weekday coverage uses Mon–Fri only; US holidays are not removed.",
            "backtest engine never places orders; fetch uses read-only IBKR history only.",
        ],
    }

    if not syms:
        report.update(
            {
                "error": "no symbols after normalisation",
                "complete_result": False,
                "warnings": ["no symbols"],
                "symbols_ready_before": [],
                "symbols_missing_before": [],
                "symbols_fetched": [],
                "symbols_failed_fetch": [],
                "symbols_ready_after": [],
                "symbols_still_missing": [],
                "backtest_symbols_run": [],
                "backtest_symbols_skipped": [],
                "backtest_summary_path": None,
                "report_path": None,
                "chart_paths": [],
            }
        )
        _write_last_oneclick(root, report)
        return report

    cov0 = check_candle_coverage(syms, start, end, timeframe="1min", project_root=root)
    ready0 = list(cov0.get("symbols_ready") or [])
    partial0 = list(cov0.get("symbols_partial") or [])
    missing0 = list(cov0.get("symbols_missing") or [])
    # "symbols missing before" = not fully ready (need fetch or still incomplete)
    not_ready0 = [s for s in syms if s not in ready0]

    report["coverage_before"] = cov0
    report["symbols_ready_before"] = ready0
    report["symbols_missing_before"] = not_ready0
    report["symbols_partial_before"] = partial0
    report["symbols_strict_missing_before"] = missing0

    need_fetch = not_ready0.copy()
    fetched_ok: list[str] = []
    failed_fetch: list[str] = []
    tws_down = False

    if not need_fetch:
        warnings.append("all symbols ready — skipping IBKR fetch")
    else:
        for i, sym in enumerate(need_fetch):
            if i > 0 and fetch_pacing_seconds > 0:
                time.sleep(fetch_pacing_seconds)
            fr = _fetch_1m_from_ibkr(
                cfg,
                root,
                sym,
                start,
                end,
                use_rth=use_rth_for_fetch,
                force=force_fetch,
            )
            if fr.get("tws_unavailable"):
                report["fetch_failed_tws_unavailable"] = True
                tws_down = True
                failed_fetch.append(sym)
                warnings.append("TWS/IB Gateway unavailable; remaining fetch attempts skipped")
                failed_fetch.extend(x for x in need_fetch[i + 1 :] if x not in failed_fetch)
                break
            if fr.get("ok"):
                fetched_ok.append(sym)
            else:
                failed_fetch.append(sym)
                warnings.append(
                    f"fetch failed for {sym}: {fr.get('error') or 'unknown'}"
                )

    cov1 = check_candle_coverage(syms, start, end, timeframe="1min", project_root=root)
    ready1 = list(cov1.get("symbols_ready") or [])
    still = [s for s in syms if s not in ready1]
    report["coverage_after"] = cov1
    report["symbols_fetched"] = fetched_ok
    report["symbols_failed_fetch"] = failed_fetch
    report["symbols_ready_after"] = ready1
    report["symbols_still_missing"] = still
    report["warnings"] = list(warnings)

    if still and not allow_partial:
        if tws_down:
            report["stopped_reason"] = "fetch_failed_tws_unavailable"
        elif need_fetch and failed_fetch:
            report["stopped_reason"] = "fetch_did_not_restore_full_coverage"
        else:
            report["stopped_reason"] = "incomplete_coverage"

    # Default: if incomplete and not allow_partial, do not run backtest
    if still and not allow_partial:
        report.update(
            {
                "backtest_ran": False,
                "complete_result": False,
                "backtest_symbols_run": [],
                "backtest_symbols_skipped": still,
                "backtest_summary_path": None,
                "report_path": None,
                "chart_paths": [],
                "backtest_metrics": None,
            }
        )
        if tws_down:
            report["warnings"].append(
                "Stopped before backtest: TWS offline and --allow-partial not set. "
                "Re-open TWS or run with --allow-partial to backtest on cached symbols only."
            )
        else:
            report["warnings"].append(
                "Stopped before backtest: coverage still incomplete. "
                "Use --allow-partial to run on whatever cache exists, or fix fetch errors."
            )
        _write_last_oneclick(root, report)
        return report

    # Backtest (engine skips symbols with no cache)
    bcfg = BacktestConfig(
        symbols=tuple(syms),
        start=start,
        end=end,
        mode=mode,
        direction=direction,
        rth_only=rth_only,
        risk_cfg=IntradayRiskConfig(),
    )
    run = backtest_intraday_smc(root, bcfg)
    paths = save_backtest_artifacts(root, run, chart=chart)
    skipped = _parse_skipped_from_notes(list(run.notes or []))
    run_syms = [s for s in syms if s not in set(skipped)]

    journal = Journal(cfg)
    journal.record_event(
        category="backtest",
        level="INFO",
        message="backtest-oneclick",
        payload={
            "symbols": syms,
            "start": start,
            "end": end,
            "mode": mode,
            "direction": direction,
            "allow_partial": allow_partial,
            "fetched": fetched_ok,
            "failed_fetch": failed_fetch,
            "skipped": skipped,
            "execution_allowed": False,
            "paper_only": True,
        },
    )

    m = run.metrics
    report["backtest_ran"] = True
    report["backtest_symbols_run"] = run_syms
    report["backtest_symbols_skipped"] = skipped
    report["backtest_summary_path"] = str(paths.get("summary_json") or "")
    report["report_path"] = str(paths.get("report_md") or paths.get("summary_json") or "")
    report["chart_paths"] = sorted(
        {str(v) for v in paths.values() if isinstance(v, str) and v.endswith(".png")}
    )
    if chart and not report["chart_paths"]:
        report["warnings"].append("chart flag set but no chart paths returned (see logs)")
    report["backtest_metrics"] = m.to_dict()
    report["complete_result"] = (
        bool(cov1.get("will_backtest_be_complete"))
        and len(still) == 0
        and len(skipped) == 0
        and not tws_down
    )
    if skipped:
        report["complete_result"] = False
        report["warnings"].append(
            f"incomplete: skipped {len(skipped)} symbol(s) with no 1m cache: {', '.join(skipped)}"
        )
    if allow_partial and still and (len(still) > 0 or len(skipped) > 0):
        report["warnings"].append(
            "Partial run: --allow-partial was set; some symbols may lack full cache coverage."
        )
    _write_last_oneclick(root, report)
    return report


__all__ = ["run_backtest_oneclick"]
