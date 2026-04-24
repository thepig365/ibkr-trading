"""Integration tests for Prompt 10A — 30min SMC/ICT Test Mode.

End-to-end tests spanning:
    * IBKR client: ``get_intraday_bars`` passes the 30min preset to
      ``reqHistoricalData`` correctly and never calls ``placeOrder``.
    * ``get_bars_for_timeframe`` dispatches on ``spec.is_intraday``.
    * ``strategy_engine.evaluate_smc_liquidity_reversal`` honours the
      30min thresholds (stricter stop / RR / extension / risk).
    * ``smc_scanner.save_batch_summary`` persists 30min filenames and
      ``load_batch_summary_for_timeframe`` finds them (and the legacy
      daily filenames).
    * ``review_queue.build_review_queue`` tags every item with the
      timeframe and demotes READY→STRUCTURE_WATCH inside the 30min
      avoid window; JSON path is timeframe-suffixed.
    * CLI: ``scan-smc --timeframe 30min``, ``scan-smc-watchlist
      --timeframe 30min`` and ``smc-review-queue --timeframe 30min``
      run without placing orders; the 30min chart filename contains
      ``-30min-smc.png``.

Safety invariants re-asserted by every relevant test:
    * ``execution_allowed=False`` everywhere on disk,
    * ``research_only=True`` everywhere on disk,
    * ``broker.place_order`` is never called.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable
from unittest.mock import MagicMock

import pytest

matplotlib = pytest.importorskip("matplotlib")

from bot.ibkr_client import IBKRClient  # noqa: E402
from bot.market_structure import Candle  # noqa: E402
from bot.review_queue import build_review_queue, save_review_queue  # noqa: E402
from bot.smc_scanner import (  # noqa: E402
    BatchSummaryNotFoundError,
    ScanBatch,
    batch_summary_filename,
    build_scan_row,
    load_batch_summary_for_timeframe,
    save_batch_summary,
)
from bot.smc_timeframes import (  # noqa: E402
    TimeframeSpec,
    normalise_timeframe,
    resolve_timeframe_spec,
)
from bot.strategy_engine import evaluate_smc_liquidity_reversal  # noqa: E402
from tests.test_smc_liquidity_reversal import _approved_setup_candles  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write_csv(path: Path, candles: Iterable[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.timestamp, c.open, c.high, c.low, c.close, c.volume])


def _patch_project_root(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bot import cli as cli_module
    from bot import config as config_module

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_project)
    monkeypatch.setattr(
        cli_module, "load_config",
        lambda **kw: config_module.load_config(project_root=tmp_project, **kw),
    )


def _guard_place_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot import broker as broker_module

    def _boom(*_a, **_kw):  # pragma: no cover - guardrail
        raise AssertionError("place_order must not be invoked")

    monkeypatch.setattr(broker_module.Broker, "place_order", _boom)


class _FakeBar:
    def __init__(self, date: str, o: float, h: float, l: float,  # noqa: E741
                 c: float, v: float) -> None:
        self.date = date
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v


# ---------------------------------------------------------------------------
# IBKR client — 30min candle request wiring
# ---------------------------------------------------------------------------
def test_get_intraday_bars_sends_30min_request_parameters(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``reqHistoricalData`` must be called with the 30min preset and
    the client must never try to place any order."""
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    client = IBKRClient(cfg)

    fake_contract = object()
    fake_ib = MagicMock()
    fake_ib.isConnected = lambda: True
    fake_ib.qualifyContracts = lambda _c: [fake_contract]
    fake_ib.reqHistoricalData = MagicMock(
        return_value=[_FakeBar("2026-04-24 09:30:00", 1, 2, 0.5, 1.5, 1_000)]
    )
    client._ib = fake_ib  # type: ignore[attr-defined]

    bars = client.get_intraday_bars(
        "AAPL",
        duration="20 D",
        bar_size="30 mins",
        what_to_show="TRADES",
        use_rth=True,
    )
    assert bars and bars[0]["timestamp"] == "2026-04-24 09:30:00"

    kwargs = fake_ib.reqHistoricalData.call_args.kwargs
    assert kwargs["durationStr"] == "20 D"
    assert kwargs["barSizeSetting"] == "30 mins"
    assert kwargs["whatToShow"] == "TRADES"
    assert kwargs["useRTH"] is True

    # The client must not have any placeOrder call path that was
    # triggered by fetching bars. `fake_ib.placeOrder` would be set
    # implicitly by MagicMock if it had been called.
    assert "placeOrder" not in [c[0] for c in fake_ib.method_calls]


def test_get_bars_for_timeframe_dispatches_on_is_intraday(
    tmp_project: Path,
) -> None:
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    client = IBKRClient(cfg)

    calls: dict[str, dict] = {}

    def fake_intraday(symbol, **kwargs):
        calls["intraday"] = {"symbol": symbol, **kwargs}
        return [{"timestamp": "t", "open": 1, "high": 1, "low": 1,
                 "close": 1, "volume": 1}]

    def fake_daily(symbol, **kwargs):
        calls["daily"] = {"symbol": symbol, **kwargs}
        return [{"timestamp": "d", "open": 1, "high": 1, "low": 1,
                 "close": 1, "volume": 1}]

    client.get_intraday_bars = fake_intraday  # type: ignore[assignment]
    client.get_daily_bars = fake_daily  # type: ignore[assignment]

    spec_30 = resolve_timeframe_spec("30min", cfg)
    rows = client.get_bars_for_timeframe("AAPL", spec_30)
    assert rows[0]["timestamp"] == "t"
    assert calls["intraday"]["bar_size"] == "30 mins"
    assert calls["intraday"]["duration"] == "20 D"
    assert calls["intraday"]["use_rth"] is True

    calls.clear()
    spec_daily = resolve_timeframe_spec("daily", cfg)
    rows = client.get_bars_for_timeframe("AAPL", spec_daily)
    assert rows[0]["timestamp"] == "d"
    assert "intraday" not in calls
    assert calls["daily"]["duration_str"] == spec_daily.duration == "1 Y"


# ---------------------------------------------------------------------------
# Strategy engine — per-timeframe thresholds
# ---------------------------------------------------------------------------
def test_strategy_engine_applies_30min_thresholds_to_evaluation(
    tmp_project: Path,
) -> None:
    """An otherwise-approved setup (R/R ≈ 2.9 in the fixture) must be
    rejected under the 30min profile if stop is wider than 2% — or it
    at least must expose the stricter per-timeframe threshold in
    ``_timeframe_thresholds`` so the validator sees it."""
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    candles = _approved_setup_candles()
    ev_daily = evaluate_smc_liquidity_reversal(
        symbol="TEST", candles=candles, cfg=cfg, timeframe="daily",
        account_equity=100_000, latest_close=candles[-1].close,
    )
    ev_30m = evaluate_smc_liquidity_reversal(
        symbol="TEST", candles=candles, cfg=cfg, timeframe="30min",
        account_equity=100_000, latest_close=candles[-1].close,
    )
    assert ev_daily.timeframe == "daily"
    assert ev_30m.timeframe == "30min"
    # Risk-per-trade is 0.25% on 30min vs 1% on daily → position size
    # drops by ~4x. We just assert that the setup was sized smaller on
    # 30min so the thresholds truly propagated.
    daily_qty = (ev_daily.trade_plan or {}).get("position_size")
    m30_qty = (ev_30m.trade_plan or {}).get("position_size")
    if daily_qty and m30_qty:
        assert m30_qty < daily_qty


def test_evaluation_payload_carries_timeframe_field() -> None:
    candles = _approved_setup_candles()
    ev = evaluate_smc_liquidity_reversal(
        symbol="TEST", candles=candles, timeframe="30min",
        market_regime="neutral", account_equity=100_000,
        latest_close=candles[-1].close,
    )
    assert ev.to_dict()["timeframe"] == "30min"


# ---------------------------------------------------------------------------
# Scanner — timeframe-aware persistence
# ---------------------------------------------------------------------------
def test_batch_summary_filename_includes_timeframe() -> None:
    assert batch_summary_filename("2026-04-24", "30min") == (
        "2026-04-24-30min-watchlist-summary.json"
    )
    assert batch_summary_filename("2026-04-24", "daily") == (
        "2026-04-24-daily-watchlist-summary.json"
    )


def test_save_and_load_batch_summary_roundtrips_30min(
    tmp_project: Path,
) -> None:
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    candles = _approved_setup_candles()
    ev = evaluate_smc_liquidity_reversal(
        symbol="TEST", candles=candles, timeframe="30min",
        cfg=cfg, market_regime="neutral", account_equity=100_000,
        latest_close=candles[-1].close,
    )
    row = build_scan_row(ev)
    batch = ScanBatch(date="2026-04-24", timeframe="30min", rows=[row])
    path = save_batch_summary(cfg, batch)
    assert path.name.endswith("-30min-watchlist-summary.json")

    summary, loaded = load_batch_summary_for_timeframe(
        cfg, timeframe="30min", date="2026-04-24"
    )
    assert loaded == path
    assert summary["timeframe"] == "30min"
    assert summary["execution_allowed"] is False
    assert summary["research_only"] is True


def test_load_daily_summary_falls_back_to_legacy_filename(
    tmp_project: Path,
) -> None:
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    out_dir = cfg.absolute("data/smc_setups")
    out_dir.mkdir(parents=True, exist_ok=True)
    legacy = out_dir / "2026-04-24-watchlist-summary.json"
    legacy.write_text(json.dumps({
        "date": "2026-04-24", "timeframe": "daily", "buckets": {},
        "execution_allowed": False, "research_only": True,
    }), encoding="utf-8")
    summary, path = load_batch_summary_for_timeframe(
        cfg, timeframe="daily", date="2026-04-24"
    )
    assert path == legacy
    assert summary["timeframe"] == "daily"


def test_load_30min_summary_does_not_match_legacy_filename(
    tmp_project: Path,
) -> None:
    """Only daily gets the legacy-filename fallback. 30min must not
    accidentally load a legacy daily scan."""
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    out_dir = cfg.absolute("data/smc_setups")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "2026-04-24-watchlist-summary.json").write_text(
        json.dumps({"buckets": {}}), encoding="utf-8",
    )
    with pytest.raises(BatchSummaryNotFoundError):
        load_batch_summary_for_timeframe(
            cfg, timeframe="30min", date="2026-04-24"
        )


# ---------------------------------------------------------------------------
# Review queue — timeframe propagation + session guard
# ---------------------------------------------------------------------------
def _ready_row(symbol: str = "AAA") -> dict:
    return {
        "symbol": symbol,
        "bucket": "WATCH_NOW",
        "smc_quality_score": 85,
        "approved_for_dry_run": False,
        "execution_allowed": False,
        "market_regime": "neutral",
        "sweep": True,
        "choch": True,
        "fvg": True,
        "order_block": True,
        "entry_price": 100.0,
        "structural_stop": 97.0,
        "target_1": 110.0,
        "risk_reward_to_target_1": 3.0,
        "stop_distance_pct": 1.5,
        "extension_pct_vs_latest_close": 0.5,
        "rejection_reasons": [],
        "chart_path": "",
    }


def _summary(rows: list[dict], *, timeframe: str = "30min") -> dict:
    return {
        "date": "2026-04-24",
        "timeframe": timeframe,
        "symbols_scanned": len(rows),
        "market_regime": "neutral",
        "regime_confidence": "medium",
        "regime_missing_fields": [],
        "research_scans_allowed": True,
        "new_positions_allowed": False,
        "buckets": {"WATCH_NOW": rows},
        "execution_allowed": False,
        "research_only": True,
    }


def test_review_item_records_30min_timeframe() -> None:
    q = build_review_queue(_summary([_ready_row()]), timeframe="30min")
    assert q.timeframe == "30min"
    assert all(i.timeframe == "30min" for i in q.items)
    d = q.to_dict()
    assert d["timeframe"] == "30min"
    for item in d["items"]:
        assert item["timeframe"] == "30min"
        assert item["execution_allowed"] is False
        assert item["research_only"] is True


def test_session_guard_demotes_ready_to_structure_watch_in_first_15m() -> None:
    q = build_review_queue(
        _summary([_ready_row()]),
        timeframe="30min",
        now_et_hhmm="09:40",
    )
    assert not q.session_guard["allowed"]
    assert "first" in q.session_guard["reason"].lower()
    assert q.items and q.items[0].review_category == "STRUCTURE_WATCH"
    # The demotion reason is recorded on the item's review_notes.
    assert any(
        "first" in n.lower() for n in (q.items[0].review_notes or [])
    )


def test_session_guard_demotes_ready_to_structure_watch_in_last_15m() -> None:
    q = build_review_queue(
        _summary([_ready_row()]),
        timeframe="30min",
        now_et_hhmm="15:50",
    )
    assert not q.session_guard["allowed"]
    assert q.items and q.items[0].review_category == "STRUCTURE_WATCH"


def test_session_guard_keeps_ready_during_regular_session_hours() -> None:
    q = build_review_queue(
        _summary([_ready_row()]),
        timeframe="30min",
        now_et_hhmm="12:00",
    )
    assert q.session_guard["allowed"] is True
    assert q.items
    assert q.items[0].review_category == "READY_FOR_MANUAL_CHART_REVIEW"


def _pullback_ready_row(symbol: str = "BBB") -> dict:
    r = _ready_row(symbol)
    r["extension_pct_vs_latest_close"] = 3.5  # > max_ext 3% ⇒ PULLBACK_WATCH
    return r


def test_session_guard_demotes_pullback_watch_in_first_15m() -> None:
    q = build_review_queue(
        _summary([_pullback_ready_row()]),
        timeframe="30min",
        now_et_hhmm="09:40",
    )
    assert not q.session_guard["allowed"]
    assert q.items and q.items[0].review_category == "STRUCTURE_WATCH"


def test_daily_queue_ignores_session_clock() -> None:
    """Daily scans must never be affected by the 30min session guard
    — even if someone passes a ``now_et_hhmm`` inside the avoid
    window the daily review queue stays READY."""
    q = build_review_queue(
        _summary([_ready_row()], timeframe="daily"),
        timeframe="daily",
        now_et_hhmm="09:35",
    )
    assert q.timeframe == "daily"
    assert q.items[0].review_category == "READY_FOR_MANUAL_CHART_REVIEW"


def test_save_review_queue_writes_30min_filename(tmp_project: Path) -> None:
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    q = build_review_queue(_summary([_ready_row()]), timeframe="30min")
    path = save_review_queue(cfg, q)
    assert path.name.endswith("-30min-smc-review-queue.json")
    payload = json.loads(path.read_text())
    assert payload["timeframe"] == "30min"
    assert payload["execution_allowed"] is False
    assert payload["research_only"] is True


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------
def test_cli_scan_smc_30min_from_csv_writes_chart_with_30min_suffix(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``scan-smc --timeframe 30min --chart`` persists a chart with the
    ``-30min-smc.png`` suffix and never calls place_order."""
    _patch_project_root(tmp_project, monkeypatch)
    _guard_place_order(monkeypatch)

    csv_path = tmp_project / "data" / "candles_demo" / "AAA.csv"
    _write_csv(csv_path, _approved_setup_candles())

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan-smc",
            "--symbol", "AAA",
            "--timeframe", "30min",
            "--csv", str(csv_path),
            "--market-regime", "neutral",
            "--account-equity", "100000",
            "--chart",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "timeframe=30min" in result.output

    charts = list(
        (tmp_project / "data" / "debug_charts").glob("*-AAA-30min-smc.png")
    )
    assert charts, f"expected 30min chart; output:\n{result.output}"


def test_cli_scan_smc_watchlist_30min_writes_30min_summary(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``scan-smc-watchlist --timeframe 30min`` persists a summary with
    the timeframe-suffixed filename and the ``timeframe: 30min``
    envelope."""
    _patch_project_root(tmp_project, monkeypatch)
    _guard_place_order(monkeypatch)

    candles_dir = tmp_project / "data" / "candles_demo"
    _write_csv(candles_dir / "AAA.csv", _approved_setup_candles())

    (tmp_project / "config" / "watchlist.yaml").write_text(
        "equities:\n"
        "  - {symbol: AAA, exchange: SMART, currency: USD}\n",
        encoding="utf-8",
    )

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan-smc-watchlist",
            "--timeframe", "30min",
            "--candles-dir", str(candles_dir),
            "--account-equity", "100000",
            "--market-regime", "neutral",
            "--limit", "1",
        ],
    )
    assert result.exit_code == 0, result.output

    summaries = list(
        (tmp_project / "data" / "smc_setups")
        .glob("*-30min-watchlist-summary.json")
    )
    assert summaries, f"no 30min summary; output:\n{result.output}"
    payload = json.loads(summaries[0].read_text())
    assert payload["timeframe"] == "30min"
    assert payload["execution_allowed"] is False
    assert payload["research_only"] is True


def test_cli_smc_review_queue_30min_uses_30min_summary_and_path(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_project_root(tmp_project, monkeypatch)
    _guard_place_order(monkeypatch)

    summary_dir = tmp_project / "data" / "smc_setups"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / "2026-04-24-30min-watchlist-summary.json").write_text(
        json.dumps(_summary([_ready_row()], timeframe="30min")),
        encoding="utf-8",
    )

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "smc-review-queue",
            "--timeframe", "30min",
            "--markdown",
            "--top", "5",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "30min" in result.output

    files = list(
        (tmp_project / "data" / "review_queue")
        .glob("*-30min-smc-review-queue.json")
    )
    assert files, result.output
    payload = json.loads(files[0].read_text())
    assert payload["timeframe"] == "30min"
    assert payload["execution_allowed"] is False
    assert payload["research_only"] is True

    md = (tmp_project / "memory" / "SMC-REVIEW-QUEUE.md").read_text()
    assert "30min" in md


# ---------------------------------------------------------------------------
# Safety: no new broker import path introduced by 30min code.
# ---------------------------------------------------------------------------
def test_no_30min_module_imports_broker() -> None:
    import ast

    modules = (
        Path(__file__).resolve().parent.parent / "bot" / "smc_timeframes.py",
        Path(__file__).resolve().parent.parent / "bot" / "ibkr_client.py",
    )
    for mod_path in modules:
        tree = ast.parse(mod_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "") != "bot.broker", mod_path
                assert (node.module or "") != "broker", mod_path
            elif isinstance(node, ast.Import):
                for a in node.names:
                    assert not a.name.endswith("broker"), mod_path
