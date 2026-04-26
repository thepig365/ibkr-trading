"""Disk usage and safe cleanup of non-audit, non-runtime artifacts (Prompt 13DATA-UI)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc


@dataclass
class DirStat:
    relpath: str
    bytes: int
    file_count: int


@dataclass
class DataStatusReport:
    dirs: list[DirStat] = field(default_factory=list)
    total_bytes: int = 0
    project_root: str = ""


def _dir_size(root: Path, rel: str) -> DirStat:
    base = (root / rel).resolve()
    if not base.is_dir():
        return DirStat(rel, 0, 0)
    total = 0
    n = 0
    for p in base.rglob("*"):
        if p.is_file() and p.is_symlink() is False:
            try:
                total += p.stat().st_size
                n += 1
            except OSError:
                continue
    return DirStat(rel, total, n)


def data_status(root: Path) -> DataStatusReport:
    out = DataStatusReport(project_root=str(root))
    for rel in (
        "data/paper_orders",
        "data/runtime",
        "data/reports",
        "data/debug_charts",
        "data/candles",
        "data/backtests",
        "data/research",
        "data/intraday_smc",
        "data/watchlists",
        "data/edge_profiles",
        "data/mtf_smc",
        "logs",
    ):
        st = _dir_size(root, rel)
        out.dirs.append(st)
        out.total_bytes += st.bytes
    return out


def _is_protected_path(root: Path, p: Path) -> bool:
    try:
        rroot = root.resolve()
        rel = p.resolve().relative_to(rroot)
    except (OSError, ValueError):
        return True
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "data" and parts[1] == "paper_orders":
        return True
    if len(parts) >= 2 and parts[0] == "data" and parts[1] == "runtime":
        return True
    s = rel.as_posix()
    if s == "data/trading_bot.sqlite" or s.endswith("trading_bot.sqlite") and "data" in s:
        return True
    if parts == (".env",) or (len(parts) == 1 and parts[0] == ".env"):
        return True
    if "settings.local.yaml" in rel.name and (len(parts) > 0 and parts[0] == "config"):
        return True
    return False


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class CleanupResult:
    dry_run: bool
    would_delete: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped_protected: list[str] = field(default_factory=list)
    message: str = ""


def data_cleanup(
    root: Path,
    *,
    apply: bool = False,
    now: datetime | None = None,
) -> CleanupResult:
    """Remove old ephemeral report/chart/log files. Never touch audit/runtime/paper_orders."""
    now = now or _utc_now()
    res = CleanupResult(dry_run=not apply)
    if not (root / "data").is_dir():
        res.message = "no data/ directory"
        return res

    # age thresholds
    t_reports = now - timedelta(hours=24)
    t_charts = now - timedelta(days=7)
    t_logs = now - timedelta(days=30)
    t_research = now - timedelta(days=14)
    t_scan = now - timedelta(days=14)
    t_bt_charts = now - timedelta(days=30)

    candidates: list[tuple[Path, str]] = []
    for folder, min_age, label in [
        (root / "data" / "reports" / "paper", t_reports, "ephemeral_report"),
        (root / "data" / "debug_charts", t_charts, "debug_charts"),
        (root / "data" / "research", t_research, "research"),
        (root / "data" / "intraday_smc", t_scan, "intraday_smc"),
        (root / "data" / "mtf_smc", t_scan, "mtf_smc"),
    ]:
        if not folder.is_dir():
            continue
        for p in folder.rglob("*"):
            if not p.is_file():
                continue
            if _is_protected_path(root, p):
                res.skipped_protected.append(str(p.relative_to(root)))
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            # current-day: never delete
            if mtime.date() == now.date():
                continue
            if mtime < min_age:
                candidates.append((p, label))

    bt_charts = root / "data" / "backtests" / "intraday" / "charts"
    if bt_charts.is_dir():
        for p in bt_charts.rglob("*"):
            if not p.is_file():
                continue
            if _is_protected_path(root, p):
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            if mtime.date() == now.date():
                continue
            if mtime < t_bt_charts:
                candidates.append((p, "backtest_charts"))

    logd = root / "logs"
    if logd.is_dir():
        for p in logd.iterdir():
            if not p.is_file():
                continue
            if _is_protected_path(root, p):
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            if mtime.date() == now.date():
                continue
            if mtime < t_logs:
                candidates.append((p, "logs"))

    for p, _tag in candidates:
        rel = str(p.relative_to(root))
        if apply:
            try:
                p.unlink()
                res.deleted.append(rel)
            except OSError as e:
                res.message += f"err {rel}: {e}\n"
        else:
            res.would_delete.append(rel)

    res.message = res.message or (
        f"{'deleted' if apply else 'would_delete'} {len(res.deleted or res.would_delete)} files"
    )
    return res
