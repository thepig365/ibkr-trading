"""Unit tests for the backtest candle cache (Prompt 13E PART A)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.backtests.candle_cache import (
    CANDLE_CSV_HEADER,
    BarRow,
    CandleCacheError,
    cache_dir_for,
    load_candles,
    read_candles_csv,
    save_candles_csv,
    write_csv_for_day,
)


def _bars(day: str, n: int, *, start_minute: int = 30) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        m = (start_minute + i) % 60
        h = 9 + ((start_minute + i) // 60)
        ts = f"{day} {h:02d}:{m:02d}:00-04:00"
        out.append(
            {
                "timestamp": ts,
                "open": 100.0 + i * 0.01,
                "high": 100.5 + i * 0.01,
                "low": 99.5 + i * 0.01,
                "close": 100.2 + i * 0.01,
                "volume": 1_000.0 + i,
            }
        )
    return out


def test_save_candles_writes_normalised_csv_with_canonical_header(tmp_path: Path) -> None:
    bars = _bars("2026-04-22", 3)
    stats = save_candles_csv(tmp_path, "CRM", "1min", bars)
    assert stats.symbol == "CRM"
    assert stats.timeframe == "1min"
    assert stats.days_written == 1
    assert stats.rows_written >= 1
    # File path is the per-day CSV under data/candles/CRM/1min.
    expected_dir = cache_dir_for(tmp_path, "CRM", "1min")
    assert expected_dir.is_dir()
    csv_path = expected_dir / "2026-04-22.csv"
    assert csv_path.exists()
    text = csv_path.read_text(encoding="utf-8").splitlines()
    assert text[0] == ",".join(CANDLE_CSV_HEADER)
    assert len(text) == 1 + len(bars)


def test_save_candles_dedupes_rows_on_second_write(tmp_path: Path) -> None:
    bars = _bars("2026-04-22", 3)
    save_candles_csv(tmp_path, "CRM", "1min", bars)
    # Second write of the SAME bars should not add new rows.
    stats2 = save_candles_csv(tmp_path, "CRM", "1min", bars)
    csv_path = cache_dir_for(tmp_path, "CRM", "1min") / "2026-04-22.csv"
    assert sum(1 for _ in csv_path.read_text(encoding="utf-8").splitlines()) == 1 + 3
    assert stats2.rows_deduped == 3


def test_save_candles_force_overwrites_existing_file(tmp_path: Path) -> None:
    save_candles_csv(tmp_path, "CRM", "1min", _bars("2026-04-22", 5))
    new_bars = _bars("2026-04-22", 2)
    new_bars[0]["open"] = 999.0
    stats = save_candles_csv(tmp_path, "CRM", "1min", new_bars, force=True)
    csv_path = cache_dir_for(tmp_path, "CRM", "1min") / "2026-04-22.csv"
    rows = csv_path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 + 2
    assert "999.0" in rows[1]
    assert stats.rows_deduped == 0


def test_save_candles_groups_by_day_and_reports_gaps(tmp_path: Path) -> None:
    bars = _bars("2026-04-22", 2) + _bars("2026-04-24", 2)
    stats = save_candles_csv(
        tmp_path, "CRM", "1min", bars,
        start="2026-04-21", end="2026-04-25",
    )
    assert stats.days_written == 2
    # Days inside the range with no bars become gaps (informational).
    assert "2026-04-21" in stats.gaps
    assert "2026-04-23" in stats.gaps
    assert "2026-04-25" in stats.gaps
    assert "2026-04-22" not in stats.gaps


def test_save_candles_rejects_invalid_symbol(tmp_path: Path) -> None:
    with pytest.raises(CandleCacheError):
        save_candles_csv(tmp_path, "crm!", "1min", _bars("2026-04-22", 1))


def test_save_candles_rejects_invalid_timeframe(tmp_path: Path) -> None:
    with pytest.raises(CandleCacheError):
        save_candles_csv(tmp_path, "CRM", "weekly", _bars("2026-04-22", 1))


def test_load_candles_concatenates_days_in_order(tmp_path: Path) -> None:
    save_candles_csv(tmp_path, "CRM", "1min", _bars("2026-04-22", 2))
    save_candles_csv(tmp_path, "CRM", "1min", _bars("2026-04-23", 3))
    bars = load_candles(tmp_path, "CRM", "1min", start="2026-04-22", end="2026-04-23")
    assert len(bars) == 5
    assert all(isinstance(b, BarRow) for b in bars)
    assert bars[0].timestamp.startswith("2026-04-22")
    assert bars[-1].timestamp.startswith("2026-04-23")


def test_load_candles_returns_empty_when_cache_missing(tmp_path: Path) -> None:
    bars = load_candles(tmp_path, "CRM", "1min", start="2026-04-22", end="2026-04-23")
    assert bars == []


def test_read_candles_csv_returns_empty_for_missing_file(tmp_path: Path) -> None:
    out = read_candles_csv(tmp_path / "does_not_exist.csv")
    assert out == []


def test_write_csv_for_day_skips_bars_outside_day(tmp_path: Path) -> None:
    cache = cache_dir_for(tmp_path, "CRM", "1min")
    bars_in = [BarRow.from_mapping(b) for b in _bars("2026-04-22", 3)]
    bars_other = [BarRow.from_mapping(b) for b in _bars("2026-04-23", 1)]
    path, _, _, _ = write_csv_for_day(cache, "2026-04-22", bars_in + bars_other)
    text = path.read_text(encoding="utf-8")
    assert "2026-04-22" in text
    assert "2026-04-23" not in text


def test_candle_cache_no_broker_or_ibkr_imports() -> None:
    """``bot.backtests.candle_cache`` is data-only — no broker imports.

    Uses a fresh subprocess so we get a pristine ``sys.modules``
    snapshot (mutating ``sys.modules`` from the test process would
    leak into other in-process tests that monkey-patch
    :class:`bot.ibkr_client.IBKRClient`).
    """
    import json
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import bot.backtests.candle_cache  # noqa: F401\n"
        "loaded = sorted(m for m in sys.modules if m in {'bot.broker', 'bot.ibkr_client'} or m.startswith('ib_async') or m.startswith('ib_insync'))\n"
        "import json; print(json.dumps(loaded))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout.strip())
    assert loaded == [], f"candle_cache pulled in broker-related modules: {loaded}"


# ---------------------------------------------------------------------------
# PART H #22: backtest runtime outputs are gitignored
# ---------------------------------------------------------------------------
def test_runtime_output_dirs_are_gitignored() -> None:
    """Each runtime output path must be ignored by ``git check-ignore``.

    Runs ``git check-ignore -q <path>`` for the canonical sample files
    we expect to produce. Skips silently if the test isn't running
    inside a git checkout (e.g. a tarball install).
    """
    import shutil
    import subprocess

    repo_root = Path(__file__).resolve().parent.parent
    if not (repo_root / ".git").exists() or not shutil.which("git"):
        pytest.skip("git not available — cannot run check-ignore")

    sample_paths = [
        "data/candles/CRM/1min/2026-04-22.csv",
        "data/backtests/intraday/2026-04-22-103000-backtest-summary.json",
        "data/backtests/intraday/charts/2026-04-22-103000-equity.png",
        "data/debug_charts/sample.png",
        "data/intraday_smc/2026-04-22-watchlist-intraday-smc-summary.json",
    ]
    for rel in sample_paths:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", rel],
            cwd=str(repo_root),
            capture_output=True,
        )
        assert proc.returncode == 0, (
            f"{rel!r} is NOT gitignored — runtime artifacts must never "
            "be committed. Update .gitignore."
        )
