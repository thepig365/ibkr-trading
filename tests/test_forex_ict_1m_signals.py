"""Forex ICT 1m runner — dry-run / paper gates."""

from __future__ import annotations

from pathlib import Path

import yaml

from bot.config import load_config
from bot.forex.runner import run_forex_ict_1m


def _minimal_settings_root(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "settings.yaml").write_text(
        "logging:\n  level: WARNING\naccount:\n  mode: paper\n"
        "  block_live_trading: true\ntelegram:\n  enabled: false\n"
        "trading:\n  enabled: true\nreports:\n  enabled: false\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "strategy.yaml").write_text(
        "strategy: {}\nstrategies: {}\nreview_queue: {}\n", encoding="utf-8"
    )
    for name in ("watchlist.yaml", "news.yaml", "telegram.yaml"):
        (tmp_path / "config" / name).write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "schedule.yaml").write_text("schedule: {}\n", encoding="utf-8")


def test_dry_run_does_not_require_enabled_yaml(tmp_path: Path, monkeypatch) -> None:
    _minimal_settings_root(tmp_path)
    yaml.safe_dump(
        {
            "enabled": False,
            "pairs": ["AUD/USD"],
            "strategy_id": "ict_fx_1m_test",
            "risk": {
                "paper_only": True,
                "no_market_orders": True,
                "max_units_per_trade": 100000,
                "max_trades_per_day": 10,
                "risk_per_trade_pct": 0.05,
            },
            "execution": {"order_type": "LMT", "submit_to_broker": False},
        },
        (tmp_path / "config" / "forex_ict_1m.yaml").open("w", encoding="utf-8"),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IBKR_TRADING_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("BOT_PROJECT_ROOT", raising=False)
    out = run_forex_ict_1m(tmp_path, dry_run=True, paper=False)
    assert out.get("dry_run") is True
    assert "forex_ict_yaml_enabled_false" not in out.get("blockers", [])


def test_paper_requires_enabled_yaml(tmp_path: Path, monkeypatch) -> None:
    _minimal_settings_root(tmp_path)
    yaml.safe_dump(
        {
            "enabled": False,
            "pairs": ["AUD/USD"],
            "risk": {"paper_only": True, "no_market_orders": True},
            "execution": {"submit_to_broker": False},
        },
        (tmp_path / "config" / "forex_ict_1m.yaml").open("w", encoding="utf-8"),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IBKR_TRADING_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("BOT_PROJECT_ROOT", raising=False)
    out = run_forex_ict_1m(tmp_path, dry_run=False, paper=True)
    assert "forex_ict_yaml_enabled_false" in (out.get("blockers") or [])


def test_load_cfg_for_dashboard(tmp_path: Path, monkeypatch) -> None:
    """Ensure load_config succeeds on same minimal fixture (dashboard parity)."""

    _minimal_settings_root(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IBKR_TRADING_PROJECT_ROOT", str(tmp_path))
    cfg = load_config(project_root=tmp_path)
    assert cfg.settings.account.mode == "paper"
