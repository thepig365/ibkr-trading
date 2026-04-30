"""Save Forex bracket preview PNG under data/reports/forex_trade_charts/."""

from __future__ import annotations

from pathlib import Path

from .forex_preview_chart import render_forex_bracket_chart_png_bytes
from .trade_lifecycle import forex_chart_dir


def save_forex_trade_chart_png(
    project_root: Path | str,
    *,
    trade_id: str,
    pair_slug: str,
    entry: float | None,
    stop: float | None,
    target: float | None,
) -> Path | None:
    """Write ``{trade_id}.png`` — returns path or None."""

    png, err = render_forex_bracket_chart_png_bytes(
        Path(project_root),
        pair_slug=pair_slug.strip().upper(),
        entry=entry,
        stop=stop,
        target=target,
    )
    if not png:
        _ = err
        return None
    tid_safe = "".join(c for c in str(trade_id).strip().lower() if c in "abcdef0123456789") or str(
        trade_id
    ).replace("/", "_")[:48]
    root = forex_chart_dir(Path(project_root))
    out = root / f"{tid_safe}.png"
    out.write_bytes(png)
    return out


__all__ = ["save_forex_trade_chart_png"]
