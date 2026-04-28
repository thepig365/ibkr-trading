"""Sanity-check macOS *.command launchers at repo root (no execution)."""

from __future__ import annotations

from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


_COMMAND_FILES = (
    "Start Strategy Lab.command",
    "Stop Strategy Lab.command",
    "Open Strategy Lab.command",
    "Open Strategy Lab Dashboard.command",
    "Strategy Lab.command",
)


def test_command_files_exist_and_repo_relative_root() -> None:
    root = _root()
    for name in ("Start Strategy Lab.command", "Stop Strategy Lab.command"):
        p = root / name
        assert p.is_file(), name
        text = p.read_text(encoding="utf-8")
        assert 'ROOT="$(cd "$(dirname "$0")" && pwd)"' in text
        assert "Documents/Claude" not in text
        assert "/tradingstrategies/ibkr-trading-bot" not in text


def test_start_stop_no_trading_engine_invocation() -> None:
    root = _root()
    start = (root / "Start Strategy Lab.command").read_text(encoding="utf-8")
    stop = (root / "Stop Strategy Lab.command").read_text(encoding="utf-8")
    banned = (
        "run-automatic-paper-engine",
        "run-full-auto-paper-supervisor",
        "first-paper-pass",
        "intraday-paper-on",
        "live_trading",
        "MARKET_ORDER",
    )
    for b in banned:
        assert b not in start.lower() and b not in stop.lower(), b


def test_open_dashboard_only_opens_url_no_start_script_path() -> None:
    root = _root()
    p = root / "Open Strategy Lab Dashboard.command"
    assert p.is_file()
    txt = p.read_text(encoding="utf-8")
    assert "127.0.0.1" in txt or "STRATEGY_LAB_" in txt
    assert "/dashboard" in txt
    assert "start_strategy_lab_ui.sh" not in txt
    assert "Documents/Claude" not in txt


def test_all_tracked_launchers_eschew_old_documents_path() -> None:
    root = _root()
    snippet = "/Documents/Claude Folders/"
    snippet2 = "tradingstrategies/ibkr-trading-bot"
    for name in _COMMAND_FILES:
        p = root / name
        if not p.is_file():
            continue
        blob = p.read_text(encoding="utf-8")
        assert snippet not in blob and snippet2 not in blob, name
