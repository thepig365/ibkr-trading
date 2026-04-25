"""Chart rendering for ICT/SMC Intraday V1.

Three PNGs per scan, written under ``data/debug_charts/``:

* ``YYYY-MM-DD-SYMBOL-intraday-30m-smc.png`` — context (bias + liquidity).
* ``YYYY-MM-DD-SYMBOL-intraday-5m-smc.png``  — sweep + setup zone + bias.
* ``YYYY-MM-DD-SYMBOL-intraday-1m-smc.png``  — micro sweep + MSS + entry/stop/target.

Banner text on every chart: "PAPER ONLY / RESEARCH SCAN".

Render failures must NOT crash the scan — the caller stores the error
in ``IntradayEvaluation.chart_error`` and writes the JSON anyway.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import (
    DIRECTION_LONG,
    DIRECTION_SHORT,
    IntradayEvaluation,
)

LOG = logging.getLogger(__name__)


_BANNER = "PAPER ONLY / RESEARCH SCAN — chart is not live trade approval"

_BULL = "#1f9d55"
_BEAR = "#cc1f1a"
_FVG = "#1c64f2"
_OB = "#a16207"
_ENTRY = "#1d4ed8"
_STOP = "#b91c1c"
_TARGET = "#047857"
_SWEEP = "#dc2626"
_MSS = "#7c3aed"
_ZONE = "#94a3b8"


def render_intraday_charts(
    symbol: str,
    *,
    bars_30m: list[dict[str, Any]] | None,
    bars_5m: list[dict[str, Any]] | None,
    bars_1m: list[dict[str, Any]] | None,
    evaluation: IntradayEvaluation,
    output_dir: Path,
) -> list[str]:
    """Render up to three PNGs and return their absolute paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    safe = symbol.upper().replace("/", "_")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    paths: list[str] = []
    plans: list[tuple[str, str, list[dict[str, Any]] | None, str]] = [
        ("30m", "intraday-30m-smc", bars_30m, "context"),
        ("5m", "intraday-5m-smc", bars_5m, "setup"),
        ("1m", "intraday-1m-smc", bars_1m, "trigger"),
    ]
    for tf, label, rows, mode in plans:
        if not rows or len(rows) < 5:
            LOG.debug("intraday chart %s skipped: not enough %s bars", symbol, tf)
            continue
        try:
            p = _render_one(
                rows[-200:],
                tf=tf,
                mode=mode,
                evaluation=evaluation,
                output_dir=output_dir,
                filename=f"{day}-{safe}-{label}.png",
            )
            paths.append(str(p))
        except Exception as exc:  # noqa: BLE001
            LOG.info("intraday chart %s/%s render failed: %s", symbol, tf, exc)
            raise
    return paths


def _render_one(
    rows: list[dict[str, Any]],
    *,
    tf: str,
    mode: str,
    evaluation: IntradayEvaluation,
    output_dir: Path,
    filename: str,
) -> Path:
    """Render one PNG. Imports matplotlib lazily."""
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "matplotlib is required for ICT/SMC intraday charts"
        ) from exc

    n = len(rows)
    indices = list(range(n))
    fig, ax = plt.subplots(figsize=(14, 7))

    # --- candles ---
    for i, r in enumerate(rows):
        try:
            o = float(r.get("open", 0.0) or 0.0)
            h = float(r.get("high", 0.0) or 0.0)
            l = float(r.get("low", 0.0) or 0.0)
            c = float(r.get("close", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        color = _BULL if c >= o else _BEAR
        ax.vlines(i, l, h, color=color, linewidth=0.7, zorder=2)
        body_low = min(o, c)
        body_h = max(abs(c - o), 1e-6)
        ax.bar(
            i,
            height=body_h,
            bottom=body_low,
            width=0.6,
            color=color,
            edgecolor=color,
            linewidth=0.0,
            zorder=3,
            align="center",
        )

    # --- mode-specific overlays ---
    setup = evaluation.five_min_setup
    trig = evaluation.one_min_trigger
    plan = evaluation.trade_plan

    if mode == "context":
        # 30m: draw nearest liquidity levels.
        ctx = evaluation.context
        if ctx and ctx.liquidity_levels:
            for ll in ctx.liquidity_levels:
                ax.axhline(
                    ll.price,
                    color=_SWEEP if ll.side == "buy_side" else _MSS,
                    linestyle=":",
                    linewidth=1.0,
                    alpha=0.7,
                )
                ax.text(
                    n - 1,
                    ll.price,
                    f"  {ll.side} {ll.price:.2f}",
                    fontsize=8,
                    color=_SWEEP if ll.side == "buy_side" else _MSS,
                    va="center",
                )

    if mode == "setup" and setup and setup.found:
        # Mark sweep + setup zone on 5m.
        if setup.swept_level_price is not None:
            ax.axhline(
                setup.swept_level_price,
                color=_SWEEP,
                linestyle="--",
                linewidth=1.0,
                alpha=0.8,
            )
            ax.text(
                0, setup.swept_level_price,
                f"swept {setup.swept_level_price:.2f}",
                fontsize=8, color=_SWEEP, va="bottom",
            )
        zone_lo = setup.setup_zone_low
        zone_hi = setup.setup_zone_high
        if zone_lo is not None and zone_hi is not None and zone_hi > zone_lo:
            ax.add_patch(
                Rectangle(
                    (0, zone_lo),
                    n - 1,
                    zone_hi - zone_lo,
                    facecolor=_FVG if setup.setup_kind == "fvg" else _OB,
                    alpha=0.18,
                    edgecolor=_FVG if setup.setup_kind == "fvg" else _OB,
                    linewidth=1.0,
                )
            )
            ax.text(
                n - 1,
                zone_hi,
                f"  zone {setup.setup_kind}",
                fontsize=8,
                color=_FVG if setup.setup_kind == "fvg" else _OB,
                va="bottom",
            )
        if setup.mss_found and setup.mss_pivot_price is not None:
            ax.axhline(
                setup.mss_pivot_price,
                color=_MSS,
                linestyle=":",
                linewidth=1.0,
                alpha=0.8,
            )

    if mode == "trigger":
        # 1m chart with micro sweep + entry/stop/target.
        if trig and trig.found and trig.swept_level_price is not None:
            ax.axhline(
                trig.swept_level_price,
                color=_SWEEP,
                linestyle="--",
                linewidth=1.0,
                alpha=0.8,
            )
            ax.text(
                0,
                trig.swept_level_price,
                f"micro swept {trig.swept_level_price:.2f}",
                fontsize=8,
                color=_SWEEP,
                va="bottom",
            )
        if trig and trig.fvg_low is not None and trig.fvg_high is not None:
            ax.add_patch(
                Rectangle(
                    (0, trig.fvg_low),
                    n - 1,
                    trig.fvg_high - trig.fvg_low,
                    facecolor=_FVG,
                    alpha=0.20,
                    edgecolor=_FVG,
                    linewidth=1.0,
                )
            )
        elif trig and trig.ob_low is not None and trig.ob_high is not None:
            ax.add_patch(
                Rectangle(
                    (0, trig.ob_low),
                    n - 1,
                    trig.ob_high - trig.ob_low,
                    facecolor=_OB,
                    alpha=0.18,
                    edgecolor=_OB,
                    linewidth=1.0,
                )
            )
        if plan and plan.valid:
            for price, color, label in (
                (plan.entry, _ENTRY, "entry"),
                (plan.stop, _STOP, "stop"),
                (plan.target, _TARGET, "target"),
            ):
                if price is None:
                    continue
                ax.axhline(price, color=color, linestyle="-", linewidth=1.1, alpha=0.85)
                ax.text(
                    n - 1,
                    price,
                    f"  {label} {price:.2f}",
                    fontsize=8,
                    color=color,
                    va="center",
                )

    # --- title + banner ---
    cat = evaluation.signal_category
    direction = evaluation.direction
    title = (
        f"{evaluation.symbol} — ICT/SMC intraday {tf} ({mode}) — "
        f"{cat} / {direction}"
    )
    ax.set_title(title, fontsize=11)
    ax.set_xlabel("bar index")
    ax.set_ylabel("price")
    ax.grid(True, linestyle=":", alpha=0.3)

    fig.text(
        0.5,
        0.01,
        _BANNER,
        ha="center",
        fontsize=8,
        color="#475569",
        alpha=0.9,
    )

    fig.tight_layout(rect=(0, 0.03, 1, 1))
    out_path = output_dir / filename
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


__all__ = ["render_intraday_charts"]
