"""Canonical paths and safe readers for the paper report engine (Prompt 13M)."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

DEFAULT_REPORT_DIR = "data/reports/paper"
RESEARCH_DIR = "data/research"
INTRADAY_SMC_DIR = "data/intraday_smc"
SCAN_SUMMARY_GLOB = "*-watchlist-intraday-smc-summary.json"
EDGE_DIR = "data/edge_profiles"
PAPER_ORDERS_DIR = "data/paper_orders"
BACKTEST_INTRADAY_DIR = "data/backtests/intraday"
RUNTIME_LOOP_STATE = "data/runtime/intraday_auto_paper_loop_state.json"
RUNTIME_FIRST_PASS = "data/runtime/first_paper_pass_last.json"
RUNTIME_INTRADAY_FLAG = "data/runtime/intraday_auto_paper_enabled"
KILL_SWITCH = "data/KILL_SWITCH"


def utc_today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def default_report_dir(project_root: Path) -> Path:
    p = project_root / DEFAULT_REPORT_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def safe_read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def latest_glob_path(dir_path: Path, pattern: str) -> Path | None:
    if not dir_path.is_dir():
        return None
    cands = sorted(dir_path.glob(pattern), key=lambda p: p.stat().st_mtime)
    return cands[-1] if cands else None


def parse_date(s: str) -> date:
    return date.fromisoformat(s.strip()[:10])


def infer_latest_report_date(project_root: Path) -> str:
    """Best UTC calendar day from existing artifacts; falls back to today."""
    cands: list[str] = []
    pod = project_root / PAPER_ORDERS_DIR
    if pod.is_dir():
        for p in pod.glob("*-intraday-paper-orders.jsonl"):
            m = DATE_RE.match(p.name)
            if m:
                cands.append(m.group(1))
    smc = project_root / INTRADAY_SMC_DIR
    if smc.is_dir():
        for p in smc.glob(SCAN_SUMMARY_GLOB):
            m = DATE_RE.match(p.name)
            if m:
                cands.append(m.group(1))
    if cands:
        return max(cands)
    return utc_today_str()


def research_files_for_date(
    project_root: Path, report_date: str
) -> tuple[Path | None, Path | None]:
    """Prefer files whose names start with report_date, else latest in dir."""
    rdir = project_root / RESEARCH_DIR
    if not rdir.is_dir():
        return None, None
    report: Path | None = None
    inst: Path | None = None
    pref = f"{report_date}-"
    for p in sorted(rdir.glob("*-research-report.json")):
        if p.name.startswith(pref):
            report = p
            break
    if report is None:
        report = latest_glob_path(rdir, "*-research-report.json")
    for p in sorted(rdir.glob("*-research-instructions.json")):
        if p.name.startswith(pref):
            inst = p
            break
    if inst is None:
        inst = latest_glob_path(rdir, "*-research-instructions.json")
    return report, inst


def edge_profile_path_for_date(project_root: Path, report_date: str) -> Path | None:
    p = project_root / EDGE_DIR / f"{report_date}-edge-profiles.json"
    if p.is_file():
        return p
    return latest_glob_path(project_root / EDGE_DIR, "*-edge-profiles.json")


def backtest_summary_latest(project_root: Path) -> Path | None:
    return latest_glob_path(project_root / BACKTEST_INTRADAY_DIR, "*-backtest-summary.json")


def daterange_inclusive(start: date, end: date) -> list[date]:
    from datetime import timedelta

    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur = cur + timedelta(days=1)
    return out


def write_report_json_md(
    out_dir: Path, stem: str, payload: dict[str, Any], md_text: str
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jp = out_dir / f"{stem}.json"
    mp = out_dir / f"{stem}.md"
    jp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    mp.write_text(md_text, encoding="utf-8")
    return jp, mp
