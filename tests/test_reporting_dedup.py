"""Dedup store prune logic."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

from bot.reports.telegram_report_dedup import prune_old, load_dedup_map, save_dedup_map


def test_prune_old_drops_stale(tmp_path: Path) -> None:
    now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    m = {"a": old, "b": new}
    o = prune_old(m, window_hours=24, now=now)
    assert "a" not in o
    assert "b" in o
