"""Mocked Forex candle CSV writer (fetch-forex-candles wiring)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from bot.ibkr_connection import IbkrRoConnectOutcome
from bot.forex.fetch_bridge import fetch_forex_1m_duration


def test_fetch_writes_csv_and_returns_ok(monkeypatch, tmp_path: Path) -> None:
    class _Cfg:
        project_root = tmp_path

        def absolute(self, p: str) -> Path:
            return Path(p) if Path(p).is_absolute() else tmp_path / p

    cfg = _Cfg()
    sample = [
        {
            "timestamp": "2026-04-26",
            "open": 1.0,
            "high": 1.01,
            "low": 0.99,
            "close": 1.005,
            "volume": 0,
        },
    ]

    cli = MagicMock()
    cli.get_intraday_bars.return_value = sample

    monkeypatch.setattr(
        "bot.forex.fetch_bridge.connect_readonly_roster_retry",
        lambda *_a, **_k: IbkrRoConnectOutcome(
            client=cli, client_id_used=24, attempted_client_ids=[24]
        ),
    )
    monkeypatch.setattr(
        "bot.forex.fetch_bridge.load_config",
        lambda project_root=None: cfg,
    )
    monkeypatch.setattr(
        "bot.forex.fetch_bridge.Journal",
        lambda c: MagicMock(record_event=lambda *_a, **_k: None),
    )

    r = fetch_forex_1m_duration(
        project_root=tmp_path, pair_display="AUD/USD", cfg=cfg
    )
    assert r.get("ok")
    slug = Path(r["cache_dir"]).parent.name.upper()
    assert slug == "AUDUSD"
    assert Path(r["cache_dir"]).is_dir()


def test_forex_candle_csv_has_header(tmp_path: Path) -> None:
    from bot.forex.candle_store import save_forex_candles_csv

    rows = [{"timestamp": "2026-04-26", "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 0}]
    stats = save_forex_candles_csv(tmp_path, "AUDUSD", "1min", rows, force=True)
    p = Path(stats["cache_dir"]) / "2026-04-26.csv"
    assert p.is_file()
    assert "timestamp" in p.read_text(encoding="utf-8").splitlines()[0]
