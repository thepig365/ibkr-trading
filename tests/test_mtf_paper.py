"""MTF full-alignment paper bracket (Prompt 10C)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from bot.broker import Broker, TradingDisabled
from bot.config import load_config
from bot.mtf_paper_execution import build_mtf_paper_intent, mtf_paper_may_run
from bot.risk_engine import TradeIntent


def _mtf_full(symbol: str = "AAPL") -> dict:
    return {
        "symbol": symbol,
        "alignment_category": "FULL_ALIGNMENT",
        "eligible_for_future_paper_trade": True,
        "timeframes": {
            "5min": {"trigger_state": "confirmed", "loaded": True},
            "30min": {
                "entry_price": 100.0,
                "stop_price": 98.0,
                "target_1": 104.0,
            },
        },
    }


def test_mtf_paper_may_run_blocks_without_flags(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    ok, r = mtf_paper_may_run(cfg, _mtf_full())
    assert ok is False
    assert "mtf_paper_bracket_enabled" in " ".join(r) or "trading.enabled" in " ".join(r)


def test_mtf_paper_may_run_blocks_without_confirmed_5m(
    tmp_project: Path, write_yaml
) -> None:
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s["trading"]["enabled"] = True
    s["trading"]["mtf_paper_bracket_enabled"] = True
    s["account"]["mode"] = "paper"
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    m = _mtf_full()
    m["timeframes"]["5min"] = {"trigger_state": "waiting_for_pullback", "loaded": True}
    ok, r = mtf_paper_may_run(cfg, m)
    assert ok is False
    assert "confirmed" in " ".join(r).lower() or "5min" in " ".join(r).lower()


def test_mtf_paper_may_run_ok_when_switches_set(tmp_project: Path, write_yaml) -> None:
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s["trading"]["enabled"] = True
    s["trading"]["mtf_paper_bracket_enabled"] = True
    s["account"]["mode"] = "paper"
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    ok, r = mtf_paper_may_run(cfg, _mtf_full())
    assert ok is True
    assert r == []


def test_build_mtf_paper_intent_sizing(tmp_project: Path, write_yaml) -> None:
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s["trading"]["enabled"] = True
    s["trading"]["mtf_paper_bracket_enabled"] = True
    s["account"]["mode"] = "paper"
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    mtf = _mtf_full()
    intent, err = build_mtf_paper_intent(mtf, cfg, account_equity=100_000.0)
    assert not err
    assert intent is not None
    assert intent.entry_limit_price == 100.0
    assert intent.take_profit_price == 104.0
    assert intent.stop_loss_price == 98.0
    # risk 0.25% of 100k -> 125 sh raw; notional cap 10% equity -> 100 sh
    assert int(intent.quantity) == 100


def test_broker_mtf_paper_places_bracket(
    tmp_project: Path, write_yaml, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s["trading"]["enabled"] = True
    s["trading"]["mtf_paper_bracket_enabled"] = True
    s["trading"]["mtf_paper_dry_run"] = False
    s["trading"]["require_manual_confirmation"] = True
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)

    fake_ib = MagicMock()
    c = MagicMock()
    fake_ib.qualifyContracts = MagicMock(return_value=[c])
    fo = MagicMock()
    so = MagicMock()
    to = MagicMock()
    for o, oid in ((fo, 1), (so, 2), (to, 3)):
        o.orderId = oid
    fake_ib.bracketOrder = MagicMock(
        return_value=(fo, so, to)
    )
    fake_ib.placeOrder = MagicMock()
    client = MagicMock()
    client._ib = fake_ib
    client.is_connected = True

    broker = Broker(cfg, client=client, journal=None)
    intent = TradeIntent(
        symbol="AAPL",
        sec_type="STK",
        side="BUY",
        quantity=10.0,
        estimated_price=100.0,
        take_profit_price=104.0,
        stop_loss_price=98.0,
        entry_limit_price=100.0,
    )
    t = broker.place_order(
        intent, confirmed=False, mtf_paper_bracket=True, dry_run=False
    )
    assert t.mtf_paper
    assert t.mtf_paper.get("order_ids")
    assert fake_ib.placeOrder.call_count == 3


def test_broker_rejects_non_mtf_path(tmp_project: Path, write_yaml) -> None:
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s["trading"]["enabled"] = True
    s["trading"]["mtf_paper_dry_run"] = False
    s["trading"]["require_manual_confirmation"] = False
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    broker = Broker(cfg, MagicMock())
    with pytest.raises(TradingDisabled) as e:
        broker.place_order(
            TradeIntent("A", "STK", "BUY", 1, 10.0), confirmed=True, dry_run=False
        )
    assert "mtf_paper" in str(e.value).lower() or "only" in str(e.value).lower()
