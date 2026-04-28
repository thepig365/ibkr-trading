"""Trade chart completion pipeline (local + optional IBKR read-only candle fetch)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bot.trade_chart_completion import _trade_eligible_for_chart, complete_trade_charts


def test_complete_trade_charts_requires_latest_xor_date(tmp_path: Path) -> None:
    out = complete_trade_charts(tmp_path)
    assert out.get("error") == "require_latest_or_date"


def test_complete_trade_charts_dry_run_never_calls_ibkr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    def _boom(*_a, **_k):
        raise AssertionError("IBKR should not be contacted in dry-run")

    with patch("bot.ibkr_connection.connect_readonly_roster_retry", side_effect=_boom):
        out = complete_trade_charts(
            tmp_path,
            latest=True,
            limit=5,
            fetch_missing_candles=True,
            dry_run=True,
        )

    assert isinstance(out, dict)


def test_complete_trade_charts_without_fetch_no_ibkr_when_rows_need_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    def _boom(*_a, **_k):
        raise AssertionError("no IBKR when fetch_missing_candles=False")

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

    orders = tmp_path / "data" / "paper_orders"
    orders.mkdir(parents=True)

    tid = "a1b2c3d4e5f60718291a2b3c4d5e6f708"
    line = (
        '{"symbol":"TEST","direction":"long","timestamp":"2024-06-03T13:55:05.123456+00:00",'
        '"skipped_reasons":[],"submitted":true,"bracket_integrity":"complete",'
        '"strategy_id":"ict_smc_intraday_v1","entry":100,"stop":99,"target":103,'
        f'"trade_review_row_id_hex":"{tid}"}}\n'
    )
    fp = orders / "acct-intraday-paper-orders.jsonl"
    fp.write_text(line, encoding="utf-8")

    from bot.config import load_config

    cfg = load_config(project_root=tmp_path)

    with patch("bot.ibkr_connection.connect_readonly_roster_retry", side_effect=_boom):
        out = complete_trade_charts(
            tmp_path,
            latest=True,
            limit=5,
            fetch_missing_candles=False,
            cfg=cfg,
        )

    assert int(out.get("missing_candles_count") or 0) >= 1
    assert int(out.get("generated_count") or 0) == 0


def test_trade_eligible_rejects_rejected_slug() -> None:
    from bot.trade_ledger import TradeLedgerRecord

    raw = {"symbol": "X", "timestamp": "2024-06-03T13:55:05Z"}
    rec = TradeLedgerRecord(
        trade_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        symbol="X",
        direction="long",
        strategy="s",
        mode_signal="x",
        status_slug="rejected",
        submitted_time="2024-06-03T13:55:05Z",
        entry_time=None,
        entry_price=None,
        exit_time=None,
        exit_price=None,
        stop_price=None,
        target_price=None,
        qty=None,
        notional=None,
        planned_rr=None,
        realized_r=None,
        close_reason="not_recorded",
        ict_labels="",
        submitted_to_broker=False,
        skipped_reason_raw="",
        bracket_status="",
        parent_entry_order_id=None,
        stop_order_id=None,
        target_order_id=None,
        raw_json=raw,
    )
    assert _trade_eligible_for_chart(rec) is False


def test_fetch_path_invokes_roster(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When fetch helper runs, it uses connect_readonly_roster_retry(roster=candles)."""

    monkeypatch.chdir(tmp_path)

    captured: dict[str, str | None] = {"roster": None}

    class _Oc:
        client = None
        live_blocked = None
        fatal_message = "x"

    def _grab_roster(cfg, roster: str):  # noqa: ANN001
        captured["roster"] = roster
        return _Oc()

    with patch(
        "bot.ibkr_connection.connect_readonly_roster_retry",
        side_effect=_grab_roster,
    ):
        from bot.trade_chart_completion import _fetch_1min_symbol_date  # noqa: PLC0415

        from bot.config import load_config

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

        cfg = load_config(project_root=tmp_path)
        _fetch_1min_symbol_date(cfg, "SPY", "2026-04-01", "2026-04-01", use_rth=True)

    assert captured.get("roster") == "candles"
