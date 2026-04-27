"""Full-auto Telegram blockers: dedup and formatting (no real sends in tests)."""

from __future__ import annotations

import json
from pathlib import Path

from bot.full_auto_telegram import (
    DEDUP_RELPATH,
    format_blocker_telegram,
    should_send_blocker_now,
    record_blocker_sent,
    BLOCKER_TWS_NOT_LISTENING,
)


def test_format_blocker_includes_action_and_reason() -> None:
    t = format_blocker_telegram(
        blocker_code=BLOCKER_TWS_NOT_LISTENING,
        detail="x",
        ny_hhmm="09:46",
    )
    assert "Paper engine blocked" in t
    assert "tws_not_listening" in t
    assert "Action needed" in t


def test_blocker_dedup_respects_window(tmp_path: Path) -> None:
    root = tmp_path
    (root / DEDUP_RELPATH).parent.mkdir(parents=True, exist_ok=True)
    assert should_send_blocker_now(root, blocker_code="x", detail="a", dedup_minutes=120) is True
    record_blocker_sent(root, blocker_code="x", detail="a")
    ded = root / DEDUP_RELPATH
    data = json.loads(ded.read_text(encoding="utf-8"))
    assert "blockers" in data
    assert should_send_blocker_now(root, blocker_code="x", detail="a", dedup_minutes=120) is False
