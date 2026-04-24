"""Shared pytest fixtures.

Each test gets a fresh temporary project root so SQLite/JSONL writes
do not pollute the real `data/` directory.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def tmp_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Copy the real config files into the temp project so tests exercise
    # the real defaults but write to an isolated data directory.
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "memory").mkdir()
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
    ):
        shutil.copy(REPO_ROOT / "config" / name, tmp_path / "config" / name)

    # Ensure environment is clean.
    for var in (
        "IBKR_HOST", "IBKR_PORT", "IBKR_CLIENT_ID", "IBKR_ACCOUNT_MODE",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "PERPLEXITY_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _write_yaml(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


@pytest.fixture
def write_yaml():
    return _write_yaml
