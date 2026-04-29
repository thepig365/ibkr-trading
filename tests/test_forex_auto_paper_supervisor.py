"""Forex auto paper supervisor — dry-run and gate tests (no broker)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml

from bot.config import load_config
from bot.forex.auto_paper_supervisor import run_forex_auto_paper_supervisor


def _fx_yaml(root, **doc_over: object) -> None:
    p = root / "config" / "forex_ict_1m.yaml"
    doc = {
        "strategy_id": "ict_fx_1m_test",
        "session": {"timezone": "Australia/Melbourne"},
        "pairs": {"primary": ["AUD/USD"], "secondary": []},
        "risk": {
            "paper_only": True,
            "risk_per_trade_pct": 0.01,
            "max_daily_notional_usd": 100_000,
            "max_notional_per_trade_usd": 10_000,
            "per_pair_notional_cap_usd": 30_000,
            "usd_jpy_for_conversion": 150,
        },
        "execution": {
            "submit_to_broker": True,
            "order_type": "LMT",
            "no_market_orders": True,
            "bracket_required": True,
        },
        "auto_paper": {"enabled": True},
    }
    doc.update(doc_over)
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")


def test_supervisor_dry_run_never_submits(
    tmp_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted: list[bool] = []

    def _boom(**_: object) -> dict:
        submitted.append(True)
        raise AssertionError("submit_forex_paper_bracket must not run in dry_run")

    _fx_yaml(tmp_project)

    monkeypatch.setattr(
        "bot.forex.paper_submit.submit_forex_paper_bracket",
        _boom,
    )

    cfg = load_config(project_root=tmp_project)

    with patch(
        "bot.forex.auto_paper_supervisor.fetch_forex_1m_duration",
        lambda **kw: None,
    ):
        with patch(
            "bot.forex.candle_store.load_forex_candles",
            return_value=[],
        ):
            out = run_forex_auto_paper_supervisor(
                tmp_project, dry_run=True, cfg=cfg
            )
    assert out.get("dry_run") is True
    assert submitted == []


def test_actual_supervisor_blocked_when_submit_false(tmp_project) -> None:
    _fx_yaml(
        tmp_project,
        execution={
            "submit_to_broker": False,
            "order_type": "LMT",
            "no_market_orders": True,
            "bracket_required": True,
        },
    )
    (tmp_project / "data/runtime").mkdir(parents=True, exist_ok=True)
    (tmp_project / "data/runtime/forex_auto_paper_enabled.json").write_text(
        '{"enabled": true}', encoding="utf-8"
    )
    cfg = load_config(project_root=tmp_project)
    out = run_forex_auto_paper_supervisor(tmp_project, dry_run=False, cfg=cfg)
    assert "submit_to_broker_yaml_false" in (out.get("blockers") or [])
