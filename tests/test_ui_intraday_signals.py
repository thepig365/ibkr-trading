"""UI tests for the ICT/SMC Intraday tab on /signals (Prompt 13D, PART J).

Verifies:

* :class:`bot_ui.services.state_store.LocalFileStateStore` returns an
  empty :class:`IntradaySignalsView` when no scan has run.
* The store correctly parses a synthetic
  ``data/intraday_smc/<date>-watchlist-intraday-smc-summary.json``
  produced by ``scan-intraday-smc-watchlist`` (PART E).
* ``GET /signals`` returns 200 with the default (MTF) tab and with
  ``?strategy=ict_smc_intraday_v1``; the ICT tab body shows symbol +
  signal category + score for fixture rows, and the new
  "Run Intraday Scan" button is rendered.
* Rendering ``/signals`` (either tab) does NOT pull in
  :mod:`bot.broker` or :mod:`bot.ibkr_client` — the UI must stay
  decoupled from the broker stack on render.
* The command queue allowlist accepts the safe intraday scan commands
  and rejects the documented unsafe variants (live flags, shell meta,
  unknown subcommands, missing ``--ibkr``, bad ``--limit``, bad
  ``--symbol``, bad ``--mode`` / ``--direction-hint``).
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
    IntradaySignalsView,
    LocalFileStateStore,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
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


def _write_summary(project_root: Path, date: str = "2026-04-25") -> Path:
    out_dir = project_root / "data" / "intraday_smc"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "date": date,
        "strategy_id": "ict_smc_intraday_v1",
        "source": "dynamic",
        "symbols_scanned": 3,
        "paper_only": True,
        "execution_allowed": False,
        "counts": {
            "DAY_TRADE_READY_STRICT": 1,
            "DAY_TRADE_READY_AGGRESSIVE": 1,
            "WATCH_ONLY": 1,
            "INVALID_RISK": 0,
            "BLOCKED": 0,
            "NO_SETUP": 0,
            "ERROR": 0,
        },
        "ready_strict_symbols": ["AAPL"],
        "ready_aggressive_symbols": ["TSLA"],
        "watch_symbols": ["NVDA"],
        "invalid_symbols": [],
        "top_candidates": [
            {
                "symbol": "AAPL",
                "signal_category": "DAY_TRADE_READY_STRICT",
                "direction": "long",
                "score": 80.0,
                "five_min_setup_found": True,
                "one_min_trigger_found": True,
                "entry": 195.50,
                "stop": 194.20,
                "target": 197.45,
                "risk_reward": 1.5,
                "stop_distance_pct": 0.66,
                "next_condition_to_watch": "monitor 1m FVG retest",
                "explanation_zh": "纸面研究: STRICT 候选",
                "chart_paths": [
                    "data/debug_charts/2026-04-25-AAPL-intraday-1m-smc.png"
                ],
                "data_source": "ibkr",
                "data_quality": {"bars_1m_count": 600},
                "execution_allowed": False,
                "paper_only": True,
            }
        ],
        "items": [
            {
                "symbol": "AAPL",
                "signal_category": "DAY_TRADE_READY_STRICT",
                "direction": "long",
                "score": 80.0,
                "five_min_setup_found": True,
                "one_min_trigger_found": True,
                "entry": 195.50,
                "stop": 194.20,
                "target": 197.45,
                "risk_reward": 1.5,
                "stop_distance_pct": 0.66,
                "next_condition_to_watch": "monitor 1m FVG retest",
                "explanation_zh": "纸面研究: STRICT 候选",
                "chart_paths": [
                    "data/debug_charts/2026-04-25-AAPL-intraday-1m-smc.png"
                ],
                "data_source": "ibkr",
                "data_quality": {"bars_1m_count": 600},
                "execution_allowed": False,
                "paper_only": True,
            },
            {
                "symbol": "TSLA",
                "signal_category": "DAY_TRADE_READY_AGGRESSIVE",
                "direction": "short",
                "score": 60.0,
                "five_min_setup_found": True,
                "one_min_trigger_found": True,
                "entry": 250.10,
                "stop": 251.40,
                "target": 248.60,
                "risk_reward": 1.2,
                "stop_distance_pct": 0.52,
                "next_condition_to_watch": "AGGRESSIVE 候选",
                "explanation_zh": "纸面研究: AGGRESSIVE 候选",
                "chart_paths": [],
                "data_source": "ibkr",
                "data_quality": {"bars_1m_count": 580},
                "execution_allowed": False,
                "paper_only": True,
            },
            {
                "symbol": "NVDA",
                "signal_category": "WATCH_ONLY",
                "direction": "long",
                "score": 40.0,
                "five_min_setup_found": True,
                "one_min_trigger_found": False,
                "entry": None,
                "stop": None,
                "target": None,
                "risk_reward": None,
                "stop_distance_pct": None,
                "next_condition_to_watch": "等待 1m 入场触发完成",
                "explanation_zh": "5m 已就位; 等待 1m 微扫",
                "chart_paths": [],
                "data_source": "ibkr",
                "data_quality": {"bars_1m_count": 600},
                "execution_allowed": False,
                "paper_only": True,
            },
        ],
    }
    out = out_dir / f"{date}-watchlist-intraday-smc-summary.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


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
# StateStore: empty project → IntradaySignalsView default
# ---------------------------------------------------------------------------
def test_intraday_signals_view_is_empty_when_no_scan(empty_project: Path) -> None:
    store = LocalFileStateStore(empty_project)
    view = store.intraday_signals()
    assert isinstance(view, IntradaySignalsView)
    assert view.is_empty
    assert view.symbols_scanned == 0
    assert view.counts == {}
    assert view.items == []
    assert view.paper_only is True
    assert view.execution_allowed is False
    assert view.file_path is None


def test_intraday_signals_view_parses_summary(project_with_summary: Path) -> None:
    store = LocalFileStateStore(project_with_summary)
    view = store.intraday_signals()
    assert view.symbols_scanned == 3
    assert view.strategy_id == "ict_smc_intraday_v1"
    assert view.source == "dynamic"
    assert view.paper_only is True
    assert view.execution_allowed is False
    assert view.counts.get("DAY_TRADE_READY_STRICT") == 1
    assert view.ready_strict_symbols == ["AAPL"]
    assert view.ready_aggressive_symbols == ["TSLA"]
    assert view.watch_symbols == ["NVDA"]
    assert len(view.items) == 3
    aapl = next(r for r in view.items if r.symbol == "AAPL")
    assert aapl.signal_category == "DAY_TRADE_READY_STRICT"
    assert aapl.direction == "long"
    assert aapl.score == 80.0
    assert aapl.entry == 195.50
    assert aapl.stop == 194.20
    assert aapl.target == 197.45
    assert aapl.risk_reward == 1.5
    assert aapl.chart_paths and "AAPL" in aapl.chart_paths[0]


# ---------------------------------------------------------------------------
# Route: /signals renders both tabs
# ---------------------------------------------------------------------------
def test_signals_default_tab_is_saved_or_ict(empty_project: Path) -> None:
    client = _client(empty_project)
    r = client.get("/signals")
    assert r.status_code == 200
    body = r.text
    # All catalog strategies are links; default selection is ict_smc_intraday_v1.
    assert "MTF SMC" in body
    assert "ICT/SMC Intraday" in body
    # Default (no file) is ICT: intraday actions are visible.
    assert "scan-intraday-smc-watchlist" in body
    assert "ict_smc_intraday_v1" in body


def test_signals_intraday_tab_renders_summary(project_with_summary: Path) -> None:
    client = _client(project_with_summary)
    r = client.get("/signals?strategy=ict_smc_intraday_v1")
    assert r.status_code == 200
    body = r.text
    # Intraday-specific button + command name on the page.
    assert "Run Intraday Scan" in body
    assert "scan-intraday-smc-watchlist" in body
    # Strategy id badge.
    assert "ict_smc_intraday_v1" in body
    # Symbols from the fixture are rendered.
    assert "AAPL" in body
    assert "TSLA" in body
    assert "NVDA" in body
    # Categories show pills.
    assert "STRICT" in body
    assert "AGGRESSIVE" in body
    # Paper-only invariant pills.
    assert "paper_only=True" in body or "paper-only" in body.lower()
    assert "execution_allowed=False" in body or "execution off" in body


def test_signals_intraday_tab_handles_unknown_strategy_value(
    empty_project: Path,
) -> None:
    """Unknown ?strategy values fall back to saved default (ict), never 500."""
    client = _client(empty_project)
    r = client.get("/signals?strategy=mystery_unicorn")
    assert r.status_code == 200
    assert "ict_smc_intraday_v1" in r.text
    assert "Note:" in r.text


# ---------------------------------------------------------------------------
# Architectural safety: rendering /signals does not load broker / ibkr_client
# ---------------------------------------------------------------------------
def test_render_signals_does_not_load_broker_or_ibkr(empty_project: Path) -> None:
    """A clean Python process renders both tabs without touching IBKR.

    Mirrors :mod:`tests.test_ui_architecture_safety` style — uses a
    subprocess so we get a pristine ``sys.modules`` snapshot.
    """
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
        "for url in ('/signals', '/signals?strategy=ict_smc_intraday_v1', '/signals?strategy=mtf_smc'):\n"
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
        f"Rendering /signals pulled in IBKR-related modules: {loaded}. "
        "The UI must stay decoupled from broker code."
    )


# ---------------------------------------------------------------------------
# Allowlist: scan-intraday-smc[-watchlist] are accepted, unsafe variants are not
# ---------------------------------------------------------------------------
def test_intraday_commands_are_allowlisted() -> None:
    assert is_allowed("scan-intraday-smc")
    assert is_allowed("scan-intraday-smc-watchlist")
    # Both are documented in the descriptions map.
    assert "scan-intraday-smc" in ALLOWED_COMMANDS
    assert "scan-intraday-smc-watchlist" in ALLOWED_COMMANDS


def test_validate_safe_scan_intraday_smc_args_passes() -> None:
    accepted, reason = validate_request(
        CommandRequest(
            command="scan-intraday-smc",
            args=("--symbol", "AAPL", "--ibkr", "--chart", "--telegram"),
        )
    )
    assert accepted is True, reason


def test_validate_safe_scan_intraday_smc_watchlist_args_passes() -> None:
    accepted, reason = validate_request(
        CommandRequest(
            command="scan-intraday-smc-watchlist",
            args=(
                "--source", "dynamic",
                "--limit", "20",
                "--ibkr", "--chart", "--telegram",
            ),
        )
    )
    assert accepted is True, reason


@pytest.mark.parametrize(
    "args",
    [
        # Missing --ibkr (safety: must be explicit).
        ("--symbol", "AAPL"),
        # Missing --symbol.
        ("--ibkr",),
        # Bad ticker pattern.
        ("--symbol", "AAPL.US", "--ibkr"),
        ("--symbol", "aapl", "--ibkr"),
        ("--symbol", "TOOLONGSYMBOL", "--ibkr"),
        # Bad direction-hint.
        ("--symbol", "AAPL", "--ibkr", "--direction-hint", "buy"),
        # Bad mode.
        ("--symbol", "AAPL", "--ibkr", "--mode", "yolo"),
        # Unknown flag.
        ("--symbol", "AAPL", "--ibkr", "--profit", "100"),
    ],
)
def test_validate_unsafe_scan_intraday_smc_args_rejected(args: tuple[str, ...]) -> None:
    accepted, reason = validate_request(
        CommandRequest(command="scan-intraday-smc", args=args)
    )
    assert accepted is False
    assert reason


@pytest.mark.parametrize(
    "args",
    [
        # Missing --ibkr.
        ("--source", "dynamic", "--limit", "20"),
        # Bad source.
        ("--ibkr", "--source", "yahoo"),
        # Bad limit (out of range).
        ("--ibkr", "--limit", "0"),
        ("--ibkr", "--limit", "1000"),
        # Bad mode.
        ("--ibkr", "--mode", "yolo"),
        # Unknown flag.
        ("--ibkr", "--profit", "100"),
        # Mutually exclusive save flags.
        ("--ibkr", "--save-json", "--no-save-json"),
    ],
)
def test_validate_unsafe_watchlist_args_rejected(args: tuple[str, ...]) -> None:
    accepted, reason = validate_request(
        CommandRequest(command="scan-intraday-smc-watchlist", args=args)
    )
    assert accepted is False
    assert reason


@pytest.mark.parametrize(
    "command,args",
    [
        # Live trading flags must always be rejected for any intraday command.
        ("scan-intraday-smc", ("--symbol", "AAPL", "--ibkr", "--live")),
        ("scan-intraday-smc-watchlist", ("--ibkr", "--enable-live-trading")),
        # Shell metacharacters always rejected.
        ("scan-intraday-smc", ("--symbol", "AAPL;rm -rf /", "--ibkr")),
        ("scan-intraday-smc-watchlist", ("--ibkr", "--source", "dynamic`whoami`")),
    ],
)
def test_validate_dangerous_intraday_args_always_rejected(
    command: str, args: tuple[str, ...]
) -> None:
    accepted, reason = validate_request(CommandRequest(command=command, args=args))
    assert accepted is False
    assert reason


def test_validate_args_for_dispatches_to_intraday_validators() -> None:
    """Sanity: dispatcher routes the new command names correctly."""
    ok, _ = validate_args_for(
        "scan-intraday-smc",
        ("--symbol", "AAPL", "--ibkr"),
    )
    assert ok is True
    ok, _ = validate_args_for(
        "scan-intraday-smc-watchlist",
        ("--ibkr",),
    )
    assert ok is True
