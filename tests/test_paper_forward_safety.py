"""Paper forward-test safety: allowlist and forbidden loop commands."""

from __future__ import annotations

from bot_ui.services.safety import ALLOWED_COMMANDS, is_allowed


def test_auto_intraday_loop_not_exposed_in_ui() -> None:
    assert is_allowed("run-auto-paper-intraday-loop") is False
    assert "run-auto-paper-intraday-loop" not in ALLOWED_COMMANDS
