"""Backtest report writer (Prompt 13E PART B + state-store reader).

Persists a :class:`BacktestRun` into ``data/backtests/intraday/`` as:

* ``YYYY-MM-DD-HHMMSS-backtest-summary.json`` — top-level metrics + cfg.
* ``YYYY-MM-DD-HHMMSS-backtest-trades.csv`` — one row per simulated trade.
* ``YYYY-MM-DD-HHMMSS-backtest-equity.csv`` — cumulative R after each
  filled trade.
* ``YYYY-MM-DD-HHMMSS-backtest-report.md`` — human-friendly Markdown
  digest used by ``backtest-report --latest`` and the UI.

Optional charts (``charts=True``):

* ``charts/<stamp>-equity-curve.png``
* ``charts/<stamp>-r-distribution.png``
* ``charts/<stamp>-trades-by-hour.png``

All outputs are runtime artifacts and gitignored. No broker import,
no IBKR connection. Matplotlib uses the non-interactive ``Agg`` backend.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .intraday_engine import BacktestRun

REPORT_DIRNAME = "data/backtests/intraday"
LOG = logging.getLogger(__name__)


def _stamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d-%H%M%S")


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def save_backtest_artifacts(
    project_root: Path,
    run: "BacktestRun",
    *,
    chart: bool = False,
    stamp: str | None = None,
) -> dict[str, str]:
    """Write summary JSON, trades CSV, equity CSV, and Markdown report.

    Returns a dict of written file paths keyed by artifact name.
    """
    out_dir = Path(project_root) / REPORT_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or _stamp()
    paths: dict[str, str] = {}

    summary_path = out_dir / f"{stamp}-backtest-summary.json"
    summary_path.write_text(
        json.dumps(run.to_dict(), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    paths["summary_json"] = str(summary_path)

    trades_path = out_dir / f"{stamp}-backtest-trades.csv"
    _write_trades_csv(trades_path, run)
    paths["trades_csv"] = str(trades_path)

    equity_path = out_dir / f"{stamp}-backtest-equity.csv"
    _write_equity_csv(equity_path, run)
    paths["equity_csv"] = str(equity_path)

    md_path = out_dir / f"{stamp}-backtest-report.md"
    md_path.write_text(_render_markdown(run, paths), encoding="utf-8")
    paths["report_md"] = str(md_path)

    if chart:
        try:
            chart_paths = _render_charts(out_dir / "charts", stamp, run)
            paths.update(chart_paths)
        except Exception as exc:  # noqa: BLE001
            LOG.info("backtest charts skipped (%s)", exc)
            paths["charts_error"] = repr(exc)

    return paths


def _write_trades_csv(path: Path, run: "BacktestRun") -> None:
    fields = (
        "trade_id",
        "symbol",
        "date",
        "strategy_id",
        "direction",
        "signal_category",
        "setup_type",
        "trigger_type",
        "entry_time",
        "entry_price",
        "stop_price",
        "target_price",
        "exit_time",
        "exit_price",
        "outcome",
        "pnl_r",
        "gross_pnl",
        "planned_rr",
        "mfe_r",
        "mae_r",
        "bars_held",
        "notes",
    )
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for t in run.trades:
            w.writerow(
                [
                    t.trade_id,
                    t.symbol,
                    t.date,
                    t.strategy_id,
                    t.direction,
                    t.signal_category,
                    t.setup_type,
                    t.trigger_type,
                    t.entry_time,
                    _safe_str(t.entry_price),
                    _safe_str(t.stop_price),
                    _safe_str(t.target_price),
                    t.exit_time,
                    _safe_str(t.exit_price),
                    t.outcome,
                    _safe_str(t.pnl_r),
                    _safe_str(t.gross_pnl),
                    _safe_str(t.planned_rr),
                    _safe_str(t.mfe_r),
                    _safe_str(t.mae_r),
                    _safe_str(t.bars_held),
                    "; ".join(t.notes or []),
                ]
            )


def _write_equity_csv(path: Path, run: "BacktestRun") -> None:
    fields = ("trade_index", "trade_id", "symbol", "date", "exit_time", "pnl_r", "cumulative_r")
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for row in run.equity_curve:
            w.writerow([row.get(k, "") for k in fields])


def _render_markdown(run: "BacktestRun", paths: dict[str, str]) -> str:
    cfg = run.cfg
    m = run.metrics
    lines: list[str] = []
    lines.append(f"# ICT/SMC Intraday Backtest — {cfg.start}..{cfg.end}")
    lines.append("")
    lines.append("PAPER ONLY · RESEARCH BACKTEST · `execution_allowed=false`")
    lines.append("")
    lines.append("## Configuration")
    lines.append(f"- Strategy: `{run.cfg.symbols and 'ict_smc_intraday_v1' or '-'}`")
    lines.append(f"- Symbols: `{', '.join(cfg.symbols) or '-'}`")
    lines.append(f"- Mode: `{cfg.mode}` · Direction: `{cfg.direction}` · RTH only: `{cfg.rth_only}`")
    lines.append(f"- Risk: min_rr_strict={cfg.risk_cfg.min_rr_strict}, "
                 f"min_rr_aggressive={cfg.risk_cfg.min_rr_aggressive}, "
                 f"max_stop_pct={cfg.risk_cfg.max_stop_distance_pct}")
    lines.append("")
    lines.append("## Top-line metrics")
    lines.append(f"- Total signals: **{m.total_signals}**")
    lines.append(f"- Filled trades: **{m.total_filled_trades}**")
    lines.append(f"- Not-filled (expired): **{m.total_not_filled}**")
    lines.append(f"- Win rate: **{_pct(m.win_rate)}**")
    lines.append(f"- Average R: **{_fmt(m.average_r)}**")
    lines.append(f"- Median R: **{_fmt(m.median_r)}**")
    lines.append(f"- Total R: **{_fmt(m.total_r)}**")
    lines.append(f"- Max drawdown R: **{_fmt(m.max_drawdown_r)}**")
    lines.append(f"- Profit factor: **{_fmt(m.profit_factor)}**")
    lines.append(f"- Avg bars held: **{_fmt(m.average_bars_held)}**")
    lines.append("")
    lines.append("## STRICT vs AGGRESSIVE")
    lines.append(f"- STRICT trades: {m.strict_count} · win rate {_pct(m.strict_win_rate)}")
    lines.append(f"- AGGRESSIVE trades: {m.aggressive_count} · win rate {_pct(m.aggressive_win_rate)}")
    lines.append(f"- LONG win rate: {_pct(m.long_win_rate)} · SHORT win rate: {_pct(m.short_win_rate)}")
    lines.append("")
    if m.by_symbol:
        lines.append("## By symbol")
        lines.append("")
        lines.append("| Symbol | Trades | Wins | Losses | Win rate | Avg R | Total R |")
        lines.append("|--------|-------:|-----:|-------:|---------:|------:|--------:|")
        for s in m.by_symbol:
            lines.append(
                f"| {s.symbol} | {s.trades} | {s.wins} | {s.losses} | "
                f"{_pct(s.win_rate)} | {_fmt(s.average_r)} | {_fmt(s.total_r)} |"
            )
        lines.append("")
    if m.by_hour:
        lines.append("## By hour (entry)")
        lines.append("")
        lines.append("| Hour (ET) | Trades | Win rate | Avg R | Total R |")
        lines.append("|-----------|-------:|---------:|------:|--------:|")
        for h, row in m.by_hour.items():
            lines.append(
                f"| {h} | {int(row.get('trades') or 0)} | "
                f"{_pct(row.get('win_rate'))} | {_fmt(row.get('average_r'))} | "
                f"{_fmt(row.get('total_r'))} |"
            )
        lines.append("")
    if m.by_weekday:
        lines.append("## By weekday")
        lines.append("")
        lines.append("| Weekday | Trades | Win rate | Avg R | Total R |")
        lines.append("|---------|-------:|---------:|------:|--------:|")
        for d, row in m.by_weekday.items():
            lines.append(
                f"| {d} | {int(row.get('trades') or 0)} | "
                f"{_pct(row.get('win_rate'))} | {_fmt(row.get('average_r'))} | "
                f"{_fmt(row.get('total_r'))} |"
            )
        lines.append("")
    if run.notes:
        lines.append("## Notes")
        for n in run.notes:
            lines.append(f"- {n}")
        lines.append("")
    lines.append("## Artifacts")
    for k in ("summary_json", "trades_csv", "equity_csv", "report_md",
              "equity_chart_png", "r_distribution_chart_png", "trades_by_hour_chart_png"):
        if k in paths:
            lines.append(f"- `{k}`: `{paths[k]}`")
    return "\n".join(lines) + "\n"


def _pct(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v*100:.1f}%"


def _fmt(v: float | None) -> str:
    if v is None:
        return "-"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{f:.3f}"


def _render_charts(charts_dir: Path, stamp: str, run: "BacktestRun") -> dict[str, str]:
    """Render optional matplotlib PNGs. Returns dict of paths."""
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt  # noqa: PLC0415

    charts_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    # Equity curve.
    if run.equity_curve:
        xs = [row["trade_index"] for row in run.equity_curve]
        ys = [row["cumulative_r"] for row in run.equity_curve]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(xs, ys, marker="o", linewidth=1.4)
        ax.axhline(0, color="grey", linewidth=0.8, linestyle="--")
        ax.set_title(f"Equity (cumulative R) — {run.cfg.start}..{run.cfg.end}")
        ax.set_xlabel("filled trade #")
        ax.set_ylabel("cumulative R")
        ax.grid(True, linewidth=0.3)
        fig.text(0.99, 0.01, "PAPER ONLY · RESEARCH BACKTEST", ha="right",
                 va="bottom", fontsize=7, color="grey")
        out = charts_dir / f"{stamp}-equity-curve.png"
        fig.tight_layout()
        fig.savefig(out, dpi=110)
        plt.close(fig)
        paths["equity_chart_png"] = str(out)

    # R distribution.
    rs = [t.pnl_r for t in run.trades if t.pnl_r is not None and t.outcome != "not_filled"]
    if rs:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(rs, bins=20, color="#4f80c4", edgecolor="white")
        ax.axvline(0, color="grey", linewidth=0.8, linestyle="--")
        ax.set_title("R distribution per filled trade")
        ax.set_xlabel("R")
        ax.set_ylabel("count")
        ax.grid(True, linewidth=0.3, axis="y")
        fig.text(0.99, 0.01, "PAPER ONLY · RESEARCH BACKTEST", ha="right",
                 va="bottom", fontsize=7, color="grey")
        out = charts_dir / f"{stamp}-r-distribution.png"
        fig.tight_layout()
        fig.savefig(out, dpi=110)
        plt.close(fig)
        paths["r_distribution_chart_png"] = str(out)

    # Trades by hour.
    by_hour = run.metrics.by_hour
    if by_hour:
        hours = list(by_hour.keys())
        counts = [int(by_hour[h].get("trades") or 0) for h in hours]
        wins = [int(by_hour[h].get("wins") or 0) for h in hours]
        losses = [c - w for c, w in zip(counts, wins)]
        fig, ax = plt.subplots(figsize=(8, 4))
        x = list(range(len(hours)))
        ax.bar(x, wins, color="#4faa6d", label="wins")
        ax.bar(x, losses, bottom=wins, color="#c46f6f", label="losses")
        ax.set_xticks(x)
        ax.set_xticklabels(hours, rotation=45, ha="right")
        ax.set_title("Trades by entry hour (ET)")
        ax.set_ylabel("filled trades")
        ax.grid(True, linewidth=0.3, axis="y")
        ax.legend(loc="upper right", fontsize=8)
        fig.text(0.99, 0.01, "PAPER ONLY · RESEARCH BACKTEST", ha="right",
                 va="bottom", fontsize=7, color="grey")
        out = charts_dir / f"{stamp}-trades-by-hour.png"
        fig.tight_layout()
        fig.savefig(out, dpi=110)
        plt.close(fig)
        paths["trades_by_hour_chart_png"] = str(out)

    return paths


__all__ = [
    "REPORT_DIRNAME",
    "save_backtest_artifacts",
]
