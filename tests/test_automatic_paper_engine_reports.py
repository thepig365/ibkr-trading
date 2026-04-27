"""Report wiring for automatic paper engine exit (file-based, no email)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.automatic_paper_engine import _engine_post_exit_report
from bot.config import load_config


def test_engine_post_exit_writes_paper_daily_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    # Minimal tree: use tests' tmp_project style via conftest
    (tmp_path / "config").mkdir()
    (tmp_path / "data" / "reports" / "paper").mkdir(parents=True)
    import shutil

    REPO = Path(__file__).resolve().parent.parent
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
    ):
        shutil.copy(REPO / "config" / name, tmp_path / "config" / name)
    cfg = load_config(project_root=tmp_path)
    out = _engine_post_exit_report(cfg, telegram=False, had_activity=False)
    p = Path(out["json_path"])
    assert p.is_file()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
