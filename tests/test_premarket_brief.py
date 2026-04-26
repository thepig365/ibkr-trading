"""Pre-market brief: build, dedupe, partial providers, no crash."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

from bot.config import load_config
from bot.news.providers.base import NewsHeadline
from bot.news.providers.registry import dedupe_headlines
from bot.premarket.brief import build_premarket_brief
from bot.premarket.storage import find_latest_premarket_brief

REPO = Path(__file__).resolve().parent.parent


def test_dedupe_headlines_removes_duplicates() -> None:
    a = NewsHeadline(title="Hello", url="http://x", source="a")
    b = NewsHeadline(title="Hello", url="http://x", source="b")
    c = NewsHeadline(title="Other", url="", source="c")
    out = dedupe_headlines([a, b, c])
    assert len(out) == 2


@patch.dict(os.environ, {"FINNHUB_API_KEY": "", "FMP_API_KEY": ""}, clear=False)
def test_build_premarket_brief_no_network_keys_uses_macro_yaml() -> None:
    cfg = load_config(project_root=REPO)
    data = build_premarket_brief(cfg, trading_day=date(2026, 1, 15), email=False)
    assert data.date_ny == "2026-01-15"
    assert isinstance(data.summary_lines, list)


def test_find_latest_premarket_brief(tmp_path: Path) -> None:
    d = tmp_path / "data" / "premarket_briefs"
    d.mkdir(parents=True)
    p = d / "2026-04-25-premarket-brief.json"
    p.write_text(
        json.dumps({"date_ny": "2026-04-25", "headlines": []}),
        encoding="utf-8",
    )
    j = find_latest_premarket_brief(tmp_path)
    assert j and j.get("date_ny") == "2026-04-25"
