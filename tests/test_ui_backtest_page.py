"""UI tests for the /backtest page (Prompt 13E PART D, E, G).

Verifies:

* :class:`bot_ui.services.state_store.LocalFileStateStore` returns an
  empty :class:`BacktestSummaryView` when no backtest has been run.
* The store correctly parses a synthetic
  ``data/backtests/intraday/<stem>-backtest-summary.json`` (with
  optional companion CSVs / Markdown report).
* ``GET /backtest`` returns 200 in both the empty and populated cases.
* The page advertises both action commands
  (``backtest-intraday-smc-watchlist`` and ``fetch-candles``) so the
  forms wire to the LocalCommandRunner allowlist.
* Rendering ``/backtest`` does NOT pull in :mod:`bot.broker` or
  :mod:`bot.ibkr_client` (architectural safety — UI is broker-free).
* The command-runner allowlist accepts the new safe backtest commands
  and rejects unsafe variants (live flags, shell meta, missing
  ``--ibkr`` for ``fetch-candles``, ``--ibkr`` for backtest commands,
  bad symbols / dates / modes / directions / limit).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import (
    CommandRequest,
    LocalCommandRunner,
    validate_request,
)
from bot_ui.services.safety import (
    ALLOWED_COMMANDS,
    is_allowed,
    validate_args_for,
)
from bot_ui.services.state_store import (
    BacktestSummaryView,
    LocalFileStateStore,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _client(project_root: Path) -> TestClient:
    state = LocalFileStateStore(project_root)
    queue = LocalCommandRunner(
        project_root=project_root,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=project_root / "ui_audit.jsonl",
    )
    app = create_app(project_root=project_root, state_store=state, command_queue=queue)
    return TestClient(app)


def _write_summary(project_root: Path, *, stem: str = "2026-04-25-103000") -> Path:
    """Drop a synthetic backtest summary + companion files."""
    out_dir = project_root / "data" / "backtests" / "intraday"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "strategy_id": "ict_smc_intraday_v1",
        "paper_only": True,
        "execution_allowed": False,
        "started_at_utc": "2026-04-25T10:30:00Z",
        "finished_at_utc": "2026-04-25T10:30:11Z",
        "config": {
            "symbols": ["CRM", "AMZN"],
            "start": "2026-04-22",
            "end": "2026-04-24",
            "mode": "strict_and_aggressive",
            "direction": "both",
            "rth_only": True,
            "pending_lifetime_bars": 30,
            "allow_multiple_trades_per_day": True,
        },
        "metrics": {
            "total_signals": 5,
            "total_filled_trades": 4,
            "total_not_filled": 1,
            "win_rate": 0.5,
            "average_r": 0.5,
            "median_r": 0.5,
            "total_r": 2.0,
            "max_drawdown_r": -1.0,
            "profit_factor": 2.0,
            "average_bars_held": 6.5,
            "strict_count": 2,
            "aggressive_count": 2,
            "strict_win_rate": 0.5,
            "aggressive_win_rate": 0.5,
            "long_win_rate": 0.5,
            "short_win_rate": 0.5,
            "by_symbol": [
                {
                    "symbol": "CRM",
                    "trades": 2,
                    "wins": 1,
                    "losses": 1,
                    "win_rate": 0.5,
                    "average_r": 0.5,
                    "total_r": 1.0,
                },
                {
                    "symbol": "AMZN",
                    "trades": 2,
                    "wins": 1,
                    "losses": 1,
                    "win_rate": 0.5,
                    "average_r": 0.5,
                    "total_r": 1.0,
                },
            ],
            "by_hour": {
                "10:00": {
                    "trades": 2,
                    "wins": 1,
                    "win_rate": 0.5,
                    "average_r": 0.5,
                    "total_r": 1.0,
                },
                "13:00": {
                    "trades": 2,
                    "wins": 1,
                    "win_rate": 0.5,
                    "average_r": 0.5,
                    "total_r": 1.0,
                },
            },
            "by_weekday": {
                "Wed": {
                    "trades": 4,
                    "wins": 2,
                    "win_rate": 0.5,
                    "average_r": 0.5,
                    "total_r": 2.0,
                },
            },
        },
        "trades": [
            {
                "trade_id": "abc12345",
                "symbol": "CRM",
                "date": "2026-04-22",
                "strategy_id": "ict_smc_intraday_v1",
                "direction": "long",
                "signal_category": "DAY_TRADE_READY_STRICT",
                "setup_type": "fvg_retest",
                "trigger_type": "1m_close",
                "entry_time": "2026-04-22 10:00:00-04:00",
                "entry_price": 100.0,
                "stop_price": 99.0,
                "target_price": 102.0,
                "exit_time": "2026-04-22 10:15:00-04:00",
                "exit_price": 102.0,
                "outcome": "win",
                "pnl_r": 2.0,
                "planned_rr": 2.0,
                "bars_held": 15,
            }
        ],
        "notes": ["sample run"],
    }
    summary = out_dir / f"{stem}-backtest-summary.json"
    summary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{stem}-backtest-trades.csv").write_text(
        "trade_id,symbol,date,outcome,pnl_r\nabc12345,CRM,2026-04-22,win,2.0\n",
        encoding="utf-8",
    )
    (out_dir / f"{stem}-backtest-equity.csv").write_text(
        "trade_index,trade_id,symbol,date,exit_time,pnl_r,cumulative_r\n1,abc12345,CRM,2026-04-22,2026-04-22 10:15:00-04:00,2.0,2.0\n",
        encoding="utf-8",
    )
    (out_dir / f"{stem}-backtest-report.md").write_text(
        "# Backtest report\nWin rate: 50%\nTotal R: 2.00\n",
        encoding="utf-8",
    )
    return summary


@pytest.fixture
def empty_project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir(exist_ok=True)
    return tmp_path


@pytest.fixture
def project_with_summary(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir(exist_ok=True)
    _write_summary(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# StateStore: empty project → BacktestSummaryView default
# ---------------------------------------------------------------------------
def test_backtest_summary_view_is_empty_when_no_runs(empty_project: Path) -> None:
    store = LocalFileStateStore(empty_project)
    view = store.get_backtest_summary()
    assert isinstance(view, BacktestSummaryView)
    assert view.is_empty
    assert view.summary_path is None
    assert view.total_filled_trades == 0
    assert view.paper_only is True
    assert view.execution_allowed is False
    assert view.is_stale is True


def test_backtest_summary_view_parses_summary(project_with_summary: Path) -> None:
    store = LocalFileStateStore(project_with_summary)
    view = store.get_backtest_summary()
    assert not view.is_empty
    assert view.is_stale is False
    assert view.symbols == ["CRM", "AMZN"]
    assert view.start == "2026-04-22"
    assert view.end == "2026-04-24"
    assert view.mode == "strict_and_aggressive"
    assert view.direction == "both"
    assert view.rth_only is True
    assert view.total_signals == 5
    assert view.total_filled_trades == 4
    assert view.total_not_filled == 1
    assert view.win_rate == 0.5
    assert view.total_r == 2.0
    assert view.max_drawdown_r == -1.0
    assert view.profit_factor == 2.0
    assert view.strict_count == 2
    assert view.aggressive_count == 2
    assert view.paper_only is True
    assert view.execution_allowed is False

    by_sym = {row.symbol: row for row in view.by_symbol}
    assert set(by_sym) == {"CRM", "AMZN"}
    assert by_sym["CRM"].trades == 2
    assert by_sym["CRM"].win_rate == 0.5

    hours = {row.hour for row in view.by_hour}
    assert "10:00" in hours and "13:00" in hours

    assert len(view.trades) == 1
    t = view.trades[0]
    assert t.symbol == "CRM"
    assert t.outcome == "win"
    assert t.pnl_r == 2.0

    # Companion file paths populated when files exist.
    assert view.summary_path and "summary.json" in view.summary_path
    assert view.trades_csv_path and "trades.csv" in view.trades_csv_path
    assert view.equity_csv_path and "equity.csv" in view.equity_csv_path
    assert view.report_md_path and "report.md" in view.report_md_path
    assert "Backtest report" in view.report_md_excerpt


def test_backtest_summary_view_handles_missing_companions(tmp_path: Path) -> None:
    """Trades CSV / report MD missing → still returns valid view."""
    (tmp_path / "data").mkdir(exist_ok=True)
    _write_summary(tmp_path, stem="2026-04-26-100000")
    # Remove companions to simulate partial run.
    backtests = tmp_path / "data" / "backtests" / "intraday"
    for name in (
        "2026-04-26-100000-backtest-trades.csv",
        "2026-04-26-100000-backtest-equity.csv",
        "2026-04-26-100000-backtest-report.md",
    ):
        (backtests / name).unlink()
    view = LocalFileStateStore(tmp_path).get_backtest_summary()
    assert not view.is_empty
    assert view.trades_csv_path is None
    assert view.equity_csv_path is None
    assert view.report_md_path is None
    assert view.report_md_excerpt == ""


# ---------------------------------------------------------------------------
# Route: /backtest
# ---------------------------------------------------------------------------
def test_get_backtest_returns_200_when_empty(empty_project: Path) -> None:
    client = _client(empty_project)
    r = client.get("/backtest")
    assert r.status_code == 200
    body = r.text
    assert "Backtest" in body
    # Both action commands wired up in the page.
    assert "backtest-intraday-smc-watchlist" in body
    assert "fetch-candles" in body
    # No summary present yet.
    assert "No backtest run yet" in body or "—" in body


def test_get_backtest_returns_200_with_summary(project_with_summary: Path) -> None:
    client = _client(project_with_summary)
    r = client.get("/backtest")
    assert r.status_code == 200
    body = r.text
    assert "CRM" in body
    assert "AMZN" in body
    # By-symbol table renders.
    assert "By symbol" in body
    # By-hour table renders.
    assert "10:00" in body
    # Strict/Aggressive pills.
    assert "strict 2" in body
    assert "aggressive 2" in body


# ---------------------------------------------------------------------------
# Architectural safety: rendering /backtest does not load broker / IBKR
# ---------------------------------------------------------------------------
def test_render_backtest_does_not_load_broker_or_ibkr(empty_project: Path) -> None:
    """Pristine subprocess: render /backtest with empty + populated data."""
    proj_repr = repr(str(empty_project))
    code = (
        "import json, sys\n"
        "from pathlib import Path\n"
        f"proj = Path({proj_repr})\n"
        "from fastapi.testclient import TestClient\n"
        "from bot_ui.app import create_app\n"
        "from bot_ui.services.command_queue import LocalCommandRunner\n"
        "from bot_ui.services.state_store import LocalFileStateStore\n"
        "import sys as _s\n"
        "state = LocalFileStateStore(proj)\n"
        "queue = LocalCommandRunner(project_root=proj, python_executable=_s.executable, timeout_seconds=5, audit_file=proj / 'audit.jsonl')\n"
        "app = create_app(project_root=proj, state_store=state, command_queue=queue)\n"
        "client = TestClient(app)\n"
        "for url in ('/backtest',):\n"
        "    r = client.get(url)\n"
        "    assert r.status_code == 200, url\n"
        "loaded = sorted(m for m in sys.modules if m == 'bot.broker' or m == 'bot.ibkr_client' or m.startswith('ib_async') or m.startswith('ib_insync'))\n"
        "print(json.dumps(loaded))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout.strip())
    assert loaded == [], (
        f"Rendering /backtest pulled in IBKR-related modules: {loaded}. "
        "The backtest UI must stay decoupled from broker code."
    )


# ---------------------------------------------------------------------------
# Allowlist (PART G)
# ---------------------------------------------------------------------------
def test_backtest_commands_are_allowlisted() -> None:
    for cmd in (
        "fetch-candles",
        "backtest-intraday-smc",
        "backtest-intraday-smc-watchlist",
        "backtest-report",
    ):
        assert is_allowed(cmd), f"{cmd!r} should be on the allowlist"
        assert cmd in ALLOWED_COMMANDS


def test_validate_safe_fetch_candles_args_passes() -> None:
    accepted, reason = validate_request(
        CommandRequest(
            command="fetch-candles",
            args=(
                "--symbol", "CRM",
                "--timeframe", "1min",
                "--start", "2026-04-01",
                "--end", "2026-04-24",
                "--ibkr",
            ),
        )
    )
    assert accepted is True, reason


def test_validate_safe_backtest_intraday_smc_args_passes() -> None:
    accepted, reason = validate_request(
        CommandRequest(
            command="backtest-intraday-smc",
            args=(
                "--symbol", "CRM",
                "--start", "2026-04-01",
                "--end", "2026-04-24",
                "--mode", "strict_and_aggressive",
                "--direction", "both",
                "--chart",
            ),
        )
    )
    assert accepted is True, reason


def test_validate_safe_backtest_intraday_smc_watchlist_args_passes() -> None:
    accepted, reason = validate_request(
        CommandRequest(
            command="backtest-intraday-smc-watchlist",
            args=(
                "--symbols", "CRM,AMZN,AAPL",
                "--start", "2026-04-01",
                "--end", "2026-04-24",
                "--mode", "strict_and_aggressive",
                "--direction", "both",
                "--chart",
            ),
        )
    )
    assert accepted is True, reason


def test_validate_safe_backtest_report_args_passes() -> None:
    accepted, reason = validate_request(
        CommandRequest(command="backtest-report", args=("--latest",))
    )
    assert accepted is True, reason


@pytest.mark.parametrize(
    "args",
    [
        # Missing --ibkr (safety).
        ("--symbol", "CRM", "--timeframe", "1min", "--start", "2026-04-01", "--end", "2026-04-24"),
        # Unknown timeframe.
        ("--symbol", "CRM", "--timeframe", "13min", "--start", "2026-04-01", "--end", "2026-04-24", "--ibkr"),
        # Bad symbol.
        ("--symbol", "crm", "--timeframe", "1min", "--start", "2026-04-01", "--end", "2026-04-24", "--ibkr"),
        # Bad date.
        ("--symbol", "CRM", "--timeframe", "1min", "--start", "20260401", "--end", "2026-04-24", "--ibkr"),
        # Disallowed flag (no extra options).
        ("--symbol", "CRM", "--timeframe", "1min", "--start", "2026-04-01", "--end", "2026-04-24", "--ibkr", "--paper-bracket"),
        # Shell meta.
        ("--symbol", "CRM;ls", "--timeframe", "1min", "--start", "2026-04-01", "--end", "2026-04-24", "--ibkr"),
    ],
)
def test_validate_unsafe_fetch_candles_args_rejected(args: tuple[str, ...]) -> None:
    accepted, reason = validate_request(
        CommandRequest(command="fetch-candles", args=args)
    )
    assert accepted is False, f"Expected reject; got accepted with reason={reason!r}"


@pytest.mark.parametrize(
    "args",
    [
        # --ibkr is forbidden on backtest commands.
        ("--symbol", "CRM", "--start", "2026-04-01", "--end", "2026-04-24", "--ibkr"),
        # Bad mode.
        ("--symbol", "CRM", "--start", "2026-04-01", "--end", "2026-04-24", "--mode", "yolo"),
        # Bad direction.
        ("--symbol", "CRM", "--start", "2026-04-01", "--end", "2026-04-24", "--direction", "buy"),
        # Bad date.
        ("--symbol", "CRM", "--start", "2026/04/01", "--end", "2026-04-24"),
        # Live-trading flag (would pull in broker).
        ("--symbol", "CRM", "--start", "2026-04-01", "--end", "2026-04-24", "--paper-bracket"),
        # Shell meta in symbol.
        ("--symbol", "CRM|ls", "--start", "2026-04-01", "--end", "2026-04-24"),
    ],
)
def test_validate_unsafe_backtest_intraday_smc_args_rejected(args: tuple[str, ...]) -> None:
    accepted, reason = validate_request(
        CommandRequest(command="backtest-intraday-smc", args=args)
    )
    assert accepted is False, f"Expected reject; got accepted with reason={reason!r}"


@pytest.mark.parametrize(
    "args",
    [
        # No symbols and no source.
        ("--start", "2026-04-01", "--end", "2026-04-24"),
        # Bad symbols list (contains lowercase).
        ("--symbols", "crm,amzn", "--start", "2026-04-01", "--end", "2026-04-24"),
        # Bad source.
        ("--source", "yolo", "--start", "2026-04-01", "--end", "2026-04-24"),
        # Out-of-range limit.
        ("--symbols", "CRM", "--start", "2026-04-01", "--end", "2026-04-24", "--limit", "0"),
        ("--symbols", "CRM", "--start", "2026-04-01", "--end", "2026-04-24", "--limit", "999"),
        # Live flag.
        ("--symbols", "CRM", "--start", "2026-04-01", "--end", "2026-04-24", "--ibkr"),
    ],
)
def test_validate_unsafe_backtest_intraday_smc_watchlist_args_rejected(
    args: tuple[str, ...],
) -> None:
    accepted, reason = validate_request(
        CommandRequest(command="backtest-intraday-smc-watchlist", args=args)
    )
    assert accepted is False, f"Expected reject; got accepted with reason={reason!r}"


@pytest.mark.parametrize(
    "args",
    [
        ("--latest", "--unknown"),
        ("--path", "../../etc/passwd; ls"),
        ("--path", "report.json | cat"),
    ],
)
def test_validate_unsafe_backtest_report_args_rejected(args: tuple[str, ...]) -> None:
    accepted, reason = validate_request(
        CommandRequest(command="backtest-report", args=args)
    )
    assert accepted is False, f"Expected reject; got accepted with reason={reason!r}"
