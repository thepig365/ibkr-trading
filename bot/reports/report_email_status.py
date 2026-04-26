"""Persist last report email outcomes for the Strategy Lab UI (no secrets)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS_RELPATH = "data/runtime/report_email_status.json"


@dataclass
class LastEmailInfo:
    status: str = "never"
    at_utc: str = ""
    to_addr: str = ""
    report_key: str = ""
    detail: str = ""


@dataclass
class ReportEmailStatusView:
    by_report: dict[str, LastEmailInfo] = field(default_factory=dict)
    resend_configured: bool = False
    from_env: str = ""


def _path(root: Path) -> Path:
    p = (root / STATUS_RELPATH).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_report_email_status(
    project_root: Path, *, resend_key_present: bool, from_addr: str
) -> ReportEmailStatusView:
    p = _path(project_root)
    by_report: dict[str, LastEmailInfo] = {}
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            raw = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                if not isinstance(v, dict):
                    continue
                by_report[k] = LastEmailInfo(
                    status=str(v.get("status") or "unknown"),
                    at_utc=str(v.get("at_utc") or ""),
                    to_addr=str(v.get("to_addr") or ""),
                    report_key=str(v.get("report_key") or k),
                    detail=str(v.get("detail") or ""),
                )
    return ReportEmailStatusView(
        by_report=by_report,
        resend_configured=resend_key_present,
        from_env=from_addr,
    )


def record_email_outcome(
    project_root: Path,
    key: str,
    *,
    status: str,
    to_addr: str,
    report_key: str = "",
    detail: str = "",
) -> None:
    p = _path(project_root)
    cur: dict[str, Any] = {}
    if p.is_file():
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            cur = {}
    if not isinstance(cur, dict):
        cur = {}
    cur[key] = {
        "status": status,
        "at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to_addr": to_addr,
        "report_key": report_key or key,
        "detail": detail[:500],
    }
    p.write_text(json.dumps(cur, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
