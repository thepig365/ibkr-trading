"""Read-only context for “Background Auto Runner” (Strategy Lab). No launchctl/IBKR in render."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


LAUNCHD_LABEL = "com.strategy-lab.full-auto-paper"
LAUNCHD_PLIST_NAME = f"{LAUNCHD_LABEL}.plist"


def user_launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / LAUNCHD_PLIST_NAME


def build_background_runner_ui_context(project_root: Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    la = user_launchd_plist_path()
    st_path = root / "data" / "runtime" / "full_auto_paper_supervisor_state.json"
    sup: dict[str, Any] = {}
    if st_path.is_file():
        try:
            raw = json.loads(st_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                sup = raw
        except (OSError, json.JSONDecodeError, TypeError, UnicodeDecodeError):
            sup = {}
    return {
        "launchd_plist_in_user_dir": la.is_file(),
        "launchd_plist_path_user": str(la),
        "repo_scripts_plist": str(root / "scripts" / "com.strategy-lab.full-auto-paper.plist"),
        "log_appended_supervisor": str(root / "logs" / "full_auto_paper_supervisor.log"),
        "log_launchd_stdout": str(root / "logs" / "launchd_full_auto.out.log"),
        "log_launchd_stderr": str(root / "logs" / "launchd_full_auto.err.log"),
        "lock_file": str(root / "data" / "runtime" / "full_auto_paper_supervisor.lock"),
        "last_supervisor_state": sup,
    }


__all__ = [
    "LAUNCHD_LABEL",
    "build_background_runner_ui_context",
    "user_launchd_plist_path",
]
