"""Tests for first-paper-pass wrapper (Prompt 13I)."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from bot.config import load_config
from bot.journal import Journal
from bot.paper_activation import run_first_paper_pass

REPO = Path(__file__).resolve().parent.parent


def _install_default_config(target: Path) -> None:
    (target / "config").mkdir(parents=True, exist_ok=True)
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
    ):
        src = REPO / "config" / name
        if src.is_file():
            shutil.copy(src, target / "config" / name)


def test_first_paper_pass_skips_when_activation_not_ready(tmp_path: Path) -> None:
    _install_default_config(tmp_path)
    cfg = load_config(project_root=tmp_path)
    j = Journal(cfg)
    out = run_first_paper_pass(cfg, j, source="dynamic", limit=5, telegram=False)
    assert out.get("result") == "skipped"
    assert out.get("paper_activation_status", {}).get("final_readiness") == "NOT_READY"


def test_first_paper_pass_does_not_call_intraday_when_readiness_fails(
    tmp_path: Path,
) -> None:
    _install_default_config(tmp_path)
    (tmp_path / "data" / "KILL_SWITCH").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "KILL_SWITCH").write_text("1\n", encoding="utf-8")
    cfg = load_config(project_root=tmp_path)
    j = Journal(cfg)
    with patch("bot.execution.intraday_paper_execution.run_intraday_paper_pass") as run_p:
        out = run_first_paper_pass(cfg, j, source="dynamic", limit=5, telegram=False)
    run_p.assert_not_called()
    assert out.get("result") == "skipped"
