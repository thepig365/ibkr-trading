"""Read-only UI context for Telegram command listener (no Telegram API calls)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .telegram_listener_state import DEFAULT_RELPATH, load_state, state_path_for

LISTENER_LABEL = "com.strategy-lab.telegram-listener"


def _launchctl_has_label(label: str) -> bool:
    try:
        r = subprocess.run(
            ["/bin/launchctl", "list"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if r.returncode != 0 or not r.stdout:
        return False
    return any(label in line for line in r.stdout.splitlines())


def build_telegram_listener_ui_context(project_root: Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    home = Path.home()
    plist = home / "Library" / "LaunchAgents" / f"{LISTENER_LABEL}.plist"
    wrap = home / "Library" / "Application Support" / "StrategyLab" / "run_telegram_command_listener.sh"
    st_path = state_path_for(root)
    st = load_state(st_path)
    offset = st.update_offset
    snap: dict[str, Any] = {}
    if st_path.is_file():
        try:
            snap = json.loads(st_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, UnicodeDecodeError):
            snap = {}
    return {
        "listener_label": LISTENER_LABEL,
        "state_relpath": DEFAULT_RELPATH,
        "state_path": str(st_path),
        "update_offset": offset,
        "saved_utc": snap.get("saved_utc"),
        "launchd_plist_installed": plist.is_file(),
        "launchd_plist_path": str(plist),
        "wrapper_installed": wrap.is_file(),
        "wrapper_path": str(wrap),
        "library_log_telegram": str(home / "Library" / "Logs" / "StrategyLab" / "telegram_command_listener.log"),
        "library_out": str(home / "Library" / "Logs" / "StrategyLab" / "telegram_listener.out.log"),
        "library_err": str(home / "Library" / "Logs" / "StrategyLab" / "telegram_listener.err.log"),
        "running_hint": _launchctl_has_label(LISTENER_LABEL) if plist.is_file() else False,
    }


__all__ = ["build_telegram_listener_ui_context", "LISTENER_LABEL"]
