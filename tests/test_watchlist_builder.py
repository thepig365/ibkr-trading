"""Tests for :mod:`bot.watchlist_builder` and the CLI integration."""

from __future__ import annotations

import importlib
import json
import random
from pathlib import Path
from typing import Sequence

import pytest

from bot.watchlist_builder import (
    DEFAULT_DYNAMIC_CFG,
    WatchlistCandidate,
    build_candidate_from_bars,
    build_dynamic_watchlist,
    classify_relative_volume,
    compute_volume_rank_score,
    load_dynamic_watchlist,
    save_dynamic_watchlist,
)


# ---------------------------------------------------------------------------
# Fixtures: synthetic OHLCV generators
# ---------------------------------------------------------------------------
def _bars(
    n: int,
    *,
    close: float,
    volume: float,
    atr_range: float = 0.0,
    seed: int = 0,
) -> list[dict[str, float | str]]:
    """Deterministic OHLCV bars with a controllable ATR range."""
    rnd = random.Random(seed)
    out: list[dict[str, float | str]] = []
    for i in range(n):
        jitter = rnd.uniform(-atr_range, atr_range) if atr_range else 0.0
        o = close + jitter
        c = close + jitter * 0.5
        h = max(o, c) + atr_range
        l = min(o, c) - atr_range
        out.append(
            {
                "timestamp": f"d{i:03d}",
                "open": round(o, 4),
                "high": round(h, 4),
                "low": round(l, 4),
                "close": round(c, 4),
                "volume": volume,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Unit tests: metric helpers
# ---------------------------------------------------------------------------
def test_classify_relative_volume_thresholds() -> None:
    assert classify_relative_volume(None) == "unknown"
    assert classify_relative_volume(1.49) == "normal_activity"
    assert classify_relative_volume(1.5) == "elevated_activity"
    assert classify_relative_volume(1.99) == "elevated_activity"
    assert classify_relative_volume(2.0) == "strong_activity"
    assert classify_relative_volume(5.0) == "strong_activity"


def test_build_candidate_collects_core_metrics() -> None:
    bars = _bars(30, close=100.0, volume=1_000_000, atr_range=1.0, seed=1)
    c = build_candidate_from_bars("TEST", bars)
    assert c.symbol == "TEST"
    assert c.latest_price is not None
    assert c.avg_20d_volume == 1_000_000
    assert c.avg_20d_dollar_volume is not None
    # current_volume falls back to the latest bar's volume when the
    # caller does not pass an override — that is the intended path on
    # most paper accounts.
    assert c.current_volume == 1_000_000
    assert c.current_dollar_volume is not None
    assert c.relative_volume is not None
    assert c.atr_pct is not None


def test_build_candidate_handles_missing_bars_gracefully() -> None:
    c = build_candidate_from_bars("NOPE", [])
    assert c.symbol == "NOPE"
    assert "bars" in c.missing_fields
    assert c.latest_price is None


def test_build_candidate_marks_missing_current_volume() -> None:
    bars = _bars(30, close=100.0, volume=0, atr_range=1.0)
    c = build_candidate_from_bars("FLAT", bars, current_volume=None)
    # latest_bar_volume=0 → cur_vol None → flagged.
    assert "current_volume" in c.missing_fields
    assert c.current_dollar_volume is None


def test_volume_rank_score_degrades_when_current_is_missing() -> None:
    c = WatchlistCandidate(
        symbol="X",
        avg_20d_dollar_volume=50_000_000,
        atr_pct=5.0,
    )
    score = compute_volume_rank_score(
        c, max_cur_dv=None, max_avg_dv=100_000_000,
        max_rel_vol=None, max_vol_proxy=10.0,
    )
    assert score is not None and 0.0 < score <= 1.0


# ---------------------------------------------------------------------------
# Builder: bucket selection + filters + max_symbols
# ---------------------------------------------------------------------------
def _candidate(
    symbol: str,
    *,
    price: float = 50.0,
    avg_dv: float | None = 100_000_000,
    cur_dv: float | None = 50_000_000,
    rel_vol: float | None = 1.0,
    atr_pct: float | None = 3.0,
    beta: float | None = None,
    rv_20d: float | None = None,
) -> WatchlistCandidate:
    return WatchlistCandidate(
        symbol=symbol,
        latest_price=price,
        avg_20d_volume=(avg_dv or 0) / max(price, 1),
        avg_20d_dollar_volume=avg_dv,
        current_dollar_volume=cur_dv,
        current_volume=(cur_dv or 0) / max(price, 1),
        relative_volume=rel_vol,
        volume_activity=classify_relative_volume(rel_vol),
        atr_pct=atr_pct,
        realized_vol_20d=rv_20d,
        beta=beta,
    )


def test_static_core_is_always_present_when_enabled() -> None:
    universe = [_candidate("AAA"), _candidate("BBB")]
    w = build_dynamic_watchlist(
        universe_candidates=universe,
        static_core=["NVDA", "AAPL"],
        cfg={"include_static_core": True},
        today="2025-04-24",
    )
    kept = {r.symbol for r in w.symbols}
    assert {"NVDA", "AAPL"}.issubset(kept)


def test_price_filter_blocks_penny_stocks() -> None:
    cheap = _candidate("PENNY", price=3.0, cur_dv=50_000_000, avg_dv=50_000_000)
    w = build_dynamic_watchlist(
        universe_candidates=[cheap],
        static_core=[],
        cfg={"include_static_core": False},
        today="2025-04-24",
    )
    row = next(r for r in w.symbols if r.symbol == "PENNY")
    assert row.blocked is True
    assert row.block_reason and "price<" in row.block_reason


def test_avg_dollar_volume_below_min_is_blocked() -> None:
    illiquid = _candidate("ILLIQ", avg_dv=1_000_000, cur_dv=2_000_000)
    w = build_dynamic_watchlist(
        universe_candidates=[illiquid],
        static_core=[],
        cfg={"include_static_core": False},
        today="2025-04-24",
    )
    row = next(r for r in w.symbols if r.symbol == "ILLIQ")
    assert row.blocked is True
    assert "avg_20d_dollar_volume" in (row.block_reason or "")


def test_current_dollar_volume_below_min_is_blocked() -> None:
    weak = _candidate("WEAK", cur_dv=1_000_000, avg_dv=500_000_000)
    w = build_dynamic_watchlist(
        universe_candidates=[weak],
        static_core=[],
        cfg={"include_static_core": False},
        today="2025-04-24",
    )
    row = next(r for r in w.symbols if r.symbol == "WEAK")
    assert row.blocked is True
    assert "current_dollar_volume" in (row.block_reason or "")


def test_missing_current_volume_does_not_crash_and_rank_still_usable() -> None:
    noCur = _candidate("NOCUR", cur_dv=None, rel_vol=None, avg_dv=60_000_000, atr_pct=5.0)
    noCur.missing_fields.append("current_volume")
    noCur.missing_fields.append("relative_volume")
    w = build_dynamic_watchlist(
        universe_candidates=[noCur],
        static_core=[],
        cfg={"include_static_core": False},
        today="2025-04-24",
    )
    row = next(r for r in w.symbols if r.symbol == "NOCUR")
    assert row.blocked is False
    assert row.volume_rank_score is not None


def test_relative_volume_bucket_requires_minimum_threshold() -> None:
    low_rel = _candidate("LOWREL", rel_vol=1.2, cur_dv=50_000_000)
    high_rel = _candidate("HIREL", rel_vol=3.0, cur_dv=50_000_000)
    w = build_dynamic_watchlist(
        universe_candidates=[low_rel, high_rel],
        static_core=[],
        cfg={"include_static_core": False},
        today="2025-04-24",
    )
    low_row = next(r for r in w.symbols if r.symbol == "LOWREL")
    hi_row = next(r for r in w.symbols if r.symbol == "HIREL")
    assert "high_relative_volume" not in low_row.reason
    assert "high_relative_volume" in hi_row.reason


def test_leveraged_etf_is_excluded_by_default() -> None:
    tqqq = _candidate("TQQQ", cur_dv=500_000_000, avg_dv=1_000_000_000)
    w = build_dynamic_watchlist(
        universe_candidates=[tqqq],
        static_core=[],
        cfg={"include_static_core": False},
        today="2025-04-24",
    )
    row = next(r for r in w.symbols if r.symbol == "TQQQ")
    assert row.blocked is True
    assert row.block_reason == "leveraged_etf_excluded"


def test_blocked_symbols_from_news_are_marked() -> None:
    uhoh = _candidate("UHOH", cur_dv=60_000_000, avg_dv=120_000_000)
    w = build_dynamic_watchlist(
        universe_candidates=[uhoh],
        static_core=[],
        cfg={"include_static_core": False},
        blocked_symbols=["UHOH"],
        today="2025-04-24",
    )
    row = next(r for r in w.symbols if r.symbol == "UHOH")
    assert row.blocked is True
    assert row.block_reason == "symbol_blocked_by_news_or_policy"


def test_max_symbols_cap_is_respected_without_dropping_static_core() -> None:
    universe = [
        _candidate(f"SYM{i:02d}", cur_dv=10_000_000 + i * 1_000_000,
                   avg_dv=50_000_000 + i * 1_000_000)
        for i in range(60)
    ]
    w = build_dynamic_watchlist(
        universe_candidates=universe,
        static_core=["NVDA", "AAPL", "MSFT"],
        cfg={"max_symbols": 10, "include_static_core": True},
        today="2025-04-24",
    )
    syms = [r.symbol for r in w.symbols]
    assert len(syms) <= 10
    # Static core always wins even when the cap is tight.
    assert {"NVDA", "AAPL", "MSFT"}.issubset(set(syms))


def test_deduplication_preserves_reason_tags() -> None:
    # A single symbol that qualifies for three buckets must have all
    # three tags and must appear only once in the final list.
    hot = _candidate(
        "HOT", cur_dv=500_000_000, avg_dv=400_000_000,
        rel_vol=2.5, atr_pct=8.0,
    )
    w = build_dynamic_watchlist(
        universe_candidates=[hot],
        static_core=["HOT"],
        today="2025-04-24",
    )
    hot_rows = [r for r in w.symbols if r.symbol == "HOT"]
    assert len(hot_rows) == 1
    tags = set(hot_rows[0].reason)
    assert "static_core" in tags
    assert "high_current_dollar_volume" in tags
    assert "high_relative_volume" in tags


def test_atr_pct_acts_as_high_beta_proxy_when_beta_missing() -> None:
    volatile = _candidate(
        "VOL", cur_dv=50_000_000, avg_dv=60_000_000, atr_pct=5.5, beta=None,
    )
    boring = _candidate(
        "BORING", cur_dv=50_000_000, avg_dv=60_000_000, atr_pct=1.0, beta=None,
    )
    w = build_dynamic_watchlist(
        universe_candidates=[volatile, boring],
        static_core=[],
        cfg={"include_static_core": False},
        today="2025-04-24",
    )
    vol_row = next(r for r in w.symbols if r.symbol == "VOL")
    bor_row = next(r for r in w.symbols if r.symbol == "BORING")
    assert "high_volatility_proxy" in vol_row.reason
    assert "high_volatility_proxy" not in bor_row.reason


def test_low_liquidity_high_beta_name_is_still_blocked() -> None:
    """High ATR% cannot override the liquidity filter."""
    risky = _candidate(
        "RISKY", cur_dv=100_000, avg_dv=200_000,
        atr_pct=10.0, beta=2.5, price=8.0,
    )
    w = build_dynamic_watchlist(
        universe_candidates=[risky],
        static_core=[],
        cfg={"include_static_core": False},
        today="2025-04-24",
    )
    row = next(r for r in w.symbols if r.symbol == "RISKY")
    assert row.blocked is True


# ---------------------------------------------------------------------------
# Output JSON
# ---------------------------------------------------------------------------
def test_output_schema_matches_spec(tmp_project: Path, monkeypatch) -> None:
    from bot import config as config_module
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_project)
    cfg = config_module.load_config(project_root=tmp_project)
    w = build_dynamic_watchlist(
        universe_candidates=[_candidate("NVDA")],
        static_core=["NVDA"],
        today="2025-04-24",
        source="test",
    )
    path = save_dynamic_watchlist(cfg, w)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["date"] == "2025-04-24"
    assert payload["source"] == "test"
    assert payload["execution_allowed"] is False
    assert payload["research_only"] is True
    row = payload["symbols"][0]
    for field in (
        "symbol", "reason", "latest_price", "current_volume",
        "current_dollar_volume", "avg_20d_volume", "avg_20d_dollar_volume",
        "relative_volume", "volume_activity", "volume_rank_score",
        "beta", "atr_pct", "realized_vol_20d", "blocked", "missing_fields",
    ):
        assert field in row, f"missing field: {field}"


def test_load_dynamic_watchlist_round_trips(tmp_project: Path, monkeypatch) -> None:
    from bot import config as config_module
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_project)
    cfg = config_module.load_config(project_root=tmp_project)
    original = build_dynamic_watchlist(
        universe_candidates=[_candidate("NVDA"), _candidate("AAPL")],
        static_core=["NVDA", "AAPL"],
        today="2025-04-24",
    )
    save_dynamic_watchlist(cfg, original)
    loaded = load_dynamic_watchlist(cfg, date="2025-04-24")
    assert loaded is not None
    assert {r.symbol for r in loaded.symbols} == {"NVDA", "AAPL"}


# ---------------------------------------------------------------------------
# Safety invariants
# ---------------------------------------------------------------------------
def test_watchlist_builder_has_no_broker_imports() -> None:
    mod = importlib.import_module("bot.watchlist_builder")
    src = Path(mod.__file__).read_text()
    assert "from .broker" not in src
    assert "import bot.broker" not in src
    assert ".place_order(" not in src


def test_persisted_json_never_sets_execution_allowed_true(tmp_project: Path, monkeypatch) -> None:
    from bot import config as config_module
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_project)
    cfg = config_module.load_config(project_root=tmp_project)
    w = build_dynamic_watchlist(
        universe_candidates=[_candidate("NVDA")],
        static_core=["NVDA"],
        today="2025-04-24",
    )
    path = save_dynamic_watchlist(cfg, w)
    payload = json.loads(path.read_text())
    assert payload["execution_allowed"] is False
    assert payload["research_only"] is True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _patch_project_root(tmp_project: Path, monkeypatch) -> None:
    from bot import cli as cli_module
    from bot import config as config_module

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_project)
    monkeypatch.setattr(
        cli_module, "load_config",
        lambda **kw: config_module.load_config(project_root=tmp_project, **kw),
    )


def test_build_watchlist_cli_writes_json_and_never_places_orders(
    tmp_project: Path, monkeypatch
) -> None:
    _patch_project_root(tmp_project, monkeypatch)
    from bot import broker as broker_module

    def _boom(*_a, **_kw):  # pragma: no cover - must never be called
        raise AssertionError("place_order must not be invoked from build-watchlist")

    monkeypatch.setattr(broker_module.Broker, "place_order", _boom)

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["build-watchlist", "--limit", "5", "--no-save"])
    assert result.exit_code == 0, result.output


def test_scan_smc_watchlist_dynamic_builds_when_missing(
    tmp_project: Path, monkeypatch
) -> None:
    _patch_project_root(tmp_project, monkeypatch)
    # Force the watchlist config so --source dynamic has work to do.
    watchlist_path = tmp_project / "config" / "watchlist.yaml"
    watchlist_path.write_text(
        "default_source: dynamic\n"
        "static_core:\n  - NVDA\n  - AAPL\n"
        "dynamic:\n"
        "  enabled: true\n"
        "  max_symbols: 5\n"
        "  include_static_core: true\n"
        "  seed_universe:\n    - NVDA\n    - AAPL\n"
        "equities:\n  - {symbol: NVDA, exchange: NASDAQ, currency: USD}\n",
        encoding="utf-8",
    )

    from bot import broker as broker_module

    def _boom(*_a, **_kw):  # pragma: no cover - must never be called
        raise AssertionError("place_order must not be invoked from scan-smc-watchlist")

    monkeypatch.setattr(broker_module.Broker, "place_order", _boom)

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan-smc-watchlist",
            "--source", "dynamic",
            "--market-regime", "neutral",
            "--account-equity", "100000",
            "--limit", "2",
        ],
    )
    # Dynamic build without IBKR will leave static_core rows with
    # missing bars; the scanner just logs them. Exit code 0 is enough
    # to prove the code path runs end-to-end without placing orders.
    assert result.exit_code == 0, result.output
    # Dynamic watchlist JSON was created even though no IBKR data
    # was available.
    wl = list(
        (tmp_project / "data" / "watchlists").glob("*-dynamic-watchlist.json")
    )
    assert wl, f"dynamic watchlist json missing; output:\n{result.output}"
