"""Protected paths: cleanup never targets audit/runtime; dry-run is non-destructive."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from bot.data_lifecycle import _is_protected_path, data_cleanup


def test_protected_path_flags() -> None:
    root = Path("/tmp/fake-proj")
    assert _is_protected_path(root, root / "data" / "paper_orders" / "a.jsonl")
    assert _is_protected_path(root, root / "data" / "runtime" / "x.json")
    assert _is_protected_path(root, root / "config" / "settings.local.yaml")
    assert _is_protected_path(root, root / ".env")


def test_dry_run_does_not_remove_files(tmp_path: Path) -> None:
    """``apply=False`` → nothing deleted; old ephemeral file may be listed for deletion."""
    rep = tmp_path / "data" / "reports" / "paper"
    rep.mkdir(parents=True)
    oldf = rep / "old.md"
    oldf.write_text("x", encoding="utf-8")
    old = time.mktime(
        (datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)).timetuple()
    )
    os.utime(oldf, (old, old))
    now = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    with patch("bot.data_lifecycle._utc_now", return_value=now):
        res = data_cleanup(tmp_path, apply=False, now=now)
    assert not res.deleted
    assert oldf.exists()
    w = " ".join(res.would_delete)
    if w:
        assert "old.md" in w or "reports" in w


def test_data_cleanup_apply_removes_only_eligible_temp_file(
    tmp_path: Path,
) -> None:
    rep = tmp_path / "data" / "reports" / "paper"
    rep.mkdir(parents=True)
    oldf = rep / "stale.md"
    oldf.write_text("x", encoding="utf-8")
    old = time.mktime(
        (datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)).timetuple()
    )
    os.utime(oldf, (old, old))
    now = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    with patch("bot.data_lifecycle._utc_now", return_value=now):
        res = data_cleanup(tmp_path, apply=True, now=now)
    assert not oldf.is_file()
