"""CLI tests for edge profile commands (Prompt 13L-alt)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _project(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "memory").mkdir(exist_ok=True)
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
        "strategies.yaml",
        "macro_calendar.yaml",
    ):
        src = REPO_ROOT / "config" / name
        if src.exists():
            shutil.copy(src, tmp_path / "config" / name)
    return tmp_path


def _run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["IBKR_ACCOUNT_MODE"] = "paper"
    env["IBKR_TRADING_PROJECT_ROOT"] = str(cwd.resolve())
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.setdefault("IBKR_HOST", "127.0.0.1")
    env.setdefault("IBKR_PORT", "65530")
    env.setdefault("IBKR_CLIENT_ID", "9999")
    return subprocess.run(
        [sys.executable, "-m", "bot.cli", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_build_edge_profiles_writes_json_without_cache(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    r = _run(
        [
            "build-edge-profiles",
            "--symbols", "ZZZZZ",
            "--start", "2020-01-01",
            "--end", "2020-01-02",
            "--strategy",
            "ict_smc_intraday_v1",
            "--min-trades",
            "30",
        ],
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    ep = proj / "data" / "edge_profiles"
    files = list(ep.glob("*-edge-profiles.json"))
    assert files, "expected edge profile JSON under data/edge_profiles/"
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    profs = data.get("profiles") or []
    assert profs and profs[0].get("confidence_level") == "insufficient_data"


def test_edge_profile_report_latest(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    day = "2026-04-25"
    ep = proj / "data" / "edge_profiles"
    ep.mkdir(parents=True, exist_ok=True)
    sample = {
        "date": day,
        "profiles": [
            {
                "symbol": "X",
                "strategy_id": "ict_smc_intraday_v1",
                "confidence_level": "weak",
                "filled_trades": 0,
            }
        ],
    }
    (ep / f"{day}-edge-profiles.json").write_text(
        json.dumps(sample, indent=2), encoding="utf-8"
    )
    r = _run(["edge-profile-report", "--latest"], cwd=proj)
    assert r.returncode == 0
    assert "edge-profiles.json" in r.stdout


def test_build_edges_skips_ibkr_fetch_when_not_requested_in_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.config import load_config
    from bot.edge.build_batch import build_edges_for_symbols

    calls: list[int] = []

    def _boom(*_a, **_k):
        calls.append(1)
        return False

    monkeypatch.setattr(
        "bot.edge.build_batch.fetch_1min_range_for_backtest",
        _boom,
    )
    proj = _project(tmp_path)
    cfg = load_config(project_root=proj)
    build_edges_for_symbols(
        cfg,
        ["ZZZZY"],
        start="2019-01-01",
        end="2019-01-02",
        strategy_id="ict_smc_intraday_v1",
        fetch=False,
    )
    assert calls == []


def test_build_edges_calls_fetch_when_flag_in_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.config import load_config
    from bot.edge.build_batch import build_edges_for_symbols

    calls: list[int] = []

    def _fake(*_a, **_k):
        calls.append(1)
        return False

    monkeypatch.setattr(
        "bot.edge.build_batch.fetch_1min_range_for_backtest",
        _fake,
    )
    proj = _project(tmp_path)
    cfg = load_config(project_root=proj)
    build_edges_for_symbols(
        cfg,
        ["ZZZZX"],
        start="2019-01-01",
        end="2019-01-02",
        strategy_id="ict_smc_intraday_v1",
        fetch=True,
    )
    assert len(calls) == 1


def test_no_place_order_in_edge_modules() -> None:
    """Sanity: edge package must not reference order placement."""
    root = REPO_ROOT / "bot" / "edge"
    for p in root.glob("*.py"):
        t = p.read_text(encoding="utf-8")
        assert "place_order" not in t
        assert "place-order" not in t
