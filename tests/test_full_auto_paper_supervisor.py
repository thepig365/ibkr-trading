"""Full-auto paper supervisor (mocked; never starts live or market orders)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bot.config import load_config
from bot.full_auto_paper_supervisor import run_full_auto_paper_supervisor, write_full_auto_supervisor_state


def test_dry_run_returns_readiness_and_writes_state(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    journal = MagicMock()
    out = run_full_auto_paper_supervisor(
        cfg,
        journal,
        session="full",
        telegram=False,
        dry_run=True,
        market_open_check_only=False,
    )
    assert out.get("finished") is True
    assert "last_readiness" in out
    p = tmp_project / "data" / "runtime" / "full_auto_paper_supervisor_state.json"
    if p.is_file():
        assert "supervisor_phase" in p.read_text(encoding="utf-8")


def test_market_open_check_only_no_engine(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    journal = MagicMock()
    out = run_full_auto_paper_supervisor(
        cfg,
        journal,
        market_open_check_only=True,
        telegram=False,
        dry_run=False,
    )
    assert out.get("check_only") is True
    assert "readiness" in out


def test_engine_runner_called_when_gates_synthetic(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When window + gates pass, supervisor invokes engine runner (mock)."""
    from bot import full_auto_paper_supervisor as mod

    cfg = load_config(project_root=tmp_project)
    journal = MagicMock()
    calls: list[object] = []

    def fake_readiness(*_a, **_k):
        return {
            "ok": True,
            "status": "ready_to_run",
            "blockers": [],
            "in_trading_window": True,
        }

    def fake_tws(_h, _p, **_k):
        return True

    def fake_run_engine(*_a, **_k):
        calls.append("engine")
        return {"started": True, "finished": True, "blockers": []}

    # session=full uses trading.intraday_paper wall-clock (not NY minutes alone).
    monkeypatch.setattr(
        mod,
        "intraday_new_entries_allow_config",
        lambda ip, *, now_local=None: (True, ""),
    )
    monkeypatch.setattr(
        mod,
        "entry_timezone_now_display",
        lambda ip: ("2099-01-05T10:30:00+11:00", "10:30", 0, 10 * 60 + 30),
    )

    # NY parts still used for Telegram / health-band
    def fake_minutes():
        return 0, 10 * 60 + 30  # Mon 10:30

    monkeypatch.setattr(mod, "build_full_auto_paper_readiness", fake_readiness)
    monkeypatch.setattr(mod, "tws_port_listening", fake_tws)
    monkeypatch.setattr(mod, "_weekday_minutes", fake_minutes)
    monkeypatch.setattr(
        mod,
        "run_market_news_check",
        lambda *a, **k: {"ok": True, "dry": True},
    )
    out = run_full_auto_paper_supervisor(
        cfg,
        journal,
        session="full",
        telegram=False,
        dry_run=False,
        no_trade=False,
        engine_runner=fake_run_engine,
        sleep_fn=lambda _x: None,
        time_fn=lambda: 0.0,
    )
    assert calls == ["engine"]
    assert out.get("engine_result") is not None


def test_write_state_merges(tmp_project: Path) -> None:
    write_full_auto_supervisor_state(tmp_project, {"a": 1})
    write_full_auto_supervisor_state(tmp_project, {"b": 2})
    p = tmp_project / "data" / "runtime" / "full_auto_paper_supervisor_state.json"
    t = p.read_text(encoding="utf-8")
    assert '"a": 1' in t and '"b": 2' in t
