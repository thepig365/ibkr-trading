"""automatic_paper_engine report-on-exit → complete_trade_charts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from bot.automatic_paper_engine import _engine_post_exit_report
from bot.config import load_config


def _minimal_project(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parent.parent
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
    ):
        shutil.copy(repo / "config" / name, tmp_path / "config" / name)
    (tmp_path / "data" / "reports" / "paper").mkdir(parents=True, exist_ok=True)


def test_post_exit_invokes_complete_trade_charts_with_fetch_and_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    _minimal_project(tmp_path)
    cfg = load_config(project_root=tmp_path)

    captured: dict[str, object] = {}

    def fake_complete(*args, **kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return {"available_count": 0, "generated_count": 0, "mode": "ibkr_readonly_fetch"}

    with patch("bot.trade_chart_completion.complete_trade_charts", side_effect=fake_complete):
        _engine_post_exit_report(cfg, telegram=False, had_activity=False)

    assert captured.get("fetch_missing_candles") is True
    assert captured.get("before_mins") == int(cfg.settings.trading.trade_charts.candle_window_before_minutes)
    assert captured.get("after_mins") == int(cfg.settings.trading.trade_charts.candle_window_after_minutes)


def test_post_exit_chart_failure_is_soft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    _minimal_project(tmp_path)
    cfg = load_config(project_root=tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("simulated ibkr offline")

    with patch("bot.trade_chart_completion.complete_trade_charts", side_effect=boom):
        out = _engine_post_exit_report(cfg, telegram=False, had_activity=False)

    assert out.get("trade_charts_batch", {}).get("error") == "batch_unavailable"
    assert Path(out["json_path"]).is_file()
    data = json.loads(Path(out["json_path"]).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
