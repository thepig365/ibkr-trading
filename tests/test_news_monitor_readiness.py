"""news-monitor-readiness JSON (no network)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from bot.config import load_config
from bot.reports.news_monitor_readiness import build_news_monitor_readiness

REPO = Path(__file__).resolve().parent.parent


def _install(tmp: Path) -> None:
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    for n in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
        "strategy_ui.yaml",
    ):
        s = REPO / "config" / n
        if s.is_file():
            shutil.copy(s, tmp / "config" / n)
    (tmp / "data").mkdir(parents=True, exist_ok=True)


def test_readiness_includes_silence_and_interval(tmp_path: Path) -> None:
    _install(tmp_path)
    cfg = load_config(project_root=tmp_path)
    p = build_news_monitor_readiness(tmp_path, cfg)
    assert p["send_no_news_messages"] is False
    assert p["check_interval_minutes"] == 60
    # JSON serializable
    json.dumps(p)
