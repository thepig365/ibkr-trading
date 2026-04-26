"""one-click: coverage + fetch + backtest (Prompt 13BT-ONECLICK)."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bot.backtests.candle_cache import save_candles_csv
from bot.config import load_config

REPO = Path(__file__).resolve().parent.parent


def _bars(day: str, n: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(n):
        m = (30 + i) % 60
        h = 9 + ((30 + i) // 60)
        ts = f"{day} {h:02d}:{m:02d}:00-04:00"
        out.append(
            {
                "timestamp": ts,
                "open": 100.0 + i * 0.01,
                "high": 100.5 + i * 0.01,
                "low": 99.5 + i * 0.01,
                "close": 100.2 + i * 0.01,
                "volume": 1_000.0 + i,
            }
        )
    return out


def _seed_crm_days(root: Path, days: tuple[str, ...]) -> None:
    for d in days:
        save_candles_csv(
            root,
            "CRM",
            "1min",
            _bars(d, 50),
            start=d,
            end=d,
        )


def test_run_oneclick_all_ready_skips_fetch(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.backtests import oneclick_workflow

    # Mon–Fri 2026-04-20 .. 2026-04-24
    for d in (
        "2026-04-20",
        "2026-04-21",
        "2026-04-22",
        "2026-04-23",
        "2026-04-24",
    ):
        _seed_crm_days(tmp_project, (d,))

    called: list[str] = []

    def _no_fetch(
        *a: object,
        **k: object,
    ) -> dict[str, Any]:
        called.append("fetch")
        return {"ok": True}

    monkeypatch.setattr(
        oneclick_workflow, "_fetch_1m_from_ibkr", _no_fetch, raising=True
    )
    cfg = load_config(project_root=tmp_project)
    rep = oneclick_workflow.run_backtest_oneclick(
        tmp_project,
        cfg,
        symbols=["CRM"],
        start="2026-04-20",
        end="2026-04-24",
        source="test",
        fetch_pacing_seconds=0.0,
    )
    assert not called, "fetch must not be called when coverage is ready"
    assert rep.get("backtest_ran") is True
    assert "CRM" in (rep.get("backtest_symbols_run") or [])


def test_tws_unavailable_stops_without_allow_partial(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.backtests import oneclick_workflow

    cfg = load_config(project_root=tmp_project)

    def _nope(
        *a: object,
        **k: object,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "tws_unavailable": True,
            "error": "offline",
        }

    monkeypatch.setattr(
        oneclick_workflow, "_fetch_1m_from_ibkr", _nope, raising=True
    )
    rep = oneclick_workflow.run_backtest_oneclick(
        tmp_project,
        cfg,
        symbols=["AAPL", "MSFT"],
        start="2026-04-20",
        end="2026-04-24",
        source="test",
        allow_partial=False,
        fetch_pacing_seconds=0.0,
    )
    assert rep.get("fetch_failed_tws_unavailable") is True
    assert rep.get("backtest_ran") is False
    assert rep.get("backtest_ran") is not True


def test_allow_partial_runs_backtest_on_cache_after_tws_fail(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.backtests import oneclick_workflow

    _seed_crm_days(
        tmp_project,
        (
            "2026-04-20",
            "2026-04-21",
            "2026-04-22",
            "2026-04-23",
            "2026-04-24",
        ),
    )
    cfg = load_config(project_root=tmp_project)

    def _nope(
        *a: object,
        **k: object,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "tws_unavailable": True,
            "error": "offline",
        }

    monkeypatch.setattr(
        oneclick_workflow, "_fetch_1m_from_ibkr", _nope, raising=True
    )
    rep = oneclick_workflow.run_backtest_oneclick(
        tmp_project,
        cfg,
        symbols=["CRM", "NVDA"],
        start="2026-04-20",
        end="2026-04-24",
        source="test",
        allow_partial=True,
        fetch_pacing_seconds=0.0,
    )
    assert rep.get("backtest_ran") is True
    assert "CRM" in (rep.get("backtest_symbols_run") or [])
    assert "NVDA" in (rep.get("backtest_symbols_skipped") or [])


def test_oneclick_json_stdout_is_only_json(tmp_project: Path) -> None:
    shutil.copy(
        REPO / "config" / "strategy_ui.yaml",
        tmp_project / "config" / "strategy_ui.yaml",
    )
    for d in (
        "2026-04-20",
        "2026-04-21",
        "2026-04-22",
        "2026-04-23",
        "2026-04-24",
    ):
        _seed_crm_days(tmp_project, (d,))
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "bot.cli",
            "backtest-oneclick",
            "--symbols",
            "CRM",
            "--start",
            "2026-04-20",
            "--end",
            "2026-04-24",
            "--strategy",
            "ict_smc_intraday_v1",
            "--mode",
            "strict_and_aggressive",
            "--direction",
            "both",
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO),
            "IBKR_TRADING_PROJECT_ROOT": str(tmp_project),
        },
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    data = json.loads(r.stdout.strip())
    assert "symbols_requested" in data
    assert "backtest_ran" in data


def test_oneclick_does_not_import_order_execution() -> None:
    sys.modules.pop("bot.backtests.oneclick_workflow", None)
    m = importlib.import_module("bot.backtests.oneclick_workflow")
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert "from bot.execution" not in src and "import bot.execution" not in src
    assert "from bot.broker" not in src and "import bot.broker" not in src
    assert "place_order" not in src


def test_fetch_then_backtest_for_missing(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.backtests import oneclick_workflow

    _seed_crm_days(
        tmp_project,
        (
            "2026-04-20",
            "2026-04-21",
            "2026-04-22",
            "2026-04-23",
            "2026-04-24",
        ),
    )

    def _fill_nvidia(
        cfg: object,
        root: Path,
        symbol: str,
        start: str,
        end: str,
        **k: object,
    ) -> dict[str, Any]:
        if symbol != "NVDA":
            return {"ok": True, "days_written": 0, "rows_written": 0}
        for d in (
            "2026-04-20",
            "2026-04-21",
            "2026-04-22",
            "2026-04-23",
            "2026-04-24",
        ):
            save_candles_csv(
                root,
                "NVDA",
                "1min",
                _bars(d, 30),
                start=d,
                end=d,
            )
        return {
            "ok": True,
            "days_written": 5,
            "rows_written": 150,
        }

    monkeypatch.setattr(
        oneclick_workflow, "_fetch_1m_from_ibkr", _fill_nvidia, raising=True
    )
    cfg = load_config(project_root=tmp_project)
    rep = oneclick_workflow.run_backtest_oneclick(
        tmp_project,
        cfg,
        symbols=["CRM", "NVDA"],
        start="2026-04-20",
        end="2026-04-24",
        source="test",
        fetch_pacing_seconds=0.0,
    )
    assert "NVDA" in (rep.get("symbols_fetched") or [])
    assert rep.get("backtest_ran") is True
    assert "NVDA" in (rep.get("backtest_symbols_run") or [])
