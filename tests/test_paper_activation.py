"""Tests for paper-activation CLI and settings.local writer (Prompt 13I)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from bot.config import load_config
from bot.execution.intraday_paper_execution import INTRADAY_AUTO_PAPER_ENABLED_RELPATH
from bot.paper_activation import (
    PAPER_LOCAL_PATCH,
    build_paper_activation_status,
    propose_local_settings_merged_with_existing,
    run_paper_readiness_check,
    set_intraday_runtime_flag,
    write_paper_local_config_file,
)

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


def test_paper_activation_status_without_ibkr(tmp_path: Path) -> None:
    _install_default_config(tmp_path)
    (tmp_path / "data" / "runtime").mkdir(parents=True, exist_ok=True)
    cfg = load_config(project_root=tmp_path)
    st = build_paper_activation_status(cfg, probe_ibkr=False, journal=None)
    assert st.get("settings_local_yaml_exists") is False
    assert st.get("paper_only") is True
    assert st.get("final_readiness") == "NOT_READY"
    assert st.get("probe_ibkr") is False


def test_write_dry_run_does_not_create_file(tmp_path: Path) -> None:
    _install_default_config(tmp_path)
    local = tmp_path / "config" / "settings.local.yaml"
    r = write_paper_local_config_file(tmp_path, dry_run=True, write=False)
    assert r.get("ok") is True
    assert r.get("wrote") is False
    assert not local.is_file()
    assert "account:" in (r.get("proposed_yaml") or "")


def test_write_creates_file_and_backup(tmp_path: Path) -> None:
    _install_default_config(tmp_path)
    local = tmp_path / "config" / "settings.local.yaml"
    local.write_text("trading:\n  enabled: true\n", encoding="utf-8")
    r = write_paper_local_config_file(tmp_path, dry_run=False, write=True)
    assert r.get("ok") is True
    assert r.get("wrote") is True
    assert r.get("backup_path")
    assert local.is_file()
    merged, _ = propose_local_settings_merged_with_existing(tmp_path)
    assert merged.get("trading", {}).get("enabled") is True
    assert PAPER_LOCAL_PATCH["account"]["mode"] in ("paper",) or merged.get("account", {}).get("mode") == "paper"


def test_write_refuses_unsafe_proposed_merge(tmp_path: Path) -> None:
    _install_default_config(tmp_path)
    with patch(
        "bot.paper_activation.propose_local_settings_merged_with_existing",
        return_value=(
            {},
            {"account": {"mode": "live", "block_live_trading": True}, "trading": {"intraday_paper": {}}},
        ),
    ):
        r = write_paper_local_config_file(tmp_path, dry_run=False, write=True)
    assert r.get("ok") is False


def test_intraday_paper_on_off_writes_flag(tmp_path: Path) -> None:
    _install_default_config(tmp_path)
    cfg = load_config(project_root=tmp_path)
    p = set_intraday_runtime_flag(cfg, on=True)
    assert p.name == "intraday_auto_paper_enabled"
    assert "1" in p.read_text()
    p2 = set_intraday_runtime_flag(cfg, on=False)
    assert "0" in p2.read_text()


def test_paper_readiness_fails_with_kill_switch(tmp_path: Path) -> None:
    _install_default_config(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "KILL_SWITCH").write_text("t\n", encoding="utf-8")
    (tmp_path / "data" / "intraday_smc").mkdir(parents=True, exist_ok=True)
    summary = {
        "date": "2026-01-01",
        "ready_strict_symbols": ["AAPL"],
        "ready_aggressive_symbols": [],
        "watch_symbols": [],
    }
    (tmp_path / "data" / "intraday_smc" / "2026-01-01-watchlist-intraday-smc-summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    cfg = load_config(project_root=tmp_path)
    from bot.journal import Journal

    rr = run_paper_readiness_check(
        cfg, Journal(cfg), intraday=True, probe_ibkr=False, run_scan=False, source="dynamic", limit=5
    )
    assert rr.passed is False
    assert any("kill" in x.lower() for x in rr.blocking_reasons)


def test_paper_readiness_fails_no_scan_summary(tmp_path: Path) -> None:
    _install_default_config(tmp_path)
    (tmp_path / "data" / "runtime").mkdir(parents=True, exist_ok=True)
    flag = tmp_path / INTRADAY_AUTO_PAPER_ENABLED_RELPATH
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("1\n", encoding="utf-8")
    cfg = load_config(project_root=tmp_path)
    from bot.journal import Journal

    rr = run_paper_readiness_check(
        cfg, Journal(cfg), intraday=True, probe_ibkr=False, run_scan=False, source="dynamic", limit=5
    )
    assert not rr.passed
    assert any("summary" in x.lower() or "intraday" in x.lower() for x in rr.blocking_reasons)


def test_settings_local_stays_gitignored() -> None:
    p = subprocess.run(
        ["git", "check-ignore", "-q", "config/settings.local.yaml"],
        cwd=str(REPO),
        capture_output=True,
    )
    assert p.returncode == 0


def test_cli_paper_activation_status_subprocess(tmp_path: Path) -> None:
    _install_default_config(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO)
    env["IBKR_TRADING_PROJECT_ROOT"] = str(tmp_path)
    p = subprocess.run(
        [sys.executable, "-m", "bot.cli", "paper-activation-status"],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert p.returncode == 0
    assert "NOT_READY" in p.stdout
