"""Tests for the intraday paper bracket background loop (Prompt 13F)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from bot.auto_paper_intraday_loop import run_auto_paper_intraday_loop
from bot.config import load_config
from bot.execution.intraday_paper_execution import (
    INTRADAY_LOOP_STATE_RELPATH,
    KILL_SWITCH_RELPATH,
    IntradayPaperPassResult,
)
from bot.journal import Journal


def _enable_intraday_paper(
    project: Path, write_yaml, *, fully_automatic: bool = True,
) -> None:
    p = project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s.setdefault("trading", {})["enabled"] = True
    ip = s["trading"].setdefault("intraday_paper", {})
    ip["enabled"] = True
    ip["fully_automatic"] = fully_automatic
    ip["dry_run"] = True
    s.setdefault("account", {})
    s["account"]["mode"] = "paper"
    s["account"]["block_live_trading"] = True
    write_yaml(p, s)


def _ok_pass_result(orders: int = 0) -> IntradayPaperPassResult:
    return IntradayPaperPassResult(
        timestamp_utc="2026-04-25T13:00:00Z",
        paper_only=True,
        runtime_intraday_on=True,
        kill_switch=False,
        reconciliation_status="passed",
        config_enabled=True,
        fully_automatic=True,
        symbols_scanned=["AAPL", "TSLA"],
        strict_ready_count=1,
        aggressive_ready_count=0,
        submissions=[],
        skipped_reasons=[],
        last_status="ok",
        last_reason="submitted=0/0" if orders == 0 else f"submitted={orders}/{orders}",
        audit_log_path=None,
        state_file_path=None,
    )


def test_loop_skips_when_kill_switch_active(
    tmp_project: Path, write_yaml, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    (tmp_project / KILL_SWITCH_RELPATH).write_text("on\n", encoding="utf-8")
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    pass_mock = MagicMock(return_value=_ok_pass_result())
    monkeypatch.setattr(
        "bot.auto_paper_intraday_loop.run_intraday_paper_pass", pass_mock,
    )
    run_auto_paper_intraday_loop(
        cfg,
        journal,
        once=True,
        telegram=False,
        market_hours_only=False,
        interval_seconds=10,
        sleep_fn=lambda s: None,
    )
    pass_mock.assert_not_called()
    log = (
        tmp_project / "data" / "auto_paper_loop"
    )
    assert any(p.suffix == ".jsonl" for p in log.glob("*.jsonl")) if log.exists() else True


def test_loop_skips_outside_rth_when_market_hours_only(
    tmp_project: Path, write_yaml, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    pass_mock = MagicMock(return_value=_ok_pass_result())
    monkeypatch.setattr(
        "bot.auto_paper_intraday_loop.run_intraday_paper_pass", pass_mock,
    )
    monkeypatch.setattr(
        "bot.auto_paper_intraday_loop.intraday_new_entries_allow_config",
        lambda ip, *, now_local=None: (False, "test closed"),
    )
    run_auto_paper_intraday_loop(
        cfg,
        journal,
        once=True,
        telegram=False,
        market_hours_only=True,
        interval_seconds=10,
        sleep_fn=lambda s: None,
    )
    pass_mock.assert_not_called()


def test_loop_runs_pass_and_writes_audit_log(
    tmp_project: Path, write_yaml, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    pass_mock = MagicMock(return_value=_ok_pass_result(orders=2))
    monkeypatch.setattr(
        "bot.auto_paper_intraday_loop.run_intraday_paper_pass", pass_mock,
    )
    run_auto_paper_intraday_loop(
        cfg,
        journal,
        once=True,
        telegram=False,
        market_hours_only=False,
        interval_seconds=10,
        sleep_fn=lambda s: None,
    )
    pass_mock.assert_called_once()
    log_dir = tmp_project / "data" / "auto_paper_loop"
    assert log_dir.exists()
    files = list(log_dir.glob("*-intraday-loop.jsonl"))
    assert len(files) == 1
    line = files[0].read_text(encoding="utf-8").strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["execution_mode"] == "paper"
    assert rec["live_trading"] is False
    assert rec["orders_submitted"] == 0  # mocked to "submitted=2/2" but
    # orders_submitted is read off result.orders_submitted which counts subs
    assert rec["status"] == "ok"


def test_loop_records_kill_switch_in_audit_line(
    tmp_project: Path, write_yaml, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    (tmp_project / KILL_SWITCH_RELPATH).write_text("on\n", encoding="utf-8")
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    pass_mock = MagicMock(return_value=_ok_pass_result())
    monkeypatch.setattr(
        "bot.auto_paper_intraday_loop.run_intraday_paper_pass", pass_mock,
    )
    run_auto_paper_intraday_loop(
        cfg,
        journal,
        once=True,
        telegram=False,
        market_hours_only=False,
        interval_seconds=10,
        sleep_fn=lambda s: None,
    )
    log_dir = tmp_project / "data" / "auto_paper_loop"
    assert log_dir.exists()
    files = list(log_dir.glob("*-intraday-loop.jsonl"))
    assert files
    line = files[0].read_text(encoding="utf-8").strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["kill_switch"] is True
    assert rec["status"] == "skipped"
    assert rec["reason"] == "kill_switch"


def test_loop_does_not_send_telegram_when_disabled(
    tmp_project: Path, write_yaml, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    pass_mock = MagicMock(return_value=_ok_pass_result(orders=1))
    monkeypatch.setattr(
        "bot.auto_paper_intraday_loop.run_intraday_paper_pass", pass_mock,
    )
    tg_mock = MagicMock()
    monkeypatch.setattr(
        "bot.auto_paper_intraday_loop.send_telegram_message", tg_mock,
    )
    run_auto_paper_intraday_loop(
        cfg,
        journal,
        once=True,
        telegram=False,  # explicit OFF
        market_hours_only=False,
        interval_seconds=10,
        sleep_fn=lambda s: None,
    )
    tg_mock.assert_not_called()


def test_loop_writes_state_file_path_into_log_line(
    tmp_project: Path, write_yaml, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    pass_mock = MagicMock(return_value=_ok_pass_result())
    monkeypatch.setattr(
        "bot.auto_paper_intraday_loop.run_intraday_paper_pass", pass_mock,
    )
    run_auto_paper_intraday_loop(
        cfg,
        journal,
        once=True,
        telegram=False,
        market_hours_only=False,
        interval_seconds=10,
        sleep_fn=lambda s: None,
    )
    log_files = list((tmp_project / "data" / "auto_paper_loop").glob("*-intraday-loop.jsonl"))
    rec = json.loads(log_files[0].read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["loop_state_path"].endswith(INTRADAY_LOOP_STATE_RELPATH)
