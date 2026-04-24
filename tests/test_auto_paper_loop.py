"""Tests for MTF auto-paper loop (10H) and runtime gates."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bot.auto_paper_mtf import (
    AutoPaperMtfResult,
    is_runtime_mtf_auto_disabled_explicit,
    is_runtime_mtf_auto_enabled,
)
from bot.auto_paper_loop import run_auto_paper_mtf_loop
from bot.config import load_config
from bot.journal import Journal
from bot.telegram_commands import Dispatcher, is_unsafe_command, load_command_config


def test_loop_skips_when_kill_switch_active(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_project / "data" / "KILL_SWITCH").write_text("on\n", encoding="utf-8")
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    sub_calls: list[list[str]] = []
    monkeypatch.setattr(
        "bot.auto_paper_loop._sub_cli",
        lambda c, a: (sub_calls.append(a), 0)[1],
    )
    run_ap = MagicMock()
    monkeypatch.setattr("bot.auto_paper_loop.run_auto_paper_mtf", run_ap)
    run_auto_paper_mtf_loop(
        cfg,
        journal,
        once=True,
        telegram=False,
        market_hours_only=False,
        interval_minutes=1,
        sleep_fn=lambda s: None,
    )
    run_ap.assert_not_called()
    assert not sub_calls


def test_loop_skips_outside_rth(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    monkeypatch.setattr(
        "bot.auto_paper_loop.us_rth_allows_new_entries", lambda: (False, "test closed")
    )
    run_ap = MagicMock()
    monkeypatch.setattr("bot.auto_paper_loop.run_auto_paper_mtf", run_ap)
    sub_calls: list[list[str]] = []
    monkeypatch.setattr(
        "bot.auto_paper_loop._sub_cli",
        lambda c, a: (sub_calls.append(a), 0)[1],
    )
    run_auto_paper_mtf_loop(
        cfg,
        journal,
        once=True,
        telegram=False,
        market_hours_only=True,
        interval_minutes=1,
        sleep_fn=lambda s: None,
    )
    run_ap.assert_not_called()
    assert not sub_calls


def test_loop_does_not_submit_when_full_zero_mocked(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_project / "data" / "runtime").mkdir(parents=True, exist_ok=True)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    monkeypatch.setattr("bot.auto_paper_loop._sub_cli", lambda c, a: 0)
    fake = AutoPaperMtfResult(
        True,
        "ok",
        summary={
            "counts": {"FULL_ALIGNMENT": 0},
            "eligible_for_future_paper_trade": [],
            "mtf_paper_bracket_runs": [],
        },
        preflight={"paper_account": "paper ok"},
    )
    monkeypatch.setattr("bot.auto_paper_loop.run_auto_paper_mtf", lambda *a, **k: fake)
    run_auto_paper_mtf_loop(
        cfg,
        journal,
        once=True,
        telegram=False,
        market_hours_only=False,
        interval_minutes=1,
        sleep_fn=lambda s: None,
    )
    stp = tmp_project / "data" / "runtime" / "auto_paper_loop_state.json"
    assert stp.is_file()
    st = json.loads(stp.read_text(encoding="utf-8"))
    assert st.get("last_full_alignment_count") == 0
    assert st.get("last_orders_submitted") == 0


def test_runtime_mtf_file_on_off(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    p = tmp_project / "data" / "runtime" / "mtf_auto_paper_enabled"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("1\n", encoding="utf-8")
    assert is_runtime_mtf_auto_enabled(cfg) is True
    assert is_runtime_mtf_auto_disabled_explicit(cfg) is False
    p.write_text("0\n", encoding="utf-8")
    assert is_runtime_mtf_auto_enabled(cfg) is False
    assert is_runtime_mtf_auto_disabled_explicit(cfg) is True


def test_telegram_unsafe_blocks_extra_patterns() -> None:
    assert is_unsafe_command("buy crypto on live") is True
    assert is_unsafe_command("use market order now") is True
    assert is_unsafe_command("naked call options") is True


def test_telegram_auto_mtf_on_off_respects_gates(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_telegram_commands import _FakeRunner, _setup_auth, _silence_outbound

    _setup_auth(monkeypatch)
    _silence_outbound(monkeypatch)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    runner = _FakeRunner()
    d = Dispatcher(cfg=cfg, journal=journal, ci=ci, runner=runner)
    r1 = d.run("/auto_mtf_on")
    assert (tmp_project / "data" / "runtime" / "mtf_auto_paper_enabled").read_text() == "1\n"
    assert r1.status == "success"
    r0 = d.run("/auto_mtf_off")
    assert (tmp_project / "data" / "runtime" / "mtf_auto_paper_enabled").read_text() == "0\n"
    assert r0.status == "success"


def test_telegram_kill_resume(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_telegram_commands import _FakeRunner, _setup_auth, _silence_outbound

    _setup_auth(monkeypatch)
    _silence_outbound(monkeypatch)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    d = Dispatcher(
        cfg=cfg,
        journal=journal,
        ci=ci,
        runner=_FakeRunner(),
    )
    d.run("/kill")
    assert (tmp_project / "data" / "KILL_SWITCH").is_file()
    d.run("/resume")
    assert not (tmp_project / "data" / "KILL_SWITCH").exists()


def test_launchd_template_no_secrets() -> None:
    from pathlib import Path

    p = (
        Path(__file__).resolve().parent.parent
        / "launchd"
        / "com.leon.ibkr-trading-bot.auto-paper.plist"
    )
    t = p.read_text(encoding="utf-8")
    assert "TELEGRAM" not in t
    assert "token" not in t.lower()
    assert "__PROJECT_ROOT__" in t  # install-time substitution


def test_no_live_trading_in_auto_paper_config(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    ap = cfg.settings.trading.mtf_auto_paper
    assert ap.allow_live_trading is False
    assert cfg.settings.account.block_live_trading is True
