"""Forex auto paper readiness — config gates (no broker)."""

from __future__ import annotations

from pathlib import Path

import yaml

from bot.config import load_config
from bot.forex.auto_paper_readiness import build_forex_auto_paper_readiness


def _write_fx_yaml(root: Path, **kwargs: object) -> None:
    p = root / "config" / "forex_ict_1m.yaml"
    base = {
        "strategy_id": "ict_fx_1m_test",
        "asset_class": "forex",
        "session": {"timezone": "Australia/Melbourne"},
        "pairs": {"primary": ["AUD/USD"], "secondary": []},
        "risk": {"paper_only": True, "max_daily_notional_usd": 100_000},
        "execution": {
            "submit_to_broker": False,
            "order_type": "LMT",
            "no_market_orders": True,
            "bracket_required": True,
        },
        "auto_paper": {
            "enabled": False,
            "session_timezone": "Australia/Melbourne",
            "session_window": "09:00-17:00",
            "poll_interval_seconds": 60,
        },
    }
    merged = {**base, **kwargs}
    p.write_text(yaml.safe_dump(merged), encoding="utf-8")


def _ensure_no_kill_switch(root: Path) -> None:
    p = root / "data" / "KILL_SWITCH"
    if p.is_file():
        p.unlink()


def test_readiness_false_when_yaml_auto_off(tmp_project: Path) -> None:
    _ensure_no_kill_switch(tmp_project)
    _write_fx_yaml(tmp_project)
    (tmp_project / "data/runtime").mkdir(parents=True, exist_ok=True)
    (tmp_project / "data/runtime/forex_auto_paper_enabled.json").write_text(
        '{"enabled": true}\n', encoding="utf-8"
    )

    cfg = load_config(project_root=tmp_project)
    r = build_forex_auto_paper_readiness(tmp_project, cfg, probe_ibkr=False)
    assert r["ok"] is False
    assert "auto_paper.enabled_false_yaml" in r["blockers"]


def test_readiness_false_when_submit_false_even_if_runtime_on(tmp_project: Path) -> None:
    _ensure_no_kill_switch(tmp_project)
    _write_fx_yaml(tmp_project, auto_paper={"enabled": True})
    (tmp_project / "data/runtime").mkdir(parents=True, exist_ok=True)
    (tmp_project / "data/runtime/forex_auto_paper_enabled.json").write_text(
        '{"enabled": true}\n', encoding="utf-8"
    )
    cfg = load_config(project_root=tmp_project)
    r = build_forex_auto_paper_readiness(tmp_project, cfg, probe_ibkr=False)
    assert r["submit_to_broker_yaml"] is False
    assert "execution.submit_to_broker_false" in r["blockers"]
