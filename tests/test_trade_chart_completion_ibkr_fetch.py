"""IBKR read-only candle fetch path for trade chart completion (mocked; no real TWS)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bot.trade_chart_completion import complete_trade_charts
from bot.trade_journal_chart import TradeChartOutcome


def _copy_minimal_config(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parent.parent
    cfg_dir = repo / "config"
    import shutil

    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
    ):
        shutil.copy(cfg_dir / name, tmp_path / "config" / name)


def test_fetch_missing_triggers_read_only_fetch_then_generate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _copy_minimal_config(tmp_path)

    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    line = (
        '{"symbol":"MOCK","direction":"long","timestamp":"2024-06-03T13:55:05.123456+00:00",'
        '"skipped_reasons":[],"submitted":true,"bracket_integrity":"complete",'
        '"strategy_id":"ict_smc_intraday_v1","entry":100,"stop":99,"target":103}\n'
    )
    (pod / "x-intraday-paper-orders.jsonl").write_text(line, encoding="utf-8")

    from bot.config import load_config

    cfg = load_config(project_root=tmp_path)

    fetch_n = {"v": 0}

    def fake_fetch(*_a, **_k):
        fetch_n["v"] += 1
        return True

    seq = [False, True]

    def fake_candles(_root, _raw):  # noqa: ANN001
        if not seq:
            return True
        return seq.pop(0)

    png_path = tmp_path / "data" / "reports" / "trade_charts" / "placeholder.png"

    def fake_gen(_root, _tid, **_k):  # noqa: ANN001
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(b"\211PNG\r\n\x1a\n")
        return TradeChartOutcome(ok=True, message="ok", png_path=png_path)

    monkeypatch.chdir(tmp_path)
    with (
        patch("bot.trade_chart_completion._fetch_1min_symbol_date", side_effect=fake_fetch),
        patch(
            "bot.trade_chart_completion.candles_available_for_trade",
            side_effect=fake_candles,
        ),
        patch("bot.trade_chart_completion.generate_trade_journal_chart_png", side_effect=fake_gen),
    ):
        out = complete_trade_charts(
            tmp_path,
            latest=True,
            limit=5,
            fetch_missing_candles=True,
            cfg=cfg,
            before_mins=30,
            after_mins=90,
        )

    assert fetch_n["v"] >= 1
    assert int(out.get("candle_fetch_attempted_count") or 0) >= 1
    assert int(out.get("candle_fetch_success_count") or 0) >= 1
    assert int(out.get("generated_count") or 0) >= 1


def test_local_only_resolve_never_fetches_ibkr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    def _boom(*_a, **_k):
        raise AssertionError("no IBKR")

    _copy_minimal_config(tmp_path)
    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    (pod / "y-intraday-paper-orders.jsonl").write_text(
        '{"symbol":"ZZZ","direction":"long","timestamp":"2024-06-03T13:55:05Z","submitted":true,'
        '"skipped_reasons":[],"bracket_integrity":"complete","entry":1}\n',
        encoding="utf-8",
    )

    with patch("bot.trade_chart_completion._fetch_1min_symbol_date", side_effect=_boom):
        out = complete_trade_charts(tmp_path, latest=True, limit=5, fetch_mode="local_only", dry_run=True)

    assert out.get("fetch_mode") == "local_only" or out.get("fetch_mode_resolved") == "local_only"
