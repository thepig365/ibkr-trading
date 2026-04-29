"""Optional PNG preview: Forex 1m candles + bracket lines (local cache only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .candle_store import load_forex_candles


def render_forex_bracket_chart_png_bytes(
    project_root: Path,
    *,
    pair_slug: str,
    entry: float | None,
    stop: float | None,
    target: float | None,
    max_bars: int = 200,
) -> tuple[bytes | None, str | None]:
    """Return PNG bytes or ``(None, error)``. Uses matplotlib lazily."""

    bars = load_forex_candles(project_root, pair_slug, "1min")
    if not bars:
        return None, "no_cached_candles"

    tail = bars[-max_bars:] if len(bars) > max_bars else bars

    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except Exception as exc:
        return None, f"matplotlib:{exc}"

    xs = range(len(tail))
    closes = [b.close for b in tail]

    fig, ax = plt.subplots(figsize=(9, 3.8), dpi=110)
    ax.plot(xs, closes, color="#3498db", lw=1.0, label="close")
    for y, c, lbl in (
        (entry, "#f1c40f", "entry"),
        (stop, "#e74c3c", "stop"),
        (target, "#2ecc71", "target"),
    ):
        if y is not None and y > 0:
            ax.axhline(float(y), color=c, lw=1.1, linestyle="--", label=lbl)

    ax.set_title(f"{pair_slug} 1m (local cache)")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()

    import io

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue(), None


__all__ = ["render_forex_bracket_chart_png_bytes"]
