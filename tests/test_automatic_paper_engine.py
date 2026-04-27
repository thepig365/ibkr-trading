"""Unit tests for automatic paper engine preflight and wiring (no IBKR)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from bot.automatic_paper_engine import run_automatic_paper_engine
from bot.automatic_paper_preflight import (
    REF_MAX_DAILY_NOTIONAL_USD,
    REF_MAX_NOTIONAL_PER_ORDER_USD,
    build_automatic_paper_engine_preflight,
)
from bot.config import load_config
from bot.journal import Journal

REPO = Path(__file__).resolve().parent.parent


def _prep_root(
    tmp_path: Path,
    *,
    intraday_enabled: bool,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Path:
    if monkeypatch is not None:
        # load_config skips settings.local.yaml when PYTEST_VERSION is set.
        monkeypatch.delenv("PYTEST_VERSION", raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "runtime").mkdir(parents=True)
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
    ):
        shutil.copy(REPO / "config" / name, tmp_path / "config" / name)
    shutil.copy(REPO / "config" / "strategy_ui.yaml", tmp_path / "config" / "strategy_ui.yaml")
    local = {
        "trading": {
            "intraday_paper": {
                "enabled": intraday_enabled,
                "dry_run": False,
                "allow_shorting": True,
            }
        }
    }
    with (tmp_path / "config" / "settings.local.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(local, f, sort_keys=False)
    (tmp_path / "data" / "runtime" / "intraday_auto_paper_loop_state.json").write_text(
        json.dumps({"reconciliation_status": "passed"}),
        encoding="utf-8",
    )
    (tmp_path / "data" / "runtime" / "selected_strategy.json").write_text(
        json.dumps({"active_paper_strategy": "ict_smc_intraday_v1"}),
        encoding="utf-8",
    )
    return tmp_path


def test_caps_constants_match_reference() -> None:
    assert REF_MAX_NOTIONAL_PER_ORDER_USD == 10_000.0
    assert REF_MAX_DAILY_NOTIONAL_USD == 100_000.0


def test_preflight_flags_intraday_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prep_root(tmp_path, intraday_enabled=False, monkeypatch=monkeypatch)
    cfg = load_config(project_root=root)
    out = build_automatic_paper_engine_preflight(cfg, None, probe_ibkr=False)
    assert out["ok"] is False
    assert any("intraday_paper.enabled" in b for b in out["blockers"])


def test_preflight_ok_when_enabled_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prep_root(tmp_path, intraday_enabled=True, monkeypatch=monkeypatch)
    cfg = load_config(project_root=root)
    out = build_automatic_paper_engine_preflight(cfg, None, probe_ibkr=False)
    assert out["ok"] is True, out["blockers"]


def test_run_engine_dry_run_no_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prep_root(tmp_path, intraday_enabled=True, monkeypatch=monkeypatch)
    cfg = load_config(project_root=root)
    j = Journal(cfg)
    out = run_automatic_paper_engine(
        cfg,
        j,
        dry_run=True,
        preflight_probe_ibkr=False,
    )
    assert out["dry_run"] is True
    assert out["finished"] is True
    assert not out.get("blockers")


def test_run_engine_refuses_config_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prep_root(tmp_path, intraday_enabled=True, monkeypatch=monkeypatch)
    cfg = load_config(project_root=root)
    cfg.settings.trading.intraday_paper.dry_run = True  # type: ignore[misc]
    j = Journal(cfg)
    out = run_automatic_paper_engine(cfg, j, dry_run=False, preflight_probe_ibkr=False)
    assert out.get("blockers")


def test_engine_max_cycles_runs_loop_mocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prep_root(tmp_path, intraday_enabled=True, monkeypatch=monkeypatch)
    cfg = load_config(project_root=root)
    j = Journal(cfg)

    def _fake_loop(*_a: object, **_kwargs: object) -> None:
        return None

        monkeypatch.setattr(
            "bot.auto_paper_intraday_loop.run_auto_paper_intraday_loop",
            _fake_loop,
        )
        monkeypatch.setattr(
            "bot.paper_activation.set_intraday_runtime_flag",
            lambda *_a, **_k: Path("/dev/null"),
        )
        monkeypatch.setattr(
            "bot.automatic_paper_engine._engine_post_exit_report",
            lambda *_a, **_k: {"json_path": "x"},
        )
    out = run_automatic_paper_engine(
        cfg,
        j,
        dry_run=False,
        max_cycles=1,
        preflight_probe_ibkr=False,
        telegram=False,
        report_on_exit=False,
    )
    assert out.get("finished")


def test_auto_loop_respects_max_cycles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from bot.auto_paper_intraday_loop import run_auto_paper_intraday_loop

    calls = {"n": 0}

    def _pass(*_a, **_k):
        calls["n"] += 1
        from bot.execution.intraday_paper_execution import IntradayPaperPassResult

        return IntradayPaperPassResult(
            timestamp_utc="t",
            paper_only=True,
            runtime_intraday_on=True,
            kill_switch=False,
            reconciliation_status="passed",
            config_enabled=True,
            fully_automatic=False,
            symbols_scanned=[],
            strict_ready_count=0,
            aggressive_ready_count=0,
            submissions=[],
            skipped_reasons=["no"],
            last_status="no_signals",
            last_reason="none",
            state_file_path="/x",
        )

    monkeypatch.setattr(
        "bot.auto_paper_intraday_loop.run_intraday_paper_pass",
        _pass,
    )
    monkeypatch.setattr(
        "bot.auto_paper_intraday_loop.is_intraday_paper_runtime_enabled",
        lambda _c: (True, False),
    )
    monkeypatch.setattr(
        "bot.auto_paper_intraday_loop._shared_is_kill_switch_active",
        lambda _c: False,
    )
    (tmp_path / "data" / "auto_paper_loop").mkdir(parents=True)
    (tmp_path / "data" / "runtime").mkdir(parents=True)
    cfg = MagicMock()
    cfg.absolute = lambda p: tmp_path / p
    cfg.telegram.is_configured = False
    j = MagicMock()
    run_auto_paper_intraday_loop(
        cfg,
        j,
        market_hours_only=False,
        max_cycles=3,
        interval_seconds=0.01,
        sleep_fn=lambda _x: None,
        telegram_style="engine",
        telegram=False,
    )
    assert calls["n"] == 3
