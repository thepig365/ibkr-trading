"""Tests for :mod:`bot.smc_scanner` and the watchlist CLI integration.

Covered behaviours:
    * bucket classification per scenario (WATCH_NOW / NEAR_ENTRY /
      TOO_EXTENDED / STRUCTURE_INCOMPLETE / INVALID_RISK / BLOCKED),
    * ``smc_quality_score`` clamps to [0, 100] and never bypasses a
      hard rejection,
    * batch-summary JSON schema,
    * Telegram digest respects privacy mode,
    * the watchlist scanner never calls ``broker.place_order`` and
      never imports it.
"""

from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path
from typing import Iterable

import pytest

matplotlib = pytest.importorskip("matplotlib")

from bot.market_structure import Candle  # noqa: E402
from bot.smc_scanner import (  # noqa: E402
    BUCKETS,
    NEAR_ENTRY_THRESHOLD_PCT,
    TOO_EXTENDED_THRESHOLD_PCT,
    ScanBatch,
    build_scan_row,
    classify_bucket,
    format_telegram_digest,
    score_setup,
)
from bot.strategy_engine import evaluate_smc_liquidity_reversal  # noqa: E402
from tests.test_smc_liquidity_reversal import _approved_setup_candles  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _evaluate(candles: list[Candle], **kwargs):
    kwargs.setdefault("symbol", "TEST")
    kwargs.setdefault("market_regime", "neutral")
    kwargs.setdefault("account_equity", 100_000.0)
    kwargs.setdefault("latest_close", candles[-1].close)
    return evaluate_smc_liquidity_reversal(candles=candles, **kwargs)


def _write_csv(path: Path, candles: Iterable[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.timestamp, c.open, c.high, c.low, c.close, c.volume])


# ---------------------------------------------------------------------------
# Bucket classification
# ---------------------------------------------------------------------------
def test_bucket_structure_incomplete_for_flat_candles() -> None:
    flat = [
        Candle(timestamp=f"d{i:03d}", open=100, high=101, low=99, close=100,
               volume=0)
        for i in range(30)
    ]
    ev = _evaluate(flat)
    assert classify_bucket(ev) == "STRUCTURE_INCOMPLETE"


def test_bucket_blocked_when_regime_is_risk_off() -> None:
    ev = _evaluate(_approved_setup_candles(), market_regime="risk_off")
    assert classify_bucket(ev) == "BLOCKED"


def test_bucket_invalid_risk_when_rr_below_min() -> None:
    candles = _approved_setup_candles()
    cfg = type("Cfg", (), {"strategies": {
        "SMC_LIQUIDITY_REVERSAL_RESEARCH": {
            "risk": {"min_reward_to_risk": 50.0},
        }
    }})()
    ev = _evaluate(candles, cfg=cfg)
    assert classify_bucket(ev) == "INVALID_RISK"


def test_bucket_invalid_risk_when_stop_too_wide() -> None:
    candles = _approved_setup_candles()
    # Force stop_distance_pct rejection by tightening max.
    cfg = type("Cfg", (), {"strategies": {
        "SMC_LIQUIDITY_REVERSAL_RESEARCH": {
            "stop": {"max_allowed_stop_pct": 0.01},
        }
    }})()
    ev = _evaluate(candles, cfg=cfg)
    assert any(
        r.startswith("stop_distance_pct") for r in ev.rejection_reasons
    )
    assert classify_bucket(ev) == "INVALID_RISK"


def test_bucket_too_extended_when_price_runs_away() -> None:
    candles = _approved_setup_candles()
    ev = _evaluate(candles, latest_close=1200.0)  # far above entry ~1017
    assert classify_bucket(ev) == "TOO_EXTENDED"


def test_bucket_near_entry_when_close_within_15_pct() -> None:
    candles = _approved_setup_candles()
    # entry ~ 1017; close at 1020 → +0.3% extension → NEAR_ENTRY
    ev = _evaluate(candles, latest_close=1020.0)
    assert classify_bucket(ev) == "NEAR_ENTRY"


def test_bucket_watch_now_when_regime_unknown_but_structure_full() -> None:
    candles = _approved_setup_candles()
    # regime=unknown blocks the evaluator but leaves the setup watchable.
    cfg = type("Cfg", (), {"strategies": {
        "SMC_LIQUIDITY_REVERSAL_RESEARCH": {
            "market_filter": {"block_if_market_regime": ["unknown"]},
        }
    }})()
    # Use a close further from entry so proximity logic doesn't pick
    # NEAR_ENTRY for us. Entry ~ 1017, close 1000 → ext -1.67% which
    # is inside the 1.5% NEAR_ENTRY band when taken as abs(); to make
    # WATCH_NOW we move further away but stay below the 3% extension
    # cap. Easiest is to use a close that makes ext exactly 2% below.
    ev = _evaluate(
        candles, cfg=cfg, market_regime="unknown", latest_close=997.0,
    )
    assert classify_bucket(ev) == "WATCH_NOW"


# ---------------------------------------------------------------------------
# Quality score
# ---------------------------------------------------------------------------
def test_score_in_range_and_structure_points_granted() -> None:
    ev = _evaluate(_approved_setup_candles(), latest_close=1020.0)
    score, breakdown = score_setup(ev)
    assert 0 <= score <= 100
    assert breakdown.get("full_structure") == 35
    assert breakdown.get("stop_distance_ok") == 15
    assert breakdown.get("rr_ok") == 15


def test_score_penalties_for_bad_setup() -> None:
    flat = [
        Candle(timestamp=f"d{i:03d}", open=100, high=101, low=99, close=100,
               volume=0)
        for i in range(30)
    ]
    ev = _evaluate(flat)
    score, breakdown = score_setup(ev)
    assert score == 0  # clamp kicks in on heavy penalties
    assert breakdown.get("incomplete_structure") == -30


def test_score_cannot_override_hard_rejection() -> None:
    """A high score must not make ``approved_for_dry_run`` flip."""
    ev = _evaluate(_approved_setup_candles(), market_regime="risk_off")
    row = build_scan_row(ev)
    # Score can still be computed, but the evaluation itself stays
    # rejected, no matter what the score is.
    assert ev.approved_for_dry_run is False
    assert ev.execution_allowed is False
    assert row.bucket == "BLOCKED"
    # Explicit: even in the top_by_score / closest_to_entry helpers,
    # BLOCKED rows never appear in top_by_score.
    batch = ScanBatch(date="2025-01-01", timeframe="daily", rows=[row])
    assert all(r.bucket != "BLOCKED" for r in batch.top_by_score(5))


# ---------------------------------------------------------------------------
# Batch summary JSON
# ---------------------------------------------------------------------------
def test_batch_to_dict_matches_documented_schema() -> None:
    ev = _evaluate(_approved_setup_candles(), latest_close=1020.0)
    row = build_scan_row(ev)
    batch = ScanBatch(date="2025-04-24", timeframe="daily", rows=[row])
    data = batch.to_dict()
    assert data["date"] == "2025-04-24"
    assert data["timeframe"] == "daily"
    assert data["symbols_scanned"] == 1
    assert set(data["buckets"].keys()) >= set(BUCKETS)
    assert isinstance(data["top_by_score"], list)
    assert isinstance(data["closest_to_entry"], list)
    assert data["execution_allowed"] is False
    assert data["research_only"] is True


# ---------------------------------------------------------------------------
# Telegram digest privacy
# ---------------------------------------------------------------------------
def test_digest_does_not_include_account_numbers_or_dollars() -> None:
    ev = _evaluate(_approved_setup_candles(), latest_close=1020.0,
                   account_equity=1_000_000.0)
    row = build_scan_row(ev)
    batch = ScanBatch(date="2025-04-24", timeframe="daily", rows=[row])
    text = format_telegram_digest(batch)
    # digest never names account numbers, dollar-signed risk, or
    # account summary phrases.
    assert "DU" not in text
    assert "$" not in text
    assert "net_liquidation" not in text.lower()
    assert "account_equity" not in text.lower()
    # It does include the required headline.
    assert "SMC Watchlist Research Digest" in text


def test_digest_plain_text_matches_when_parse_mode_none() -> None:
    ev = _evaluate(_approved_setup_candles(), latest_close=1020.0)
    batch = ScanBatch(date="2025-04-24", timeframe="daily",
                      rows=[build_scan_row(ev)])
    text = format_telegram_digest(batch, parse_mode=None)
    assert "<b>" not in text
    assert "SMC Watchlist Research Digest" in text


def test_digest_includes_regime_summary_lines() -> None:
    """Prompt 8.2 requirement: the digest must carry the regime,
    confidence, missing fields, and new-positions flag so the operator
    sees them alongside the SMC buckets."""
    ev = _evaluate(_approved_setup_candles(), latest_close=1020.0)
    batch = ScanBatch(
        date="2026-04-24",
        timeframe="daily",
        rows=[build_scan_row(ev)],
        market_regime="neutral",
        regime_confidence="medium",
        regime_missing_fields=["VIX", "VIX3M"],
        research_scans_allowed=True,
        new_positions_allowed=False,
    )
    for mode in ("HTML", None):
        text = format_telegram_digest(batch, parse_mode=mode)
        assert "Market regime: neutral (confidence=medium)" in text
        assert "Missing: VIX, VIX3M" in text
        assert "New positions allowed: no" in text
        assert "Research only: yes" in text


# ---------------------------------------------------------------------------
# Safety invariant
# ---------------------------------------------------------------------------
def test_scanner_module_has_no_broker_imports() -> None:
    mod = importlib.import_module("bot.smc_scanner")
    src = Path(mod.__file__).read_text()
    assert "from .broker" not in src
    assert "import bot.broker" not in src
    assert ".place_order(" not in src


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------
def _patch_project_root(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bot import cli as cli_module
    from bot import config as config_module

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_project)
    monkeypatch.setattr(
        cli_module, "load_config",
        lambda **kw: config_module.load_config(project_root=tmp_project, **kw),
    )


def test_scan_smc_watchlist_writes_summary_and_never_places_orders(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_project_root(tmp_project, monkeypatch)
    candles_dir = tmp_project / "data" / "candles_demo"
    for sym in ("AAA", "BBB"):
        _write_csv(candles_dir / f"{sym}.csv", _approved_setup_candles())

    watchlist_path = tmp_project / "config" / "watchlist.yaml"
    watchlist_path.write_text(
        "equities:\n"
        "  - {symbol: AAA, exchange: SMART, currency: USD}\n"
        "  - {symbol: BBB, exchange: SMART, currency: USD}\n",
        encoding="utf-8",
    )

    from bot import broker as broker_module

    def _boom(*_a, **_kw):  # pragma: no cover - must never be called
        raise AssertionError(
            "place_order must not be invoked from scan-smc-watchlist"
        )

    monkeypatch.setattr(broker_module.Broker, "place_order", _boom)

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan-smc-watchlist",
            "--candles-dir", str(candles_dir),
            "--account-equity", "100000",
            "--market-regime", "neutral",
            "--limit", "2",
        ],
    )
    assert result.exit_code == 0, result.output

    summary_files = list(
        (tmp_project / "data" / "smc_setups").glob("*-watchlist-summary.json")
    )
    assert summary_files, f"no summary written; output:\n{result.output}"
    payload = json.loads(summary_files[0].read_text())
    assert payload["symbols_scanned"] == 2
    assert payload["execution_allowed"] is False
    assert payload["research_only"] is True
    assert "buckets" in payload and set(payload["buckets"]).issuperset(set(BUCKETS))


def test_scan_smc_watchlist_reads_market_regime_snapshot(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scanner must pick up ``data/market_regime/*.json`` and pass
    ``market_regime=neutral`` to the evaluator so individual rows stop
    rejecting with ``market_regime=unknown blocks new setups`` — which
    is the exact bug Prompt 8.2 is fixing."""
    _patch_project_root(tmp_project, monkeypatch)
    candles_dir = tmp_project / "data" / "candles_demo"
    _write_csv(candles_dir / "AAA.csv", _approved_setup_candles())

    watchlist_path = tmp_project / "config" / "watchlist.yaml"
    watchlist_path.write_text(
        "default_source: static\n"
        "equities:\n"
        "  - {symbol: AAA, exchange: SMART, currency: USD}\n",
        encoding="utf-8",
    )

    # Write a fresh market-regime snapshot that says neutral/medium
    # with VIX/VIX3M missing — exactly what market-regime --ibkr would
    # save in the production flow.
    regime_dir = tmp_project / "data" / "market_regime"
    regime_dir.mkdir(parents=True, exist_ok=True)
    (regime_dir / "2026-04-24.json").write_text(
        json.dumps({
            "market_regime": "neutral",
            "regime_confidence": "medium",
            "new_positions_allowed": False,
            "research_scans_allowed": True,
            "reason": "VIX/VIX3M unavailable; SPY/QQQ fallback.",
            "market_data": {
                "spy_close": 500.0,
                "spy_200ma": 480.0,
                "spy_above_200ma": True,
                "qqq_close": 420.0,
                "qqq_200ma": 400.0,
                "qqq_above_200ma": True,
                "vix": None,
                "vix3m": None,
                "vix_vix3m_ratio": None,
                "missing_fields": ["VIX", "VIX3M"],
            },
        }),
        encoding="utf-8",
    )

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan-smc-watchlist",
            "--candles-dir", str(candles_dir),
            "--account-equity", "100000",
            "--limit", "1",
        ],
    )
    assert result.exit_code == 0, result.output

    # Console header must reflect the snapshot, not `regime=unknown`.
    assert "regime=neutral" in result.output
    assert "confidence=medium" in result.output
    assert "regime=unknown" not in result.output
    # The ``market_regime=unknown blocks new setups`` rejection line
    # must not appear for any symbol.
    assert "market_regime=unknown" not in result.output

    summaries = list(
        (tmp_project / "data" / "smc_setups").glob("*-watchlist-summary.json")
    )
    assert summaries, f"no summary written; output:\n{result.output}"
    payload = json.loads(summaries[0].read_text())
    # Requirement 5: regime fields must be top-level in the summary.
    assert payload["market_regime"] == "neutral"
    assert payload["regime_confidence"] == "medium"
    assert payload["regime_missing_fields"] == ["VIX", "VIX3M"]
    assert payload["research_scans_allowed"] is True
    assert payload["new_positions_allowed"] is False
    # Safety invariant remains: execution never turns on.
    assert payload["execution_allowed"] is False
    assert payload["research_only"] is True


def test_scan_smc_watchlist_missing_snapshot_uses_neutral_fallback(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no snapshot at all, the scanner must not crash and must
    not silently label everything ``unknown``; it prints a notice and
    defaults the label to ``neutral``."""
    _patch_project_root(tmp_project, monkeypatch)
    candles_dir = tmp_project / "data" / "candles_demo"
    _write_csv(candles_dir / "AAA.csv", _approved_setup_candles())

    watchlist_path = tmp_project / "config" / "watchlist.yaml"
    watchlist_path.write_text(
        "default_source: static\n"
        "equities:\n"
        "  - {symbol: AAA, exchange: SMART, currency: USD}\n",
        encoding="utf-8",
    )
    # Deliberately do NOT create data/market_regime/ or data/pre_open_news/.

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan-smc-watchlist",
            "--candles-dir", str(candles_dir),
            "--account-equity", "100000",
            "--limit", "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "No market-regime snapshot found" in result.output
    assert "regime=neutral" in result.output
    summaries = list(
        (tmp_project / "data" / "smc_setups").glob("*-watchlist-summary.json")
    )
    assert summaries
    payload = json.loads(summaries[0].read_text())
    assert payload["execution_allowed"] is False
    assert payload["market_regime"] == "neutral"


def test_scan_smc_watchlist_never_places_orders_with_snapshot(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end guarantee: even when the snapshot says ``neutral``
    with ``new_positions_allowed=false``, place_order is not called."""
    _patch_project_root(tmp_project, monkeypatch)
    candles_dir = tmp_project / "data" / "candles_demo"
    _write_csv(candles_dir / "AAA.csv", _approved_setup_candles())

    watchlist_path = tmp_project / "config" / "watchlist.yaml"
    watchlist_path.write_text(
        "default_source: static\n"
        "equities:\n"
        "  - {symbol: AAA, exchange: SMART, currency: USD}\n",
        encoding="utf-8",
    )
    regime_dir = tmp_project / "data" / "market_regime"
    regime_dir.mkdir(parents=True, exist_ok=True)
    (regime_dir / "2026-04-24.json").write_text(
        json.dumps({
            "market_regime": "neutral",
            "regime_confidence": "medium",
            "new_positions_allowed": False,
            "research_scans_allowed": True,
            "market_data": {"missing_fields": ["VIX", "VIX3M"]},
        }),
        encoding="utf-8",
    )

    from bot import broker as broker_module

    def _boom(*_a, **_kw):  # pragma: no cover - must never be called
        raise AssertionError("place_order must not be invoked")

    monkeypatch.setattr(broker_module.Broker, "place_order", _boom)

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan-smc-watchlist",
            "--candles-dir", str(candles_dir),
            "--account-equity", "100000",
            "--limit", "1",
        ],
    )
    assert result.exit_code == 0, result.output


def test_scan_smc_watchlist_telegram_falls_back_without_credentials(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_project_root(tmp_project, monkeypatch)
    candles_dir = tmp_project / "data" / "candles_demo"
    _write_csv(candles_dir / "AAA.csv", _approved_setup_candles())

    watchlist_path = tmp_project / "config" / "watchlist.yaml"
    watchlist_path.write_text(
        "equities:\n"
        "  - {symbol: AAA, exchange: SMART, currency: USD}\n",
        encoding="utf-8",
    )

    # Ensure credentials are absent so the telegram call falls back
    # to writing memory/DAILY-SUMMARY.md without crashing.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan-smc-watchlist",
            "--candles-dir", str(candles_dir),
            "--account-equity", "100000",
            "--market-regime", "neutral",
            "--limit", "1",
            "--telegram",
        ],
    )
    assert result.exit_code == 0, result.output
    fallback = tmp_project / "memory" / "DAILY-SUMMARY.md"
    assert fallback.exists(), "telegram fallback file should have been created"
    content = fallback.read_text()
    assert "SMC Watchlist Research Digest" in content
    # Privacy: no broker account number appears in the fallback.
    assert "DU" not in content
