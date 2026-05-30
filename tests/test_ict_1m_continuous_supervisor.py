"""ICT 1m continuous supervisor — Melbourne windows + persistent loop semantics."""

from __future__ import annotations

import datetime as dt_module
import json
import shutil
from datetime import timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from zoneinfo import ZoneInfo

from bot.config import load_config
from bot.execution.intraday_paper_execution import IntradayPaperPassResult
from bot.ict_1m_continuous_supervisor import (
    melbourne_window_allowed,
    run_ict_1m_continuous_loop,
    state_path,
)
from bot.journal import Journal
from bot.tws_health_alerts import TWSHealthStatus

REPO_ROOT = Path(__file__).resolve().parent.parent


def _melbourne_to_utc(y: int, m: int, d: int, hour: int, minute: int) -> dt_module.datetime:
    loc = dt_module.datetime(y, m, d, hour, minute, tzinfo=ZoneInfo("Australia/Melbourne"))
    return loc.astimezone(timezone.utc)


@pytest.fixture
def ict_tmp_project(tmp_project: Path) -> Path:
    shutil.copy(REPO_ROOT / "config" / "ict_1m_continuous.yaml", tmp_project / "config" / "ict_1m_continuous.yaml")
    shutil.copy(REPO_ROOT / "config" / "forex_ict_1m.yaml", tmp_project / "config" / "forex_ict_1m.yaml")
    return tmp_project


@pytest.mark.parametrize(
    ("utc_wall", "forex_allow"),
    [
        pytest.param(_melbourne_to_utc(2026, 1, 5, 8, 0), True, id="forex_mon_0800_open"),
        pytest.param(_melbourne_to_utc(2026, 1, 5, 21, 59), True, id="forex_mon_2159_open"),
        pytest.param(_melbourne_to_utc(2026, 1, 5, 22, 1), False, id="forex_mon_2201_closed"),
    ],
)
def test_forex_ict_window_melbourne(utc_wall: dt_module.datetime, forex_allow: bool) -> None:
    ok, why = melbourne_window_allowed(
        "Australia/Melbourne", "08:00", "22:00", now_utc=utc_wall
    )
    assert ok is forex_allow
    if forex_allow:
        assert why == ""


@pytest.mark.parametrize(
    ("utc_wall", "us_allow"),
    [
        pytest.param(_melbourne_to_utc(2026, 1, 5, 22, 30), True, id="us_mon_2230_open"),
        pytest.param(_melbourne_to_utc(2026, 1, 6, 0, 30), True, id="us_tue_0030_open"),
        pytest.param(_melbourne_to_utc(2026, 1, 6, 1, 1), False, id="us_tue_0101_closed"),
    ],
)
def test_us_stock_ict_window_melbourne_overnight(
    utc_wall: dt_module.datetime,
    us_allow: bool,
) -> None:
    ok, why = melbourne_window_allowed(
        "Australia/Melbourne", "22:30", "01:00", now_utc=utc_wall
    )
    assert ok is us_allow
    if us_allow:
        assert why == ""


def _minimal_pass_result(reason: str = "scan_ok") -> IntradayPaperPassResult:
    return IntradayPaperPassResult(
        timestamp_utc=dt_module.datetime.now(timezone.utc).isoformat(),
        paper_only=True,
        runtime_intraday_on=True,
        kill_switch=False,
        reconciliation_status="ok",
        config_enabled=True,
        fully_automatic=True,
        symbols_scanned=["SPY"],
        strict_ready_count=0,
        aggressive_ready_count=0,
        submissions=[],
        skipped_reasons=[],
        last_status="ok",
        last_reason=reason,
    )


def _patch_health(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bot.ict_1m_continuous_supervisor.check_tws_health_for_alerts",
        lambda c, j=None: TWSHealthStatus(status="healthy"),
    )
    monkeypatch.setattr(
        "bot.ict_1m_continuous_supervisor.maybe_send_tws_health_alert",
        lambda *a, **k: None,
    )


def _patch_clock(monkeypatch: pytest.MonkeyPatch, fixed_utc: dt_module.datetime) -> None:
    class PatchedDateTime:
        timezone = dt_module.timezone
        timedelta = dt_module.timedelta

        @staticmethod
        def now(tz=None):  # noqa: ANN001
            if tz is dt_module.timezone.utc:
                return fixed_utc
            return dt_module.datetime.now(tz)

    monkeypatch.setattr(
        "bot.ict_1m_continuous_supervisor.datetime",
        PatchedDateTime,
    )


def test_bot_loop_outside_session_runs_without_supervisors(
    ict_tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = _melbourne_to_utc(2026, 1, 5, 3, 0)
    _patch_clock(monkeypatch, fixed)
    _patch_health(monkeypatch)

    fx = MagicMock()
    monkeypatch.setattr(
        "bot.ict_1m_continuous_supervisor.run_forex_auto_paper_supervisor",
        fx,
    )
    us_mock = MagicMock()
    monkeypatch.setattr(
        "bot.ict_1m_continuous_supervisor.run_intraday_paper_pass",
        us_mock,
    )

    cfg = load_config(project_root=ict_tmp_project)
    journal = Journal(cfg)
    run_ict_1m_continuous_loop(
        ict_tmp_project,
        cfg=cfg,
        journal=journal,
        max_iterations=2,
        sleep_fn=lambda _: None,
    )

    fx.assert_not_called()
    us_mock.assert_not_called()


def test_kill_switch_blocks_entries_but_loop_runs_multiple_ticks(
    ict_tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = _melbourne_to_utc(2026, 1, 5, 10, 0)
    _patch_clock(monkeypatch, fixed)
    _patch_health(monkeypatch)
    (ict_tmp_project / "data" / "KILL_SWITCH").write_text("on\n", encoding="utf-8")

    fx = MagicMock()
    monkeypatch.setattr(
        "bot.ict_1m_continuous_supervisor.run_forex_auto_paper_supervisor",
        fx,
    )
    us_mock = MagicMock()
    monkeypatch.setattr(
        "bot.ict_1m_continuous_supervisor.run_intraday_paper_pass",
        us_mock,
    )

    cfg = load_config(project_root=ict_tmp_project)
    journal = Journal(cfg)
    run_ict_1m_continuous_loop(
        ict_tmp_project,
        cfg=cfg,
        journal=journal,
        max_iterations=2,
        sleep_fn=lambda _: None,
    )

    fx.assert_not_called()
    us_mock.assert_not_called()
    st = json.loads(state_path(ict_tmp_project).read_text(encoding="utf-8"))
    assert st.get("iteration") == 2
    assert st.get("block_reason") == "kill_switch"
    assert st.get("kill_switch_active") is True


def test_continuous_loop_calls_forex_supervisor_twice_same_session(
    ict_tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = _melbourne_to_utc(2026, 1, 5, 10, 0)
    _patch_clock(monkeypatch, fixed)
    _patch_health(monkeypatch)

    fx = MagicMock(
        return_value={
            "blockers": ["no_tradeable_signal"],
            "next_action": "no_tradeable_signal",
            "broker_result": {},
        }
    )
    monkeypatch.setattr(
        "bot.ict_1m_continuous_supervisor.run_forex_auto_paper_supervisor",
        fx,
    )
    monkeypatch.setattr(
        "bot.ict_1m_continuous_supervisor.run_intraday_paper_pass",
        lambda *a, **k: _minimal_pass_result("hold"),
    )

    cfg = load_config(project_root=ict_tmp_project)
    journal = Journal(cfg)

    run_ict_1m_continuous_loop(
        ict_tmp_project,
        cfg=cfg,
        journal=journal,
        max_iterations=2,
        sleep_fn=lambda _: None,
    )

    assert fx.call_count == 2
    st = json.loads(state_path(ict_tmp_project).read_text(encoding="utf-8"))
    assert st.get("iteration") == 2
    # Repo forex yaml keeps submit_to_broker false — permission stays blocked at YAML gate.
    assert st.get("block_reason") == "risk_block"
    assert st.get("forex_yaml_submit_to_broker") is False
