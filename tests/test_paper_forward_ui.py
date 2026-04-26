"""UI templates: no auto loop start; morning check only."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_paper_html_no_command_form_for_auto_loop() -> None:
    t = (REPO / "bot_ui" / "templates" / "paper.html").read_text(encoding="utf-8")
    assert "command='run-auto-paper-intraday-loop'" not in t
    assert 'command="run-auto-paper-intraday-loop"' not in t
