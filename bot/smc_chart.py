"""Matplotlib renderer for SMC dry-run validation charts.

The renderer is intentionally read-only: it consumes the
:class:`StrategyEvaluation` payload and a list of :class:`Candle`
objects, draws annotations, writes a PNG, and returns the path.

It never:
    * imports :class:`bot.broker.Broker.place_order`,
    * touches the IBKR socket,
    * mutates the evaluation,
    * decides whether a setup is approved.

Visual validation does NOT imply trade approval. Approval still
requires every existing safety gate (trading.enabled, reconciliation,
block_live_trading, asset allow-list, regime filter, R/R floor, …).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .market_structure import Candle, Candles
    from .strategy_engine import StrategyEvaluation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def render_smc_chart(
    evaluation: "StrategyEvaluation",
    candles: "Candles",
    *,
    output_dir: Path,
    swings_high: Iterable[Any] | None = None,
    swings_low: Iterable[Any] | None = None,
    filename: str | None = None,
) -> Path:
    """Render and persist the SMC validation chart.

    Returns the absolute path of the PNG. The caller is responsible
    for storing this on the evaluation (``evaluation.chart_path``) and
    for surfacing the path in any persisted JSON.
    """
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)  # headless safe
        import matplotlib.dates as mdates  # noqa: F401  - reserved for intraday
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except Exception as exc:  # noqa: BLE001 - matplotlib is optional at install time
        raise RuntimeError(
            "matplotlib is required for SMC chart output. "
            "Install with `pip install matplotlib`."
        ) from exc

    if not candles:
        raise ValueError("render_smc_chart requires at least one candle")

    output_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe_symbol = evaluation.symbol.upper().replace("/", "_")
    name = filename or f"{today}-{safe_symbol}-{evaluation.timeframe}-smc.png"
    out_path = output_dir / name

    indices = list(range(len(candles)))
    fig, ax = plt.subplots(figsize=(14, 7))

    _draw_candles(ax, candles)
    _draw_swing_markers(ax, swings_high, candles, kind="high")
    _draw_swing_markers(ax, swings_low, candles, kind="low")
    _draw_sequence_markers(ax, evaluation, candles, Rectangle=Rectangle)
    _draw_levels(ax, evaluation, indices)
    _decorate(ax, evaluation, candles)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    logger.debug("SMC chart written to %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
_BULL_COLOR = "#1f9d55"
_BEAR_COLOR = "#cc1f1a"
_FVG_COLOR = "#1c64f2"
_OB_COLOR = "#a16207"
_ENTRY_COLOR = "#1d4ed8"
_STOP_COLOR = "#b91c1c"
_TARGET_COLOR = "#047857"
_SWEEP_COLOR = "#dc2626"
_CHOCH_COLOR = "#7c3aed"


def _draw_candles(ax, candles: "Candles") -> None:
    """Render OHLC candles using matplotlib primitives only."""
    for i, c in enumerate(candles):
        color = _BULL_COLOR if c.close >= c.open else _BEAR_COLOR
        ax.vlines(i, c.low, c.high, color=color, linewidth=0.8, zorder=2)
        body_low = min(c.open, c.close)
        body_height = max(abs(c.close - c.open), 1e-6)
        rect = ax.bar(
            i,
            height=body_height,
            bottom=body_low,
            width=0.6,
            color=color,
            edgecolor=color,
            linewidth=0.0,
            zorder=3,
            align="center",
        )
        del rect
    # Extra room on the right for Entry / Stop / T1 labels.
    ax.set_xlim(-1, len(candles) + max(4, int(len(candles) * 0.05)))
    ax.grid(True, alpha=0.2, zorder=1)


def _draw_swing_markers(ax, swings, candles: "Candles", *, kind: str) -> None:
    if not swings:
        return
    for s in swings:
        confirmed = bool(getattr(s, "confirmed", True))
        if not confirmed:
            continue
        idx = int(getattr(s, "index", -1))
        if idx < 0 or idx >= len(candles):
            continue
        price = float(getattr(s, "price", 0.0))
        if kind == "high":
            ax.scatter(
                idx, price, marker="v", color="#0f172a",
                s=36, zorder=5, label="_swing_high",
            )
        else:
            ax.scatter(
                idx, price, marker="^", color="#0f172a",
                s=36, zorder=5, label="_swing_low",
            )


def _draw_sequence_markers(
    ax,
    evaluation: "StrategyEvaluation",
    candles: "Candles",
    *,
    Rectangle,
) -> None:
    """Draw every structural marker + its descriptive text label.

    The text labels are placed with small offsets so they don't stack
    on top of each other. Labels show the exact date (from the
    matching candle) and the exact price so reviewers can cross-check
    against their own chart reading.
    """
    seq = evaluation.sequence or {}
    sweep = seq.get("sweep") or {}
    choch = seq.get("choch") or {}
    fvg = seq.get("fvg") or {}
    ob = seq.get("order_block") or {}
    n = len(candles)
    right_edge = n - 0.5

    if sweep.get("found"):
        idx = int(sweep.get("index", 0))
        if 0 <= idx < n:
            sw_low = float(sweep.get("sweep_low", candles[idx].low))
            sweep_ts = str(sweep.get("timestamp") or candles[idx].timestamp)
            # Vertical band around the sweep candle so the eye lands there.
            ax.axvspan(
                idx - 0.4, idx + 0.4,
                color=_SWEEP_COLOR, alpha=0.10,
                zorder=1, label="_sweep_band",
            )
            ax.scatter(idx, sw_low, marker="X", color=_SWEEP_COLOR,
                       s=110, zorder=6, label="Sweep")
            ax.annotate(
                f"Sweep {sweep_ts} low={sw_low:.2f}",
                xy=(idx, sw_low), xytext=(8, -18),
                textcoords="offset points", fontsize=8,
                color=_SWEEP_COLOR,
                bbox={"facecolor": "white", "edgecolor": _SWEEP_COLOR,
                      "alpha": 0.85, "pad": 1.5, "boxstyle": "round,pad=0.25"},
                zorder=7,
            )
            swept = sweep.get("swept_low_price")
            swept_idx = sweep.get("swept_low_index")
            if isinstance(swept, (int, float)):
                ax.hlines(
                    float(swept), 0, right_edge,
                    colors=_SWEEP_COLOR, linestyles=":",
                    linewidth=1.0, alpha=0.6,
                    label="Swept low",
                )
                swept_ts = ""
                if (
                    isinstance(swept_idx, (int, float))
                    and 0 <= int(swept_idx) < n
                ):
                    swept_ts = candles[int(swept_idx)].timestamp
                ax.annotate(
                    f"Swept low {swept_ts} price={float(swept):.2f}",
                    xy=(right_edge, float(swept)),
                    xytext=(-6, 6), textcoords="offset points",
                    fontsize=7, color=_SWEEP_COLOR,
                    ha="right", va="bottom", zorder=7,
                )

    if choch.get("found"):
        idx = int(choch.get("index", 0))
        if 0 <= idx < n:
            close_val = float(choch.get("close", candles[idx].close))
            broke_val = choch.get("pivot_high_broken")
            choch_ts = str(choch.get("timestamp") or candles[idx].timestamp)
            ax.axvspan(
                idx - 0.4, idx + 0.4,
                color=_CHOCH_COLOR, alpha=0.10,
                zorder=1, label="_choch_band",
            )
            ax.scatter(
                idx, close_val,
                marker="*", color=_CHOCH_COLOR, s=180, zorder=6,
                label="ChoCH",
            )
            broke_str = (
                f" broke={float(broke_val):.2f}"
                if isinstance(broke_val, (int, float)) else ""
            )
            ax.annotate(
                f"ChoCH {choch_ts} close={close_val:.2f}{broke_str}",
                xy=(idx, close_val), xytext=(8, 14),
                textcoords="offset points", fontsize=8,
                color=_CHOCH_COLOR,
                bbox={"facecolor": "white", "edgecolor": _CHOCH_COLOR,
                      "alpha": 0.85, "pad": 1.5, "boxstyle": "round,pad=0.25"},
                zorder=7,
            )

    if fvg.get("found"):
        start = int(fvg.get("start_index", 0))
        end = int(fvg.get("end_index", start))
        low = float(fvg.get("low", 0.0))
        high = float(fvg.get("high", 0.0))
        if high > low:
            rect = Rectangle(
                (start - 0.4, low),
                width=(end - start) + 0.8,
                height=high - low,
                facecolor=_FVG_COLOR,
                alpha=0.22,
                edgecolor=_FVG_COLOR,
                linewidth=1.2,
                label="Bullish FVG",
                zorder=2,
            )
            ax.add_patch(rect)
            ax.text(
                end + 0.5, (low + high) / 2.0,
                f"FVG {low:.2f}–{high:.2f}",
                fontsize=8, color=_FVG_COLOR,
                va="center", ha="left", zorder=5,
                bbox={"facecolor": "white", "edgecolor": _FVG_COLOR,
                      "alpha": 0.8, "pad": 1.2, "boxstyle": "round,pad=0.2"},
            )

    if ob.get("found"):
        idx = int(ob.get("index", 0))
        low = float(ob.get("low", 0.0))
        high = float(ob.get("high", 0.0))
        if high > low and 0 <= idx < n:
            rect = Rectangle(
                (idx - 0.45, low),
                width=0.9,
                height=high - low,
                facecolor=_OB_COLOR,
                alpha=0.28,
                edgecolor=_OB_COLOR,
                linewidth=1.2,
                label="Order block",
                zorder=2,
            )
            ax.add_patch(rect)
            ax.text(
                idx + 0.6, (low + high) / 2.0,
                f"OB {low:.2f}–{high:.2f}",
                fontsize=8, color=_OB_COLOR,
                va="center", ha="left", zorder=5,
                bbox={"facecolor": "white", "edgecolor": _OB_COLOR,
                      "alpha": 0.8, "pad": 1.2, "boxstyle": "round,pad=0.2"},
            )


def _draw_levels(ax, evaluation: "StrategyEvaluation", indices: list[int]) -> None:
    if not indices:
        return
    plan = evaluation.trade_plan or {}
    levels = (
        ("entry_price", _ENTRY_COLOR, "Limit entry", "Entry"),
        ("structural_stop", _STOP_COLOR, "Structural stop", "Stop"),
        ("target_1", _TARGET_COLOR, "Target 1", "T1"),
    )
    right_edge = indices[-1] + 0.5
    for key, color, legend_label, tag in levels:
        v = plan.get(key)
        if not isinstance(v, (int, float)):
            continue
        ax.hlines(
            float(v), indices[0] - 0.5, right_edge,
            colors=color, linestyles="--", linewidth=1.4,
            label=legend_label,
        )
        ax.text(
            right_edge, float(v),
            f" {tag} {float(v):.2f}",
            fontsize=8, color=color,
            va="center", ha="left", zorder=6,
            bbox={"facecolor": "white", "edgecolor": color,
                  "alpha": 0.85, "pad": 1.4, "boxstyle": "round,pad=0.25"},
        )


def _decorate(ax, evaluation: "StrategyEvaluation", candles: "Candles") -> None:
    title = (
        f"{evaluation.strategy} — {evaluation.symbol} — "
        f"{evaluation.timeframe}"
    )
    ax.set_title(title, fontsize=12, loc="left", pad=14)

    status = "APPROVED FOR DRY RUN" if evaluation.approved_for_dry_run else "REJECTED"
    subtitle_bits = [status]
    if evaluation.market_regime:
        subtitle_bits.append(f"regime={evaluation.market_regime}")
    if evaluation.candle_count:
        subtitle_bits.append(f"candles={evaluation.candle_count}")
    if evaluation.candles_start and evaluation.candles_end:
        subtitle_bits.append(
            f"range={evaluation.candles_start}…{evaluation.candles_end}"
        )
    ax.text(
        0.0, 1.01, "  •  ".join(subtitle_bits),
        transform=ax.transAxes, fontsize=9, color="#475569",
    )

    # Always-visible safety banner. Removing this would defeat the
    # purpose of the chart.
    ax.text(
        1.0, 1.01,
        "research only — execution disabled — chart ≠ trade approval",
        transform=ax.transAxes, fontsize=9, color="#b91c1c",
        ha="right",
    )

    rejections = list(evaluation.rejection_reasons or [])
    if rejections:
        ax.text(
            0.01, 0.02,
            "Rejection reasons:\n• " + "\n• ".join(rejections[:6]),
            transform=ax.transAxes, fontsize=8, color="#7f1d1d",
            verticalalignment="bottom",
            bbox={"facecolor": "#fee2e2", "edgecolor": "#fecaca",
                  "boxstyle": "round,pad=0.3"},
        )

    missing = _missing_sequence_steps(evaluation)
    if missing:
        ax.text(
            0.99, 0.02,
            "Missing structural steps:\n• " + "\n• ".join(missing),
            transform=ax.transAxes, fontsize=8, color="#374151",
            verticalalignment="bottom",
            horizontalalignment="right",
            bbox={"facecolor": "#fef3c7", "edgecolor": "#fde68a",
                  "boxstyle": "round,pad=0.3"},
        )

    handles, labels = ax.get_legend_handles_labels()
    seen: dict[str, Any] = {}
    for h, lab in zip(handles, labels):
        if lab.startswith("_") or lab in seen:
            continue
        seen[lab] = h
    if seen:
        ax.legend(seen.values(), seen.keys(), loc="upper left", fontsize=8)

    if str(getattr(evaluation, "timeframe", "") or "") == "30min":
        ax.set_xlabel(
            "bar index (0 = oldest) — 30m bars: use sweep/ChoCH/FVG/OB text "
            "and subtitle range=… for full NY-session timestamps (RTH)"
        )
    else:
        ax.set_xlabel("bar index (0 = oldest)")
    ax.set_ylabel("price")


def _missing_sequence_steps(evaluation: "StrategyEvaluation") -> list[str]:
    seq = evaluation.sequence or {}
    out = []
    for key, label in (
        ("sweep", "liquidity sweep"),
        ("choch", "ChoCH"),
        ("fvg", "bullish FVG"),
        ("order_block", "order block"),
    ):
        if not (seq.get(key) or {}).get("found"):
            out.append(label)
    return out


__all__ = ["render_smc_chart"]
