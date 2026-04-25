"""CLI tests for ``fetch-candles``, ``backtest-intraday-smc[_watchlist]``
and ``backtest-report`` (Prompt 13E PART C).

The tests spawn ``python -m bot.cli`` as a subprocess, just like
``test_intraday_scan_cli.py``, to exercise the full Typer surface.
They never connect to TWS — backtests only read cached CSVs we
write by hand into the repo's ``data/candles/`` tree under a
unique fixture-only symbol so we don't collide with any real cache.

Tracked invariants:
* ``backtest-intraday-smc`` rejects bad ``--symbol`` / ``--mode`` /
  ``--direction`` with exit code 2.
* ``backtest-intraday-smc`` writes the four artifact files
  (``*-backtest-summary.json``, ``-backtest-trades.csv``,
  ``-backtest-equity.csv``, ``-backtest-report.md``).
* The summary JSON is well-formed and carries
  ``paper_only=True`` / ``execution_allowed=False``.
* ``backtest-report --latest`` prints the metrics table from the
  saved summary.
* ``fetch-candles`` requires ``--ibkr`` (refuses to run silently).
* The watchlist variant rejects empty / missing input gracefully.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDLES_DIR = REPO_ROOT / "data" / "candles"
BACKTESTS_DIR = REPO_ROOT / "data" / "backtests" / "intraday"
FIXTURE_SYMBOL = "ZZZZZ"  # 5 chars, fits ^[A-Z]{1,5}$, never collides with real tickers


def _run_cli(args: list[str], *, cwd: Path, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["IBKR_ACCOUNT_MODE"] = "paper"
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.setdefault("IBKR_HOST", "127.0.0.1")
    env.setdefault("IBKR_PORT", "65530")
    env.setdefault("IBKR_CLIENT_ID", "9999")
    return subprocess.run(
        [sys.executable, "-m", "bot.cli", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "memory").mkdir(exist_ok=True)
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
        "strategies.yaml",
        "macro_calendar.yaml",
    ):
        src = REPO_ROOT / "config" / name
        if src.exists():
            shutil.copy(src, tmp_path / "config" / name)
    return tmp_path


@pytest.fixture
def cleanup_outputs() -> list[Path]:
    """Clean up cache + report files we wrote during the test."""
    BACKTESTS_DIR.mkdir(parents=True, exist_ok=True)
    CANDLES_DIR.mkdir(parents=True, exist_ok=True)
    before_reports = {p for p in BACKTESTS_DIR.glob("*") if p.is_file()}
    fixture_cache_dir = CANDLES_DIR / FIXTURE_SYMBOL
    yield []
    after_reports = {p for p in BACKTESTS_DIR.glob("*") if p.is_file()}
    for p in (after_reports - before_reports):
        try:
            p.unlink()
        except OSError:
            pass
    if fixture_cache_dir.exists():
        try:
            shutil.rmtree(fixture_cache_dir)
        except OSError:
            pass
    charts = BACKTESTS_DIR / "charts"
    if charts.exists():
        for p in charts.glob(f"*{FIXTURE_SYMBOL}*"):
            try:
                p.unlink()
            except OSError:
                pass


def _write_synthetic_cache(symbol: str, day: str) -> Path:
    """Drop a tiny but parseable 1-minute CSV at ``data/candles/{SYM}/1min/{day}.csv``.

    The series isn't engineered to fire any signal — we only need the
    engine to traverse ``_simulate_symbol`` and write artifacts.
    """
    path = CANDLES_DIR / symbol / "1min" / f"{day}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    price = 100.0
    for i in range(0, 200):
        h = 9 + ((30 + i) // 60)
        m = (30 + i) % 60
        if h >= 16:
            break
        rows.append({
            "timestamp": f"{day} {h:02d}:{m:02d}:00-04:00",
            "open": f"{price:.4f}",
            "high": f"{price + 0.3:.4f}",
            "low": f"{price - 0.3:.4f}",
            "close": f"{price + 0.05:.4f}",
            "volume": "1000",
        })
        price += 0.02
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"]
        )
        w.writeheader()
        w.writerows(rows)
    return path


# ---------------------------------------------------------------------------
# fetch-candles
# ---------------------------------------------------------------------------
def test_cli_fetch_candles_requires_ibkr(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    p = _run_cli(
        [
            "fetch-candles",
            "--symbol", "CRM",
            "--timeframe", "1min",
            "--start", "2026-04-22",
            "--end", "2026-04-23",
        ],
        cwd=proj,
    )
    assert p.returncode == 2, p.stdout + p.stderr
    assert "--ibkr is required" in (p.stdout + p.stderr)


def test_cli_fetch_candles_rejects_bad_symbol(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    p = _run_cli(
        [
            "fetch-candles",
            "--symbol", "bad-symbol",
            "--timeframe", "1min",
            "--start", "2026-04-22",
            "--end", "2026-04-23",
            "--ibkr",
        ],
        cwd=proj,
    )
    assert p.returncode == 2
    out = (p.stdout + p.stderr).lower()
    assert "symbol" in out


def test_cli_fetch_candles_rejects_bad_date(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    p = _run_cli(
        [
            "fetch-candles",
            "--symbol", "CRM",
            "--timeframe", "1min",
            "--start", "20260422",
            "--end", "2026-04-23",
            "--ibkr",
        ],
        cwd=proj,
    )
    assert p.returncode == 2
    out = (p.stdout + p.stderr).lower()
    assert "yyyy-mm-dd" in out


# ---------------------------------------------------------------------------
# backtest-intraday-smc
# ---------------------------------------------------------------------------
def test_cli_backtest_rejects_bad_symbol(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    p = _run_cli(
        [
            "backtest-intraday-smc",
            "--symbol", "bad-symbol",
            "--start", "2026-04-22",
            "--end", "2026-04-22",
        ],
        cwd=proj,
    )
    assert p.returncode == 2
    assert "symbol" in (p.stdout + p.stderr).lower()


def test_cli_backtest_rejects_bad_mode(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    p = _run_cli(
        [
            "backtest-intraday-smc",
            "--symbol", "CRM",
            "--start", "2026-04-22",
            "--end", "2026-04-22",
            "--mode", "yolo",
        ],
        cwd=proj,
    )
    assert p.returncode == 2
    assert "mode" in (p.stdout + p.stderr).lower()


def test_cli_backtest_rejects_bad_direction(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    p = _run_cli(
        [
            "backtest-intraday-smc",
            "--symbol", "CRM",
            "--start", "2026-04-22",
            "--end", "2026-04-22",
            "--direction", "buy",
        ],
        cwd=proj,
    )
    assert p.returncode == 2
    assert "direction" in (p.stdout + p.stderr).lower()


def test_cli_backtest_writes_summary_trades_equity_report(
    tmp_path: Path, cleanup_outputs: list[Path]
) -> None:
    proj = _make_project(tmp_path)
    day = "2026-04-22"
    cache = _write_synthetic_cache(FIXTURE_SYMBOL, day)
    assert cache.exists()

    p = _run_cli(
        [
            "backtest-intraday-smc",
            "--symbol", FIXTURE_SYMBOL,
            "--start", day,
            "--end", day,
            "--mode", "strict_and_aggressive",
            "--direction", "both",
        ],
        cwd=proj,
    )
    assert p.returncode == 0, p.stdout + p.stderr

    summaries = sorted(BACKTESTS_DIR.glob("*-backtest-summary.json"))
    assert summaries, f"no summary files under {BACKTESTS_DIR}\n{p.stdout}"
    latest = summaries[-1]
    payload = json.loads(latest.read_text("utf-8"))
    assert payload["paper_only"] is True
    assert payload["execution_allowed"] is False
    assert payload["strategy_id"] == "ict_smc_intraday_v1"
    assert FIXTURE_SYMBOL in payload["config"]["symbols"]

    stem = latest.name.replace("-backtest-summary.json", "")
    trades = BACKTESTS_DIR / f"{stem}-backtest-trades.csv"
    equity = BACKTESTS_DIR / f"{stem}-backtest-equity.csv"
    report = BACKTESTS_DIR / f"{stem}-backtest-report.md"
    for path in (trades, equity, report):
        assert path.exists(), f"expected {path.name} to exist"


def test_cli_backtest_report_latest_handles_no_reports(tmp_path: Path) -> None:
    """``backtest-report --latest`` must not crash if no reports exist."""
    proj = _make_project(tmp_path)
    p = _run_cli(["backtest-report", "--latest"], cwd=proj)
    assert p.returncode == 0, p.stdout + p.stderr
    out = p.stdout + p.stderr
    # Either prints "no reports" or an existing report — both are valid.
    assert (
        "No backtest reports yet" in out
        or "Backtest report" in out
    )


def test_cli_backtest_report_prints_metrics_for_existing_summary(
    tmp_path: Path, cleanup_outputs: list[Path]
) -> None:
    proj = _make_project(tmp_path)
    day = "2026-04-22"
    _write_synthetic_cache(FIXTURE_SYMBOL, day)
    p = _run_cli(
        [
            "backtest-intraday-smc",
            "--symbol", FIXTURE_SYMBOL,
            "--start", day,
            "--end", day,
        ],
        cwd=proj,
    )
    assert p.returncode == 0
    p2 = _run_cli(["backtest-report", "--latest"], cwd=proj)
    assert p2.returncode == 0, p2.stdout + p2.stderr
    out = p2.stdout
    assert "total_filled_trades" in out
    assert "total_signals" in out


# ---------------------------------------------------------------------------
# backtest-intraday-smc-watchlist
# ---------------------------------------------------------------------------
def test_cli_backtest_watchlist_requires_input(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    p = _run_cli(
        [
            "backtest-intraday-smc-watchlist",
            "--start", "2026-04-22",
            "--end", "2026-04-22",
        ],
        cwd=proj,
    )
    assert p.returncode == 2
    out = (p.stdout + p.stderr).lower()
    assert "symbols" in out or "source" in out


def test_cli_backtest_watchlist_runs_with_explicit_symbols(
    tmp_path: Path, cleanup_outputs: list[Path]
) -> None:
    proj = _make_project(tmp_path)
    day = "2026-04-22"
    _write_synthetic_cache(FIXTURE_SYMBOL, day)
    p = _run_cli(
        [
            "backtest-intraday-smc-watchlist",
            "--symbols", FIXTURE_SYMBOL,
            "--start", day,
            "--end", day,
        ],
        cwd=proj,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    summaries = sorted(BACKTESTS_DIR.glob("*-backtest-summary.json"))
    assert summaries
    payload = json.loads(summaries[-1].read_text("utf-8"))
    assert FIXTURE_SYMBOL in payload["config"]["symbols"]


# ---------------------------------------------------------------------------
# Hard invariant: backtest path never imports broker / IBKR
# ---------------------------------------------------------------------------
def test_backtest_module_does_not_import_broker_or_ibkr() -> None:
    """Subprocess check: backtest engine + cache modules don't pull broker.

    Mirrors the in-process subprocess pattern used in
    ``tests/test_intraday_backtest.py`` and
    ``tests/test_ui_backtest_page.py``. We never pop modules from the
    parent ``sys.modules``, which would silently break other tests
    that monkey-patch :class:`bot.ibkr_client.IBKRClient`.
    """
    import json
    code = (
        "import sys\n"
        "import bot.backtests.intraday_engine  # noqa: F401\n"
        "import bot.backtests.candle_cache  # noqa: F401\n"
        "import bot.backtests.metrics  # noqa: F401\n"
        "import bot.backtests.reports  # noqa: F401\n"
        "loaded = sorted(m for m in sys.modules if m in {'bot.broker', 'bot.ibkr_client'} or m.startswith('ib_async') or m.startswith('ib_insync'))\n"
        "import json; print(json.dumps(loaded))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout.strip())
    assert loaded == [], f"bot.backtests pulled in broker-related modules: {loaded}"
