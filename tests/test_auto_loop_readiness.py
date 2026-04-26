"""Read-only auto-loop-readiness (13L.1-PREP) — no loop, no orders."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from bot.auto_loop_readiness import build_auto_loop_readiness
from bot.config import load_config
from bot.execution.intraday_paper_execution import KILL_SWITCH_RELPATH
from bot.strategy_ui.selection import StrategySelectionState

REPO = Path(__file__).resolve().parent.parent


def _install_config(target: Path) -> None:
    (target / "config").mkdir(parents=True, exist_ok=True)
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
        "strategy_ui.yaml",
    ):
        src = REPO / "config" / name
        if src.is_file():
            shutil.copy(src, target / "config" / name)


def _runtime_flags(tmp: Path) -> None:
    r = tmp / "data" / "runtime"
    r.mkdir(parents=True, exist_ok=True)
    (r / "intraday_auto_paper_enabled").write_text("1\n", encoding="utf-8")


def test_module_does_not_import_intraday_loop() -> None:
    src = (REPO / "bot" / "auto_loop_readiness.py").read_text(encoding="utf-8")
    assert "auto_paper_intraday_loop" not in src
    assert "run_auto_paper_intraday_loop" not in src
    assert "auto_paper_mtf" not in src
    assert "ny_session_windows" in src


def test_build_runs_read_only_completes(tmp_path: Path) -> None:
    """Sanity: readiness aggregation runs without side effects; does not start the loop."""
    _install_config(tmp_path)
    _runtime_flags(tmp_path)
    (tmp_path / "data" / "runtime" / "selected_strategy.json").write_text(
        json.dumps({"active_paper_strategy": "ict_smc_intraday_v1"}),
        encoding="utf-8",
    )
    cfg = load_config(project_root=tmp_path)
    with (
        patch("bot.auto_loop_readiness.ledger_snapshot_for_status") as m_led,
        patch("bot.auto_loop_readiness.build_paper_activation_status") as m_pa,
    ):
        m_led.return_value = {"daily_remaining_notional_usd": 5_000.0}
        m_pa.return_value = {
            "final_readiness": "READY_FOR_PAPER_TEST",
            "blocking_reasons": [],
        }
        r = build_auto_loop_readiness(tmp_path, cfg, None, probe_ibkr=False)
    assert m_led.called
    assert m_pa.called
    assert "next_safe_action" in r
    assert "commands" in r
    assert r.get("morning_session_supported") is True
    assert r.get("morning_window_start_ny") == "09:45"
    assert r.get("morning_window_end_ny") == "11:30"
    assert "morning_next_safe_action" in r
    assert "morning_readiness" in r


def test_kill_switch_gives_not_ready_and_action(tmp_path: Path) -> None:
    _install_config(tmp_path)
    _runtime_flags(tmp_path)
    p = tmp_path / KILL_SWITCH_RELPATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("stop\n", encoding="utf-8")
    cfg = load_config(project_root=tmp_path)
    r = build_auto_loop_readiness(tmp_path, cfg, None, probe_ibkr=False)
    assert r["readiness"] == "Not ready"
    assert r["next_safe_action"] == "kill_switch_active"
    assert r["kill_switch"] is True
    assert r["morning_next_safe_action"] == "kill_switch_active"
    assert r["morning_readiness"] == "Not ready"


def test_daily_budget_zero_wait_for_budget(tmp_path: Path) -> None:
    _install_config(tmp_path)
    _runtime_flags(tmp_path)
    (tmp_path / "data" / "runtime").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "runtime" / "selected_strategy.json").write_text(
        json.dumps({"active_paper_strategy": "ict_smc_intraday_v1"}),
        encoding="utf-8",
    )
    cfg = load_config(project_root=tmp_path)
    with patch(
        "bot.auto_loop_readiness.ledger_snapshot_for_status",
        return_value={"daily_remaining_notional_usd": 0.0},
    ):
        with patch(
            "bot.auto_loop_readiness.build_paper_activation_status",
            return_value={"final_readiness": "READY_FOR_PAPER_TEST"},
        ):
            r = build_auto_loop_readiness(tmp_path, cfg, None, probe_ibkr=False)
    assert r["next_safe_action"] == "wait_for_daily_budget"
    assert r["readiness"] == "Not ready"
    assert r["morning_next_safe_action"] == "wait_for_daily_budget"


def test_non_ict_paper_selection_not_ready(tmp_path: Path) -> None:
    _install_config(tmp_path)
    _runtime_flags(tmp_path)
    cfg = load_config(project_root=tmp_path)

    def _fake_sel(
        _root: Path, *, catalog: object | None = None
    ) -> StrategySelectionState:
        return StrategySelectionState(active_paper_strategy="mtf_smc")

    with (
        patch("bot.auto_loop_readiness.load_strategy_selection", _fake_sel),
        patch(
            "bot.auto_loop_readiness.build_paper_activation_status",
            return_value={"final_readiness": "READY_FOR_PAPER_TEST"},
        ),
    ):
        r = build_auto_loop_readiness(tmp_path, cfg, None, probe_ibkr=False)
    assert r["next_safe_action"] == "paper_strategy_not_enabled"
    assert r["ict_paper_path_ok"] is False


def test_live_trading_allowed_marks_unsafe_config(tmp_path: Path) -> None:
    _install_config(tmp_path)
    _runtime_flags(tmp_path)
    cfg = load_config(project_root=tmp_path)
    ip = cfg.settings.trading.intraday_paper
    bad_ip = ip.model_copy(update={"live_trading_allowed": True})
    t = cfg.settings.trading.model_copy(update={"intraday_paper": bad_ip})
    cfg2 = cfg.model_copy(update={"settings": cfg.settings.model_copy(update={"trading": t})})
    with patch(
        "bot.auto_loop_readiness.build_paper_activation_status",
        return_value={"final_readiness": "READY_FOR_PAPER_TEST"},
    ):
        r = build_auto_loop_readiness(tmp_path, cfg2, None, probe_ibkr=False)
    assert r["config_invariants_ok"] is False
    assert r["next_safe_action"] == "unsafe_config"


def test_market_orders_allowed_marks_unsafe_config(tmp_path: Path) -> None:
    _install_config(tmp_path)
    _runtime_flags(tmp_path)
    cfg = load_config(project_root=tmp_path)
    ip = cfg.settings.trading.intraday_paper
    bad_ip = ip.model_copy(update={"market_orders_allowed": True})
    t = cfg.settings.trading.model_copy(update={"intraday_paper": bad_ip})
    cfg2 = cfg.model_copy(update={"settings": cfg.settings.model_copy(update={"trading": t})})
    with patch(
        "bot.auto_loop_readiness.build_paper_activation_status",
        return_value={"final_readiness": "READY_FOR_PAPER_TEST"},
    ):
        r = build_auto_loop_readiness(tmp_path, cfg2, None, probe_ibkr=False)
    assert r["config_invariants_ok"] is False
    assert r["next_safe_action"] == "unsafe_config"


def test_reconcile_fail_when_required_fixes_action(tmp_path: Path) -> None:
    _install_config(tmp_path)
    _runtime_flags(tmp_path)
    (tmp_path / "data" / "runtime" / "selected_strategy.json").write_text(
        json.dumps({"active_paper_strategy": "ict_smc_intraday_v1"}),
        encoding="utf-8",
    )
    st = tmp_path / "data" / "runtime" / "intraday_auto_paper_loop_state.json"
    st.write_text(
        json.dumps({"reconciliation_status": "failed"}), encoding="utf-8"
    )
    cfg = load_config(project_root=tmp_path)
    with patch(
        "bot.auto_loop_readiness.build_paper_activation_status",
        return_value={"final_readiness": "READY_FOR_PAPER_TEST"},
    ):
        r = build_auto_loop_readiness(tmp_path, cfg, None, probe_ibkr=False)
    assert r["next_safe_action"] == "fix_reconcile"


def test_cli_auto_loop_readiness_does_not_start_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_config(tmp_path)
    _runtime_flags(tmp_path)
    (tmp_path / "data" / "runtime" / "selected_strategy.json").write_text(
        json.dumps({"active_paper_strategy": "ict_smc_intraday_v1"}),
        encoding="utf-8"
    )
    env = {**os.environ, "IBKR_TRADING_PROJECT_ROOT": str(tmp_path)}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bot.cli",
            "auto-loop-readiness",
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)
    assert "readiness" in body
    # optional probe default off
    assert body.get("tws", {}).get("probed") is False


def test_cli_probe_flag_sets_probed_in_json(tmp_path: Path) -> None:
    _install_config(tmp_path)
    _runtime_flags(tmp_path)
    (tmp_path / "data" / "runtime" / "selected_strategy.json").write_text(
        json.dumps({"active_paper_strategy": "ict_smc_intraday_v1"}),
        encoding="utf-8"
    )
    env = {**os.environ, "IBKR_TRADING_PROJECT_ROOT": str(tmp_path)}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bot.cli",
            "auto-loop-readiness",
            "--json",
            "--probe-ibkr",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)
    assert body.get("tws", {}).get("probed") is True
