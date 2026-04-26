"""UI control surface for ICT/SMC intraday paper trading (Prompt 13F).

These tests guard the boundary between the (pure-render) UI tier and
the (broker-touching) Worker tier:

* The /paper page renders the new intraday paper section without
  importing any broker / IBKR client code.
* The intraday auto runtime flag toggle writes to the canonical path
  shared with the worker (``data/runtime/intraday_auto_paper_enabled``).
* The new CLI subcommands (``auto-paper-intraday-smc``,
  ``run-auto-paper-intraday-loop``, ``intraday-paper-status``) are on
  the allowlist with strict validators that reject any live / market
  / shell-meta argument.
* The MTF auto-paper flag is unaffected by toggling the intraday flag
  (and vice-versa).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

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
    FORBIDDEN_ARG_TOKENS,
    is_allowed,
    is_forbidden,
    validate_args_for,
)
from bot_ui.services.state_store import (
    INTRADAY_AUTO_PAPER_ENABLED_RELPATH,
    KILL_SWITCH_RELPATH,
    MTF_AUTO_PAPER_ENABLED_RELPATH,
    LocalFileStateStore,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    return tmp_path


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


# ---------------------------------------------------------------------------
# /paper page renders the new intraday section
# ---------------------------------------------------------------------------


def test_paper_page_renders_intraday_section(project: Path) -> None:
    r = _client(project).get("/paper")
    assert r.status_code == 200, r.text
    text = r.text
    assert "ICT/SMC Intraday Paper Trading" in text
    assert "Intraday auto" in text
    # Prompt 13K.3: per-trade and daily notional caps + paper sizing card.
    assert "10,000" in text or "10000" in text
    assert "100,000" in text or "100000" in text
    assert "Paper sizing" in text
    # canonical path label appears so operators can see exactly which file
    # the worker process polls.
    assert INTRADAY_AUTO_PAPER_ENABLED_RELPATH in text
    # buttons enqueue allowlisted commands, never live trading.
    assert "auto-paper-intraday-smc" in text
    assert "intraday-paper-status" in text
    # explicit reassurance for operators.
    assert "PAPER ONLY" in text


def test_paper_page_does_not_expose_live_trading_buttons(project: Path) -> None:
    """No <button> / <a> CTA on /paper may invite live or market-order
    actions. Reassuring informational text ("live trading is hard-blocked")
    is allowed — only actionable CTAs are forbidden."""
    r = _client(project).get("/paper")
    cta_re = re.compile(
        r"<(?:button|a)\b[^>]*>([^<]*)</(?:button|a)>",
        re.IGNORECASE,
    )
    forbidden_phrases = [
        "place order",
        "place_order",
        "market order",
        "go live",
        "enable live",
        "submit live",
        "live trade",
        "live trading",
    ]
    for label_match in cta_re.finditer(r.text):
        label = (label_match.group(1) or "").strip().lower()
        for bad in forbidden_phrases:
            assert bad not in label, (
                f"/paper exposes a CTA labelled {label!r} which is forbidden."
            )


# ---------------------------------------------------------------------------
# Runtime flag toggle endpoints
# ---------------------------------------------------------------------------


def test_intraday_auto_toggle_creates_canonical_file(project: Path) -> None:
    target = project / INTRADAY_AUTO_PAPER_ENABLED_RELPATH
    legacy = project / "data" / "INTRADAY_AUTO_PAPER_ENABLED"
    assert not target.exists()
    client = _client(project)
    r = client.post(
        "/paper/runtime/intraday-auto",
        data={"state": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert target.read_text(encoding="utf-8").strip() == "1"
    assert not legacy.exists()
    r = client.post(
        "/paper/runtime/intraday-auto",
        data={"state": "off"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert target.read_text(encoding="utf-8").strip() == "0"


def test_intraday_auto_toggle_does_not_touch_mtf_or_kill(project: Path) -> None:
    client = _client(project)
    client.post(
        "/paper/runtime/intraday-auto",
        data={"state": "on"},
        follow_redirects=False,
    )
    assert not (project / KILL_SWITCH_RELPATH).exists()
    assert not (project / MTF_AUTO_PAPER_ENABLED_RELPATH).exists()


def test_kill_switch_remains_canonical_after_intraday_toggle(project: Path) -> None:
    """The intraday and kill-switch flags must use the same paths the worker
    reads, with no path drift between the two endpoints."""
    client = _client(project)
    client.post("/paper/runtime/kill-switch", data={"enable": "on"})
    client.post("/paper/runtime/intraday-auto", data={"state": "on"})
    assert (project / KILL_SWITCH_RELPATH).is_file()
    assert (project / INTRADAY_AUTO_PAPER_ENABLED_RELPATH).is_file()


# ---------------------------------------------------------------------------
# Allowlist + per-command validators
# ---------------------------------------------------------------------------


def test_intraday_paper_commands_are_on_allowlist() -> None:
    for cmd in (
        "auto-paper-intraday-smc",
        "run-auto-paper-intraday-loop",
        "intraday-paper-status",
        "paper-activation-status",
        "write-paper-local-config",
        "intraday-paper-on",
        "intraday-paper-off",
        "paper-readiness-check",
        "first-paper-pass",
    ):
        assert cmd in ALLOWED_COMMANDS, f"{cmd!r} must be allowlisted"
        assert is_allowed(cmd) is True, f"{cmd!r} must pass is_allowed"


def test_old_mtf_paper_commands_remain_forbidden() -> None:
    for cmd in ("auto-paper-mtf", "run-auto-paper-mtf-loop"):
        assert is_forbidden(cmd) is True
        assert is_allowed(cmd) is False


def test_intraday_paper_commands_validators_accept_safe_args() -> None:
    accepted, reason = validate_args_for(
        "auto-paper-intraday-smc",
        ("--source", "dynamic", "--limit", "20", "--telegram"),
    )
    assert accepted, reason
    accepted, reason = validate_args_for(
        "run-auto-paper-intraday-loop",
        (
            "--source", "dynamic",
            "--limit", "20",
            "--interval-seconds", "60",
            "--heartbeat-minutes", "30",
            "--market-hours-only",
            "--telegram",
        ),
    )
    assert accepted, reason
    accepted, reason = validate_args_for("intraday-paper-status", ())
    assert accepted, reason
    accepted, reason = validate_args_for("intraday-paper-status", ("--json",))
    assert accepted, reason
    accepted, reason = validate_args_for("strategy-lab-engine-status", ())
    assert accepted, reason
    accepted, reason = validate_args_for("strategy-lab-engine-status", ("--json",))
    assert accepted, reason
    accepted, reason = validate_args_for("engine-status", ())
    assert accepted, reason
    accepted, reason = validate_args_for("engine-status", ("--json", "--probe-ui"))
    assert accepted, reason


@pytest.mark.parametrize(
    "args",
    [
        ("--live",),
        ("--enable-live-trading",),
        ("--market",),
        ("--market-order",),
        ("--mkt",),
        ("--place-order",),
        ("--place_order",),
        ("--buy",),
        ("--sell",),
        ("--source", "dynamic", "--live"),
        ("--source", "dynamic;rm -rf /"),
        ("--source", "dynamic", "--limit", "20", "--telegram", "--enable-live"),
    ],
)
def test_auto_paper_intraday_smc_rejects_dangerous_args(args: tuple[str, ...]) -> None:
    accepted, reason = validate_request(
        CommandRequest(command="auto-paper-intraday-smc", args=args),
    )
    assert accepted is False, f"args {args!r} should be rejected: {reason}"


@pytest.mark.parametrize(
    "args",
    [
        ("--source", "garbage"),
        ("--limit", "0"),
        ("--limit", "101"),
        ("--limit", "abc"),
        ("--telegram", "--no-telegram"),
        ("--unknown-flag",),
        ("positional",),
    ],
)
def test_auto_paper_intraday_smc_rejects_bad_values(args: tuple[str, ...]) -> None:
    accepted, reason = validate_request(
        CommandRequest(command="auto-paper-intraday-smc", args=args),
    )
    assert accepted is False, f"args {args!r} should be rejected: {reason}"


@pytest.mark.parametrize(
    "args",
    [
        ("--interval-seconds", "1"),     # below min
        ("--interval-seconds", "100000"),  # above max
        ("--heartbeat-minutes", "0"),
        ("--heartbeat-minutes", "9999"),
        ("--market-hours-only", "--ignore-market-hours"),
        ("--source", "live"),  # not in {static,dynamic,manual}
        ("--limit", "abc"),
    ],
)
def test_run_auto_paper_intraday_loop_rejects_bad_args(args: tuple[str, ...]) -> None:
    accepted, reason = validate_request(
        CommandRequest(command="run-auto-paper-intraday-loop", args=args),
    )
    assert accepted is False, f"{args!r} should be rejected: {reason}"


def test_intraday_paper_status_rejects_unknown_flags() -> None:
    accepted, _ = validate_request(
        CommandRequest(command="intraday-paper-status", args=("--live",)),
    )
    assert accepted is False
    accepted, _ = validate_request(
        CommandRequest(command="intraday-paper-status", args=("--whatever",)),
    )
    assert accepted is False


def test_forbidden_arg_tokens_include_market_and_live_extensions() -> None:
    # Belt-and-suspenders for the brand-new tokens.
    must_have = {
        "--live",
        "--enable-live-trading",
        "--market",
        "--mkt",
        "--place-order",
        "--buy",
        "--sell",
    }
    assert must_have.issubset(FORBIDDEN_ARG_TOKENS)


# ---------------------------------------------------------------------------
# UI source code never imports broker / IBKR — also covers the new files
# ---------------------------------------------------------------------------


def test_no_ui_module_imports_broker_or_ibkr_client() -> None:
    """Re-runs the broader architecture check on the bot_ui tree to make
    sure the new ``intraday_paper`` UI code didn't accidentally pull in
    broker imports."""
    bad = re.compile(
        r"^\s*(?:from|import)\s+(?:bot\.broker|bot\.ibkr_client|ib_async|ib_insync)\b",
        re.MULTILINE,
    )
    offenders: list[str] = []
    for p in (PROJECT_ROOT / "bot_ui").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if bad.search(text):
            offenders.append(str(p.relative_to(PROJECT_ROOT)))
    assert offenders == [], offenders


def test_create_app_does_not_load_broker_or_ibkr_client() -> None:
    """The /paper render path must not import the broker even with a new
    intraday section added."""
    code = (
        "import sys, json\n"
        "from bot_ui.app import create_app\n"
        "app = create_app()\n"
        "loaded = sorted(m for m in sys.modules if m == 'bot.broker' or "
        "                m == 'bot.ibkr_client' or m.startswith('ib_async') or "
        "                m.startswith('ib_insync'))\n"
        "print(json.dumps(loaded))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    loaded = sorted(eval(proc.stdout.strip()))  # noqa: S307 - trusted JSON list
    assert loaded == [], (
        f"create_app() pulled in IBKR-related modules: {loaded}."
    )


# ---------------------------------------------------------------------------
# Paper-orders directory and runtime files are gitignored
# ---------------------------------------------------------------------------


def test_intraday_paper_runtime_outputs_are_gitignored() -> None:
    p = PROJECT_ROOT / ".gitignore"
    text = p.read_text(encoding="utf-8")
    for needed in [
        "data/runtime/",
        "data/auto_paper_loop/",
        "data/paper_orders/",
        "data/backtests/*",
        "data/intraday_smc/*",
        "data/candles/*",
        "data/debug_charts/*",
    ]:
        assert needed in text, f".gitignore missing {needed!r}"
