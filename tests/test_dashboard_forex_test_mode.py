"""Build forex dashboard UX context from YAML + filesystem."""

from __future__ import annotations

from pathlib import Path

import yaml

from bot.forex.runner import build_forex_test_ui_context


def test_build_forex_test_ui_requires_minimal_config(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    yaml.safe_dump(
        {
            "strategy_id": "ict_fx_1m_test",
            "enabled": True,
            "pairs": ["AUD/USD"],
            "execution": {"submit_to_broker": False},
        },
        (tmp_path / "config" / "forex_ict_1m.yaml").open("w", encoding="utf-8"),
    )
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv("IBKR_TRADING_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("BOT_PROJECT_ROOT", raising=False)

    (tmp_path / "config" / "settings.yaml").write_text(
        "logging:\n  level: WARNING\naccount:\n  mode: paper\n  block_live_trading: true\n"
        "telegram:\n  enabled: false\ntrading:\n  enabled: true\nreports:\n  enabled: false\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "strategy.yaml").write_text(
        "strategy: {}\nstrategies: {}\nreview_queue: {}\n",
        encoding="utf-8",
    )
    for name in ("watchlist.yaml", "news.yaml", "telegram.yaml"):
        if not (tmp_path / "config" / name).exists():
            (tmp_path / "config" / name).write_text("{}")
    (tmp_path / "config" / "schedule.yaml").write_text("schedule: {}\n")

    cfg = __import__(
        "bot.config", fromlist=["load_config"]
    ).load_config(project_root=tmp_path)
    ui = build_forex_test_ui_context(tmp_path, cfg=cfg)
    assert ui.get("strategy_id") == "ict_fx_1m_test"
    assert ui.get("mode") == "dry-run"
