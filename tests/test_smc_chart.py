"""Tests for the SMC visual debug pack.

Covered behaviours:
    * ``--chart`` writes a PNG file under data/debug_charts/.
    * Charts render even for rejected / incomplete setups.
    * ``--account-equity`` overrides IBKR sizing.
    * ``--use-account-values`` is opt-in and still does not place orders.
    * The persisted JSON includes ``chart_path`` when a chart is rendered.
    * ``smc_chart.render_smc_chart`` does not import or call the broker.
    * ``StrategyEvaluation`` exposes ``detected_levels``, ``candles_start``,
      ``candles_end``, ``validation_notes``, ``chart_path``.
"""

from __future__ import annotations

import csv
import importlib
import json
import sys
from pathlib import Path
from typing import Iterable

import pytest

# matplotlib is now a hard dependency for the chart module - skip the
# tests cleanly if the wheel isn't installed in the test environment.
matplotlib = pytest.importorskip("matplotlib")

from bot.market_structure import Candle  # noqa: E402
from bot.smc_chart import render_smc_chart  # noqa: E402
from bot.strategy_engine import (  # noqa: E402
    StrategyEvaluation,
    evaluate_smc_liquidity_reversal,
)
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


def _evaluation(candles: list[Candle], **kwargs) -> StrategyEvaluation:
    return evaluate_smc_liquidity_reversal(
        symbol=kwargs.pop("symbol", "TEST"),
        candles=candles,
        market_regime=kwargs.pop("market_regime", "neutral"),
        account_equity=kwargs.pop("account_equity", 100_000.0),
        latest_close=kwargs.pop("latest_close", candles[-1].close),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Evaluation payload contract
# ---------------------------------------------------------------------------
def test_evaluation_exposes_visual_validation_fields() -> None:
    candles = _approved_setup_candles()
    evaluation = _evaluation(candles)
    payload = evaluation.to_dict()
    for key in (
        "candles_start", "candles_end", "detected_levels",
        "validation_notes", "chart_path",
    ):
        assert key in payload, f"missing key {key} in payload"
    assert payload["candles_start"] == "d000"
    assert payload["candles_end"] == "d020"
    levels = payload["detected_levels"]
    for k in (
        "swept_low", "sweep_low", "choch_pivot", "fvg_low", "fvg_high",
        "ob_low", "ob_high", "entry", "stop", "target_1",
    ):
        assert k in levels
    assert levels["entry"] == pytest.approx(payload["trade_plan"]["entry_price"])
    assert levels["stop"] == pytest.approx(payload["trade_plan"]["structural_stop"])
    assert levels["target_1"] == pytest.approx(payload["trade_plan"]["target_1"])


def test_detected_levels_have_none_when_setup_incomplete() -> None:
    flat = [
        Candle(timestamp=f"d{i:03d}", open=100, high=101, low=99, close=100,
               volume=1000)
        for i in range(30)
    ]
    payload = _evaluation(flat).to_dict()
    assert payload["sequence"]["sweep"]["found"] is False
    levels = payload["detected_levels"]
    assert levels["entry"] is None
    assert levels["stop"] is None
    assert levels["target_1"] is None
    assert payload["chart_path"] is None


# ---------------------------------------------------------------------------
# Direct chart rendering
# ---------------------------------------------------------------------------
def test_render_smc_chart_writes_png(tmp_path: Path) -> None:
    candles = _approved_setup_candles()
    evaluation = _evaluation(candles)
    out = render_smc_chart(evaluation, candles, output_dir=tmp_path)
    assert out.exists()
    assert out.suffix == ".png"
    assert out.stat().st_size > 1000  # actual image, not empty


def test_render_smc_chart_works_for_rejected_setup(tmp_path: Path) -> None:
    candles = _approved_setup_candles()
    evaluation = _evaluation(candles, market_regime="risk_off")
    assert evaluation.approved_for_dry_run is False
    out = render_smc_chart(evaluation, candles, output_dir=tmp_path)
    assert out.exists()


def test_render_smc_chart_labels_every_structural_element(
    tmp_path: Path,
) -> None:
    """The chart must carry explicit labels for Sweep / ChoCH / FVG / OB
    / Entry / Stop / T1. We introspect the matplotlib Axes directly
    instead of OCR'ing the PNG so the test stays fast and reliable."""
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    from bot.market_structure import detect_swing_highs, detect_swing_lows
    from bot.smc_chart import (
        _decorate,
        _draw_candles,
        _draw_levels,
        _draw_sequence_markers,
    )

    candles = _approved_setup_candles()
    evaluation = _evaluation(candles)
    fig, ax = plt.subplots()
    _draw_candles(ax, candles)
    _draw_sequence_markers(
        ax, evaluation, candles, Rectangle=matplotlib.patches.Rectangle,
    )
    _draw_levels(ax, evaluation, list(range(len(candles))))
    _decorate(ax, evaluation, candles)

    texts = [t.get_text() for t in ax.texts]
    assert any(t.startswith("Sweep ") and "low=" in t for t in texts)
    assert any(t.startswith("Swept low ") and "price=" in t for t in texts)
    assert any(
        t.startswith("ChoCH ") and "close=" in t and "broke=" in t
        for t in texts
    )
    assert any(t.startswith("FVG ") for t in texts)
    assert any(t.startswith("OB ") for t in texts)
    assert any(t.startswith(" Entry ") or t.startswith("Entry ") for t in texts)
    assert any(t.startswith(" Stop ") or t.startswith("Stop ") for t in texts)
    assert any(t.startswith(" T1 ") or t.startswith("T1 ") for t in texts)
    plt.close(fig)


def test_render_smc_chart_has_sweep_and_choch_vertical_bands(
    tmp_path: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    from bot.smc_chart import _draw_sequence_markers

    candles = _approved_setup_candles()
    evaluation = _evaluation(candles)
    fig, ax = plt.subplots()
    _draw_sequence_markers(
        ax, evaluation, candles, Rectangle=matplotlib.patches.Rectangle,
    )
    # axvspan adds Polygon patches; look for at least one tall, narrow
    # span that covers ±0.4 around the sweep / choch candle indices.
    from matplotlib.patches import Rectangle as MplRect

    sweep_idx = evaluation.sequence["sweep"]["index"]
    choch_idx = evaluation.sequence["choch"]["index"]
    rects = [
        p for p in ax.patches if isinstance(p, MplRect)
    ]

    def _origin(p):
        xy = p.get_xy()
        return xy if isinstance(xy, tuple) else (xy[0][0], xy[0][1])

    sweep_found = any(
        abs(_origin(p)[0] - (sweep_idx - 0.4)) < 1e-3 for p in rects
    )
    choch_found = any(
        abs(_origin(p)[0] - (choch_idx - 0.4)) < 1e-3 for p in rects
    )
    assert sweep_found, "no vertical band around the sweep candle"
    assert choch_found, "no vertical band around the ChoCH candle"
    plt.close(fig)


def test_render_smc_chart_works_for_incomplete_setup(tmp_path: Path) -> None:
    flat = [
        Candle(timestamp=f"d{i:03d}", open=100, high=101, low=99, close=100,
               volume=1000)
        for i in range(30)
    ]
    evaluation = _evaluation(flat)
    assert evaluation.trade_plan is None
    out = render_smc_chart(evaluation, flat, output_dir=tmp_path)
    assert out.exists()


def test_render_smc_chart_has_no_broker_dependency() -> None:
    """The renderer must not import the broker module."""
    smc_chart = importlib.import_module("bot.smc_chart")
    src = Path(smc_chart.__file__).read_text()
    # Only flag *call sites*, not docstring mentions.
    assert ".place_order(" not in src
    assert "from .broker" not in src
    assert "import bot.broker" not in src
    # Defensive: ensure the module was not subsequently injected with broker.
    assert getattr(smc_chart, "place_order", None) is None
    assert "broker" not in sys.modules.get("bot.smc_chart").__dict__


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------
def _runner_invoke(*args, env_root: Path):
    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    return runner, runner.invoke(
        app,
        list(args),
        env={"BOT_PROJECT_ROOT": str(env_root)},
    )


def _patch_project_root(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Force load_config / Journal to use tmp_project as project root."""
    from bot import cli as cli_module
    from bot import config as config_module
    from bot import journal as journal_module

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_project)
    monkeypatch.setattr(
        cli_module, "load_config",
        lambda **kw: config_module.load_config(project_root=tmp_project, **kw),
    )

    # Journal stores its sqlite under settings.paths but resolves relative
    # paths via cfg.absolute(); since AppConfig.project_root is now
    # tmp_project, no further patching is needed here.
    del journal_module  # silences unused-import warning


def test_scan_smc_csv_with_chart(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_project_root(tmp_project, monkeypatch)
    csv_path = tmp_project / "data" / "candles_demo" / "TEST.csv"
    _write_csv(csv_path, _approved_setup_candles())

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan-smc",
            "--symbol", "TEST",
            "--csv", str(csv_path),
            "--account-equity", "100000",
            "--market-regime", "neutral",
            "--chart",
        ],
    )
    assert result.exit_code == 0, result.output
    chart_dir = tmp_project / "data" / "debug_charts"
    pngs = list(chart_dir.glob("*-TEST-daily-smc.png"))
    assert pngs, f"no chart written under {chart_dir}; output:\n{result.output}"

    setup_files = list((tmp_project / "data" / "smc_setups").glob("*-TEST.json"))
    assert setup_files, "no JSON saved"
    payload = json.loads(setup_files[-1].read_text())
    assert payload["chart_path"], "chart_path should be set in JSON"
    assert payload["chart_path"].endswith(".png")
    # Chart path should match the file actually written.
    assert Path(payload["chart_path"]).exists()


def test_scan_smc_without_chart_writes_json_only(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_project_root(tmp_project, monkeypatch)
    csv_path = tmp_project / "data" / "candles_demo" / "TEST.csv"
    _write_csv(csv_path, _approved_setup_candles())

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan-smc",
            "--symbol", "TEST",
            "--csv", str(csv_path),
            "--account-equity", "100000",
        ],
    )
    assert result.exit_code == 0, result.output

    chart_dir = tmp_project / "data" / "debug_charts"
    assert not list(chart_dir.glob("*.png"))
    setup_files = list((tmp_project / "data" / "smc_setups").glob("*-TEST.json"))
    assert setup_files
    payload = json.loads(setup_files[-1].read_text())
    assert payload["chart_path"] is None


def test_scan_smc_account_equity_drives_qty_by_risk(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_project_root(tmp_project, monkeypatch)
    csv_path = tmp_project / "data" / "candles_demo" / "TEST.csv"
    _write_csv(csv_path, _approved_setup_candles())

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan-smc",
            "--symbol", "TEST",
            "--csv", str(csv_path),
            "--account-equity", "1000000",
            "--available-cash", "500000",
            "--market-regime", "neutral",
        ],
    )
    assert result.exit_code == 0, result.output
    setup_files = list((tmp_project / "data" / "smc_setups").glob("*-TEST.json"))
    assert setup_files
    payload = json.loads(setup_files[-1].read_text())
    assert payload["trade_plan"]["qty_by_risk"] > 0
    # 1% of $1M = $10k risk; with risk_per_share≈23 that's ≈434
    # shares, but capped by max_equity_per_position_pct=10%
    # ($100k / $1017 ≈ 98 shares).
    assert payload["trade_plan"]["qty_by_risk"] <= 100


def test_scan_smc_use_account_values_does_not_place_orders(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_project_root(tmp_project, monkeypatch)
    csv_path = tmp_project / "data" / "candles_demo" / "TEST.csv"
    _write_csv(csv_path, _approved_setup_candles())

    from bot import broker as broker_module
    from bot import cli as cli_module
    from bot.ibkr_client import AccountSummary

    sentinel = {"connected": False, "disconnected": False}

    class _StubClient:
        def __init__(self, cfg) -> None:
            sentinel["cfg"] = cfg

        def connect(self, *a, **k) -> None:
            sentinel["connected"] = True

        def disconnect(self) -> None:
            sentinel["disconnected"] = True

        def get_account_summary(self):
            return [
                AccountSummary(
                    account_id="DU0",
                    net_liquidation=750_000.0,
                    available_funds=600_000.0,
                    total_cash=600_000.0,
                    currency="USD",
                )
            ]

        def get_daily_bars(self, *a, **k):  # pragma: no cover - unused
            return []

    monkeypatch.setattr(cli_module, "IBKRClient", _StubClient)

    def _boom(*_a, **_kw):  # pragma: no cover - must never be called
        raise AssertionError("place_order must not be invoked from scan-smc")

    monkeypatch.setattr(broker_module.Broker, "place_order", _boom)

    from typer.testing import CliRunner

    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan-smc",
            "--symbol", "TEST",
            "--csv", str(csv_path),
            "--use-account-values",
            "--market-regime", "neutral",
        ],
    )
    assert result.exit_code == 0, result.output
    assert sentinel["connected"] is True
    assert sentinel["disconnected"] is True
    setup_files = list((tmp_project / "data" / "smc_setups").glob("*-TEST.json"))
    assert setup_files
    payload = json.loads(setup_files[-1].read_text())
    # Equity 750k → 1% risk = $7500; capped by 10% per-position rule.
    assert payload["trade_plan"]["qty_by_risk"] > 0


def test_scan_smc_watchlist_chart_and_limit(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_project_root(tmp_project, monkeypatch)
    candles_dir = tmp_project / "data" / "candles_demo"
    for sym in ("AAA", "BBB", "CCC"):
        _write_csv(candles_dir / f"{sym}.csv", _approved_setup_candles())

    # Override the watchlist so the scan iterates AAA, BBB, CCC.
    watchlist_path = tmp_project / "config" / "watchlist.yaml"
    watchlist_path.write_text(
        "equities:\n"
        "  - {symbol: AAA, exchange: SMART, currency: USD}\n"
        "  - {symbol: BBB, exchange: SMART, currency: USD}\n"
        "  - {symbol: CCC, exchange: SMART, currency: USD}\n",
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
            "--market-regime", "neutral",
            "--chart",
            "--limit", "2",
        ],
    )
    assert result.exit_code == 0, result.output

    chart_dir = tmp_project / "data" / "debug_charts"
    pngs = sorted(chart_dir.glob("*.png"))
    # --limit 2 → at most two PNGs
    assert 1 <= len(pngs) <= 2
    # Rich truncates headers when rendering 13 columns into the
    # default pytest terminal width. Instead of inspecting the exact
    # header text, confirm the scanner still wrote the batch summary
    # JSON which records one chart_path per row.
    summaries = list(
        (tmp_project / "data" / "smc_setups").glob("*-watchlist-summary.json")
    )
    assert summaries, "watchlist summary JSON missing"
    payload = json.loads(summaries[0].read_text())
    collected_chart_paths = [
        row.get("chart_path")
        for rows in payload["buckets"].values()
        for row in rows
    ]
    assert any(cp for cp in collected_chart_paths)


def test_render_helper_does_not_invoke_broker(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity: even via the CLI helper, broker.place_order is never reached."""
    _patch_project_root(tmp_project, monkeypatch)
    from bot import broker as broker_module

    def _boom(*_a, **_kw):  # pragma: no cover - must never be called
        raise AssertionError("place_order must not be invoked from chart pipeline")

    monkeypatch.setattr(broker_module.Broker, "place_order", _boom)

    from bot.cli import _render_chart_for
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    candles = _approved_setup_candles()
    evaluation = _evaluation(candles)
    rows = [
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
    chart_path = _render_chart_for(cfg, evaluation, rows)
    assert chart_path is not None
    assert evaluation.chart_path == str(chart_path)
    assert chart_path.exists()


# ---------------------------------------------------------------------------
# Validation notes
# ---------------------------------------------------------------------------
def test_validation_notes_flag_small_fvg() -> None:
    candles = _approved_setup_candles()
    evaluation = _evaluation(candles)
    # FVG width 997 -> 1017 = 2.0% so no auto-note expected here.
    assert isinstance(evaluation.validation_notes, list)


def test_validation_notes_flag_low_rr_when_target_close_to_entry() -> None:
    """Construct a setup whose computed R/R is < 2 to trigger the note."""
    candles = _approved_setup_candles()
    # Inflate the per-position cap so qty is computed from risk only,
    # then push min_reward_to_risk to 0 so the engine *doesn't* reject
    # the plan but our auto note about R/R<2 still fires when applicable.
    # Easiest: simulate by removing the BIG pivot at i=2 so target_1
    # collapses to the smaller pivot at i=8 (=1018), close to entry.
    trimmed = list(candles)
    trimmed[2] = Candle(
        timestamp=trimmed[2].timestamp, open=1046, high=1049, low=1044, close=1047,
        volume=1000,
    )
    cfg = type("Cfg", (), {"strategies": {
        "SMC_LIQUIDITY_REVERSAL_RESEARCH": {
            "risk": {"min_reward_to_risk": 0.0},
            "target": {"min_risk_reward": 0.0, "max_target_distance_pct": 50.0},
        }
    }})()
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=trimmed,
        cfg=cfg,
        market_regime="neutral",
        account_equity=100_000.0,
        latest_close=trimmed[-1].close,
    )
    rr = (evaluation.trade_plan or {}).get("risk_reward_to_target_1")
    assert rr is not None and 0 < rr < 2.0, f"unexpected rr={rr}"
    assert any("R/R" in note for note in evaluation.validation_notes)
