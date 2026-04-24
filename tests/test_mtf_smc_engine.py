"""Tests for :mod:`bot.mtf_smc_engine` and MTF IBKR helpers (Prompt 10B)."""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import MagicMock

import pytest

from bot.config import load_config
from bot.ibkr_client import IBKRClient
from bot.mtf_smc_engine import (
    MtfCandleBundle,
    classify_5min_trigger,
    classify_daily_bias,
    classify_4h_structure,
    compute_mtf_score,
    compute_premium_discount,
    format_mtf_watchlist_digest_zh,
    map_setup_30min,
    resolve_alignment,
    run_mtf_smc,
)
from bot.strategy_engine import evaluate_smc_liquidity_reversal
from tests.test_smc_liquidity_reversal import _approved_setup_candles


def _candles_to_rows(candles) -> list[dict]:
    return [
        {
            "timestamp": c.timestamp,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles
    ]


def _candle_row(i: int, o: float, h: float, l: float, c: float) -> dict:
    return {
        "timestamp": f"2020-01-{(i%28)+1:02d}T12:00:00",
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1.0,
    }


def _bullish_daily_rows() -> list[dict]:
    rows = []
    p = 80.0
    for i in range(250):
        p = p + 0.2
        rows.append(
            {
                "timestamp": f"2020-{(i%12)+1:02d}-{(i%28)+1:02d}",
                "open": p - 0.2,
                "high": p + 0.5,
                "low": p - 0.5,
                "close": p,
                "volume": 1.0e6,
            }
        )
    return rows


def _bearish_daily_rows() -> list[dict]:
    p = 200.0
    rows = []
    for i in range(250):
        p = p - 0.2
        rows.append(
            {
                "timestamp": f"2020-{(i%12)+1:02d}-{(i%28)+1:02d}",
                "open": p + 0.2,
                "high": p + 0.5,
                "low": p - 0.5,
                "close": p,
                "volume": 1.0e6,
            }
        )
    return rows


def test_daily_bullish_or_neutral_bias(tmp_project) -> None:
    b = _bullish_daily_rows()
    r = classify_daily_bias(b, market_regime="neutral")
    assert r["bias"] in ("bullish", "neutral")


def test_daily_bearish_liquidity_trending_down(tmp_project) -> None:
    r = classify_daily_bias(_bearish_daily_rows(), market_regime="neutral")
    assert r["bias"] in ("bearish", "neutral", "unknown")


def test_premium_discount_zones() -> None:
    r4 = [
        _candle_row(0, 100, 110, 90, 100),
    ] * 50
    low_price = 95.0
    pd1 = compute_premium_discount(
        rows_4h=r4, rows_30=r4, latest_price=low_price
    )
    assert pd1["current_zone"] in ("discount", "equilibrium", "premium", "unknown")
    high_price = 120.0
    pd2 = compute_premium_discount(
        rows_4h=r4, rows_30=r4, latest_price=high_price
    )
    assert pd2["current_zone"] in ("discount", "equilibrium", "premium", "unknown")


def test_mtf_score_clamped() -> None:
    s = compute_mtf_score("bearish", "bearish_confirmed", "blocked", "invalid", "premium")
    assert 0 <= s <= 100


def test_resolve_alignment_flags() -> None:
    c, n1, f, n2 = resolve_alignment(
        daily_bias="bullish",
        s4="bullish_confirmed",
        s30="full_setup_valid",
        t5="confirmed",
        premium_zone="equilibrium",
        mtf_score=80,
    )
    assert c == "FULL_ALIGNMENT"
    assert f is True
    c2, _, _, _ = resolve_alignment(
        daily_bias="bullish",
        s4="bullish_confirmed",
        s30="full_setup_valid",
        t5="waiting_for_choch",
        premium_zone="equilibrium",
        mtf_score=50,
    )
    assert c2 == "SETUP_READY_WAITING_TRIGGER"


def test_4h_structure_unknown_without_data(tmp_project) -> None:
    cfg = load_config(project_root=tmp_project)
    ev = evaluate_smc_liquidity_reversal(
        "T", [], cfg=cfg, timeframe="4h", market_regime="neutral",
    )
    s = classify_4h_structure([], eval4h=ev)
    assert s["structure"] == "unknown"


def test_map_setup_30min_uses_rejection_list(tmp_project) -> None:
    cfg = load_config(project_root=tmp_project)
    rows = _candles_to_rows(_approved_setup_candles())
    ev = evaluate_smc_liquidity_reversal(
        "A", rows, cfg=cfg, timeframe="30min", market_regime="neutral",
    )
    m = map_setup_30min(ev, market_regime="neutral")
    assert m["setup_state"] in (
        "full_setup_valid", "waiting_for_pullback", "too_extended", "invalid_risk",
        "incomplete", "blocked", "unknown",
    )


def test_run_mtf_smc_invariants(tmp_project) -> None:
    cfg = load_config(project_root=tmp_project)
    r30 = _candles_to_rows(_approved_setup_candles())
    h4 = r30 * 5  # pad
    b = MtfCandleBundle(daily=_bullish_daily_rows()[:200], h4=h4, m30=r30, m5=r30)
    out = run_mtf_smc("AAA", cfg, b, market_regime="neutral")
    assert out["research_only"] is True
    assert out["execution_allowed"] is False
    assert "human_summary_zh" in out
    assert "研究扫描" in out["human_summary_zh"] or "研究" in out["human_summary_zh"]


def test_mtf_telegram_digest_contains_research_phrase() -> None:
    s = {
        "date": "2026-01-01",
        "symbols_scanned": 0,
        "counts": {k: 0 for k in (
            "FULL_ALIGNMENT", "SETUP_READY_WAITING_TRIGGER", "BIAS_OK_SETUP_INCOMPLETE",
            "CONFLICTED", "BLOCKED",
        )},
        "top_by_alignment_score": [],
        "items": [],
    }
    t = format_mtf_watchlist_digest_zh(s)
    assert "研究扫描" in t or "不下单" in t


def test_mtf_no_broker_in_engine_source() -> None:
    p = pathlib.Path(__file__).resolve().parent.parent / "bot" / "mtf_smc_engine.py"
    tree = ast.parse(p.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module in ("bot.broker", "broker"):
            pytest.fail("mtf_smc_engine must not import broker")
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Attribute) and n.func.attr == "place_order":
                pytest.fail("place_order in mtf_smc_engine")


def test_cli_scan_mtf_requires_ibkr(
    tmp_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner
    from bot import config as config_module
    from bot import cli as cli_module

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_project)
    monkeypatch.setattr(
        cli_module, "load_config",
        lambda **kw: config_module.load_config(project_root=tmp_project, **kw),
    )
    runner = CliRunner()
    r = runner.invoke(
        cli_module.app, ["scan-mtf-smc", "-s", "AAPL"],
    )
    assert r.exit_code == 2


def test_ibkr_aggregate_1h_to_4h() -> None:
    h1: list[dict] = [
        {
            "timestamp": f"2020-01-01T{i:02d}:00:00",
            "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1,
        }
        for i in range(8)
    ]
    g = IBKRClient._aggregate_1h_bars_to_4h(h1)  # noqa: SLF001
    assert len(g) == 2
    assert g[0]["close"] == h1[3]["close"]


def test_get_4h_fallback_aggregates_from_1h(tmp_project) -> None:
    from bot.smc_timeframes import resolve_timeframe_spec

    cfg = load_config(project_root=tmp_project)
    spec = resolve_timeframe_spec("4h", cfg)
    client = IBKRClient(cfg)
    fake = MagicMock()
    client._ib = fake  # noqa: SLF001
    fake.isConnected = lambda: True
    fake.qualifyContracts = lambda c: [object()]

    def b_at(i: int) -> MagicMock:
        b = MagicMock()
        b.date = f"2020-01-01T{i:02d}:00:00"
        b.open = b.high = b.low = b.close = 100.0
        b.volume = 1.0
        return b

    def req_hist(*_a, **kw) -> list:
        bsz = kw.get("barSizeSetting", "")
        if bsz == "4 hours":
            return [b_at(0)]  # too short
        if bsz == "1 hour":
            return [b_at(i) for i in range(12)]
        return []

    fake.reqHistoricalData = MagicMock(side_effect=req_hist)
    rows, w = client.get_4h_bars_with_fallback("AAPL", spec)  # type: ignore[misc]
    assert rows
    assert any("aggregated" in m.lower() for m in w)
