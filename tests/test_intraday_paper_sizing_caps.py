"""Tests for intraday paper TIF + notional / quantity caps (Prompt 13K.3)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from bot.config import load_config
from bot.execution.intraday_paper_sizing import (
    apply_paper_sizing_caps,
    normalize_intraday_paper_tif,
    read_today_submitted_broker_notional_usd,
)


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _ip_from_cfg(cfg):
    return cfg.settings.trading.intraday_paper


def test_normalize_tif_day_ok() -> None:
    assert normalize_intraday_paper_tif("day") == "DAY"
    assert normalize_intraday_paper_tif(None) == "DAY"


def test_normalize_tif_rejects_non_day() -> None:
    with pytest.raises(ValueError, match="DAY"):
        normalize_intraday_paper_tif("GTC")


def test_per_trade_cap_entry_250_risk_800_yields_40(
    tmp_project: Path, write_yaml,
) -> None:
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s.setdefault("trading", {})["enabled"] = True
    ip = s["trading"].setdefault("intraday_paper", {})
    for k, v in {
        "enabled": True,
        "fully_automatic": True,
        "paper_only": True,
        "live_trading_allowed": False,
        "market_orders_allowed": False,
        "bracket_required": True,
        "stop_required": True,
        "target_required": True,
        "max_notional_per_order_usd": 10_000.0,
        "max_daily_notional_usd": 100_000.0,
        "max_equity_per_position_pct": 10.0,
        "max_quantity_per_order": 500,
    }.items():
        ip[k] = v
    s.setdefault("account", {})
    s["account"]["mode"] = "paper"
    s["account"]["block_live_trading"] = True
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    ipm = _ip_from_cfg(cfg)
    final, audit, skip = apply_paper_sizing_caps(
        cfg,
        entry=250.0,
        risk_based_quantity=800,
        equity=1_000_000.0,
        per_share_risk=1.0,
        ip=ipm,
    )
    assert not skip
    assert final == 40
    assert audit.get("per_trade_notional_cap_quantity") == 40
    assert audit.get("per_trade_cap_applied") is True
    assert audit.get("final_quantity") == 40
    assert audit.get("estimated_notional") == pytest.approx(10_000.0)


def test_daily_remaining_95000_entry_250_caps_to_20(
    tmp_project: Path, write_yaml,
) -> None:
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s.setdefault("trading", {})["enabled"] = True
    ip = s["trading"].setdefault("intraday_paper", {})
    for k, v in {
        "enabled": True,
        "fully_automatic": True,
        "paper_only": True,
        "live_trading_allowed": False,
        "market_orders_allowed": False,
        "bracket_required": True,
        "stop_required": True,
        "target_required": True,
    }.items():
        ip[k] = v
    s.setdefault("account", {})
    s["account"]["mode"] = "paper"
    s["account"]["block_live_trading"] = True
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    day = _utc_today()
    jpath = (
        tmp_project / "data" / "paper_orders" / f"{day}-intraday-paper-orders.jsonl"
    )
    jpath.parent.mkdir(parents=True, exist_ok=True)
    jpath.write_text(
        json.dumps(
            {
                "submitted_to_broker": True,
                "estimated_notional": 95_000.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ipm = _ip_from_cfg(cfg)
    final, audit, skip = apply_paper_sizing_caps(
        cfg,
        entry=250.0,
        risk_based_quantity=10_000,
        equity=5_000_000.0,
        per_share_risk=1.0,
        ip=ipm,
    )
    assert not skip
    assert final == 20
    assert audit.get("daily_remaining_notional_usd") == pytest.approx(5_000.0)
    assert audit.get("daily_remaining_quantity") == 20
    assert audit.get("daily_cap_applied") is True


def test_daily_limit_reached_skips(
    tmp_project: Path, write_yaml,
) -> None:
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s.setdefault("trading", {})["enabled"] = True
    ip = s["trading"].setdefault("intraday_paper", {})
    for k, v in {
        "enabled": True,
        "fully_automatic": True,
        "paper_only": True,
        "live_trading_allowed": False,
        "market_orders_allowed": False,
        "bracket_required": True,
        "stop_required": True,
        "target_required": True,
    }.items():
        ip[k] = v
    s.setdefault("account", {})
    s["account"]["mode"] = "paper"
    s["account"]["block_live_trading"] = True
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    day = _utc_today()
    jpath = (
        tmp_project / "data" / "paper_orders" / f"{day}-intraday-paper-orders.jsonl"
    )
    jpath.parent.mkdir(parents=True, exist_ok=True)
    jpath.write_text(
        json.dumps(
            {"submitted_to_broker": True, "estimated_notional": 100_000.0}
        )
        + "\n",
        encoding="utf-8",
    )
    ipm = _ip_from_cfg(cfg)
    final, audit, skip = apply_paper_sizing_caps(
        cfg,
        entry=250.0,
        risk_based_quantity=100,
        equity=1_000_000.0,
        per_share_risk=1.0,
        ip=ipm,
    )
    assert final == 0
    assert "daily_notional_limit_reached" in skip
    assert audit.get("daily_remaining_notional_usd") == 0.0


def test_million_equity_10pct_vs_10k_per_trade_10k_wins(
    tmp_project: Path, write_yaml,
) -> None:
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s.setdefault("trading", {})["enabled"] = True
    ip = s["trading"].setdefault("intraday_paper", {})
    for k, v in {
        "enabled": True,
        "fully_automatic": True,
        "paper_only": True,
        "live_trading_allowed": False,
        "market_orders_allowed": False,
        "bracket_required": True,
        "stop_required": True,
        "target_required": True,
        "max_quantity_per_order": 10_000,
    }.items():
        ip[k] = v
    s.setdefault("account", {})
    s["account"]["mode"] = "paper"
    s["account"]["block_live_trading"] = True
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    ipm = _ip_from_cfg(cfg)
    final, audit, skip = apply_paper_sizing_caps(
        cfg,
        entry=100.0,
        risk_based_quantity=50_000,
        equity=1_000_000.0,
        per_share_risk=0.5,
        ip=ipm,
    )
    assert not skip
    assert final == 100
    assert audit.get("per_trade_notional_cap_quantity") == 100
    # 10% of 1M USD = 100k notional → 1000 shares at $100; $10k/trade still binds first.
    assert audit.get("account_cap_quantity") == 1_000
    assert audit.get("account_cap_notional") == pytest.approx(100_000.0)
    assert audit.get("per_trade_cap_applied") is True
    assert audit.get("account_cap_applied") is False


def test_max_quantity_per_order_flag(
    tmp_project: Path, write_yaml,
) -> None:
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s.setdefault("trading", {})["enabled"] = True
    ip = s["trading"].setdefault("intraday_paper", {})
    for k, v in {
        "enabled": True,
        "fully_automatic": True,
        "paper_only": True,
        "live_trading_allowed": False,
        "market_orders_allowed": False,
        "bracket_required": True,
        "stop_required": True,
        "target_required": True,
        "max_quantity_per_order": 30,
    }.items():
        ip[k] = v
    s.setdefault("account", {})
    s["account"]["mode"] = "paper"
    s["account"]["block_live_trading"] = True
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    ipm = _ip_from_cfg(cfg)
    final, audit, skip = apply_paper_sizing_caps(
        cfg,
        entry=100.0,
        risk_based_quantity=200,
        equity=500_000.0,
        per_share_risk=0.1,
        ip=ipm,
    )
    assert not skip
    assert final == 30
    assert audit.get("quantity_cap_applied") is True


def test_ledger_unreadable_blocks_with_warning(
    tmp_project: Path, write_yaml,
) -> None:
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s.setdefault("trading", {})["enabled"] = True
    ip = s["trading"].setdefault("intraday_paper", {})
    for k, v in {
        "enabled": True,
        "fully_automatic": True,
        "paper_only": True,
        "live_trading_allowed": False,
        "market_orders_allowed": False,
        "bracket_required": True,
        "stop_required": True,
        "target_required": True,
    }.items():
        ip[k] = v
    s.setdefault("account", {})
    s["account"]["mode"] = "paper"
    s["account"]["block_live_trading"] = True
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    day = _utc_today()
    jpath = (
        tmp_project / "data" / "paper_orders" / f"{day}-intraday-paper-orders.jsonl"
    )
    jpath.parent.mkdir(parents=True, exist_ok=True)
    jpath.write_text("not json {\n", encoding="utf-8")
    used, warn = read_today_submitted_broker_notional_usd(cfg)
    assert used == float("inf")
    assert warn is not None
    ipm = _ip_from_cfg(cfg)
    final, audit, skip = apply_paper_sizing_caps(
        cfg,
        entry=100.0,
        risk_based_quantity=10,
        equity=100_000.0,
        per_share_risk=1.0,
        ip=ipm,
    )
    assert final == 0
    assert any("daily_notional_cap" in x for x in skip)


def test_audit_contains_account_cap_and_tif_fields_in_config(
    tmp_project: Path, write_yaml,
) -> None:
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s.setdefault("trading", {})["enabled"] = True
    ip = s["trading"].setdefault("intraday_paper", {})
    for k, v in {
        "enabled": True,
        "fully_automatic": True,
        "paper_only": True,
        "live_trading_allowed": False,
        "market_orders_allowed": False,
        "bracket_required": True,
        "stop_required": True,
        "target_required": True,
        "tif": "DAY",
    }.items():
        ip[k] = v
    s.setdefault("account", {})
    s["account"]["mode"] = "paper"
    s["account"]["block_live_trading"] = True
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    ipm = _ip_from_cfg(cfg)
    final, audit, _skip = apply_paper_sizing_caps(
        cfg,
        entry=100.0,
        risk_based_quantity=50,
        equity=200_000.0,
        per_share_risk=0.2,
        ip=ipm,
    )
    assert final >= 1
    for key in (
        "risk_based_quantity",
        "per_trade_notional_cap_usd",
        "per_trade_notional_cap_quantity",
        "max_daily_notional_usd",
        "today_submitted_notional_usd_before",
        "daily_remaining_notional_usd",
        "daily_remaining_quantity",
        "account_cap_pct",
        "account_cap_notional",
        "account_cap_quantity",
        "max_quantity_per_order",
        "final_quantity",
        "estimated_notional",
        "per_trade_cap_applied",
        "daily_cap_applied",
        "account_cap_applied",
        "quantity_cap_applied",
    ):
        assert key in audit, key
