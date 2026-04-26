"""Persist edge profile batches to ``data/edge_profiles/`` (Prompt 13L-alt)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import EDGE_PROFILE_DIR
from .ticker_edge import TickerEdgeProfile
from .ranking import rank_profiles


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def edge_profiles_dir(project_root: Path) -> Path:
    p = Path(project_root) / EDGE_PROFILE_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_edge_profiles_artifacts(
    project_root: Path,
    profiles: list[TickerEdgeProfile],
    *,
    run_date: str | None = None,
    top_n: int | None = None,
) -> dict[str, str]:
    """Write ``YYYY-MM-DD-edge-profiles.json`` and ``-edge-profile-report.md``."""
    d = run_date or _utc_date()
    out_dir = edge_profiles_dir(project_root)
    ordered = rank_profiles(profiles, top_n=top_n)
    payload: dict[str, Any] = {
        "date": d,
        "generated_at_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "strategy_id": (ordered[0].strategy_id if ordered else "ict_smc_intraday_v1"),
        "profiles": [p.to_dict() for p in ordered],
    }
    jp = out_dir / f"{d}-edge-profiles.json"
    jp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    md = _render_markdown(d, ordered)
    mp = out_dir / f"{d}-edge-profile-report.md"
    mp.write_text(md, encoding="utf-8")
    return {"json": str(jp), "md": str(mp)}


def _render_markdown(d: str, ordered: list[TickerEdgeProfile]) -> str:
    lines = [
        f"# Ticker edge profile report — {d}",
        "",
        "| Symbol | edge_score | confidence | mode | max_risk× | R̄ | PF | n fills |",
        "|--------|------------|------------|------|-----------|----|----|---------|",
    ]
    for p in ordered:
        ar = f"{p.average_r:.3f}" if p.average_r is not None else "—"
        pf = f"{p.profit_factor:.2f}" if p.profit_factor is not None else "—"
        lines.append(
            f"| {p.symbol} | {p.edge_score:.1f} | {p.confidence_level} | "
            f"{p.recommended_mode} | {p.max_risk_multiplier} | {ar} | {pf} | {p.filled_trades} |"
        )
    lines.append("")
    return "\n".join(lines)


def latest_edge_profiles_path(project_root: Path) -> Path | None:
    """Newest ``*-edge-profiles.json`` in the edge directory."""
    out_dir = Path(project_root) / EDGE_PROFILE_DIR
    if not out_dir.is_dir():
        return None
    cands = sorted(out_dir.glob("*-edge-profiles.json"))
    return cands[-1] if cands else None


def load_edge_profiles_merged(
    project_root: Path,
) -> dict[str, TickerEdgeProfile]:
    """Load latest file; return symbol -> profile (last file wins)."""
    p = latest_edge_profiles_path(project_root)
    if p is None or not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    profs: dict[str, TickerEdgeProfile] = {}
    for row in data.get("profiles") or []:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        # Rehydrate — minimal parse for read path
        profs[sym] = TickerEdgeProfile(
            symbol=sym,
            strategy_id=str(row.get("strategy_id") or "ict_smc_intraday_v1"),
            sample_start=str(row.get("sample_start") or ""),
            sample_end=str(row.get("sample_end") or ""),
            total_signals=int(row.get("total_signals") or 0),
            filled_trades=int(row.get("filled_trades") or 0),
            fill_rate=float(row.get("fill_rate") or 0.0),
            win_rate=row.get("win_rate"),
            average_r=row.get("average_r"),
            median_r=row.get("median_r"),
            total_r=float(row.get("total_r") or 0.0),
            max_drawdown_r=float(row.get("max_drawdown_r") or 0.0),
            profit_factor=row.get("profit_factor"),
            strict_count=int(row.get("strict_count") or 0),
            strict_win_rate=row.get("strict_win_rate"),
            strict_average_r=row.get("strict_average_r"),
            aggressive_count=int(row.get("aggressive_count") or 0),
            aggressive_win_rate=row.get("aggressive_win_rate"),
            aggressive_average_r=row.get("aggressive_average_r"),
            long_count=int(row.get("long_count") or 0),
            long_win_rate=row.get("long_win_rate"),
            long_average_r=row.get("long_average_r"),
            short_count=int(row.get("short_count") or 0),
            short_win_rate=row.get("short_win_rate"),
            short_average_r=row.get("short_average_r"),
            best_hours=list(row.get("best_hours") or []),
            weak_hours=list(row.get("weak_hours") or []),
            best_direction=str(row.get("best_direction") or "both"),
            reliability_score=float(row.get("reliability_score") or 0.0),
            edge_score=float(row.get("edge_score") or 0.0),
            confidence_level=str(
                row.get("confidence_level") or "insufficient_data"
            ),
            recommended_mode=str(
                row.get("recommended_mode") or "watch_only"
            ),
            max_risk_multiplier=float(
                row.get("max_risk_multiplier") or 0.0
            ),
            notes=str(row.get("notes") or ""),
        )
    return profs


def load_profile_for_symbol(
    project_root: Path, symbol: str, strategy_id: str | None = None
) -> TickerEdgeProfile | None:
    m = load_edge_profiles_merged(project_root)
    p = m.get((symbol or "").strip().upper())
    if p is None:
        return None
    if strategy_id and p.strategy_id != strategy_id:
        return None
    return p
