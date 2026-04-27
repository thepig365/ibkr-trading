"""Background runner UI context (read-only, no launchctl/IBKR)."""

from __future__ import annotations

from pathlib import Path

from bot.launchd_full_auto_ui import build_background_runner_ui_context

REPO = Path(__file__).resolve().parents[1]


def test_build_background_context_keys() -> None:
    c = build_background_runner_ui_context(REPO)
    assert "launchd_plist_in_user_dir" in c
    assert "log_appended_supervisor" in c
    assert isinstance(c.get("last_supervisor_state"), dict)
