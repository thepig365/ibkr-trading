"""Settings/dashboard listener panel uses file/launchd only (no outbound Telegram)."""

from __future__ import annotations

from pathlib import Path

from bot.telegram_listener_ui import build_telegram_listener_ui_context


def test_listener_ui_context_has_paths(tmp_project: Path) -> None:
    ctx = build_telegram_listener_ui_context(tmp_project)
    assert ctx.get("state_relpath")
    assert "update_offset" in ctx
    assert isinstance(ctx.get("launchd_plist_installed"), bool)
    assert isinstance(ctx.get("running_hint"), bool)
