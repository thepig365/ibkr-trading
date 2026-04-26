"""Tests for the local command runner / allowlist."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bot_ui.services.command_queue import (
    CommandRequest,
    LocalCommandRunner,
    RemoteCommandQueue,
    get_command_queue,
    validate_request,
)
from bot_ui.services.safety import (
    ALLOWED_COMMANDS,
    FORBIDDEN_ARG_TOKENS,
    FORBIDDEN_COMMAND_TOKENS,
    is_allowed,
    is_forbidden,
)


# ---------------------------------------------------------------------------
# Allowlist semantics
# ---------------------------------------------------------------------------


def test_required_commands_present_in_allowlist() -> None:
    """The Prompt 13A spec mandates this exact set on the allowlist."""
    required = {
        "paper-reconcile",
        "refresh-paper-account-state",
        "build-watchlist",
        "scan-mtf-smc-watchlist",
        "mtf-near-alignment-alert",
        "research-report",
        "research-status",
        "macro-calendar",
    }
    missing = required - set(ALLOWED_COMMANDS)
    assert not missing, f"Allowlist missing required commands: {missing}"


def test_forbidden_command_tokens_block_known_dangerous_names() -> None:
    for tok in [
        "place_order",
        "place-order",
        "auto-paper-mtf",
        "run-auto-paper-mtf-loop",
        "telegram-listen",
        "live",
        "bracket",
    ]:
        assert is_forbidden(tok), f"{tok!r} should be forbidden"


def test_forbidden_arg_tokens_include_shell_metacharacters_and_live_flag() -> None:
    must_have = {";", "&&", "|", ">", "`", "$(", "--live", "--enable-live-trading"}
    assert must_have.issubset(FORBIDDEN_ARG_TOKENS)


def test_is_allowed_only_returns_true_for_allowlisted_safe_commands() -> None:
    assert is_allowed("paper-reconcile") is True
    assert is_allowed("scan-mtf-smc-watchlist") is True
    # exact dangerous names
    assert is_allowed("auto-paper-mtf") is False
    assert is_allowed("run-auto-paper-mtf-loop") is False
    assert is_allowed("telegram-listen") is False
    # arbitrary commands
    assert is_allowed("rm") is False
    assert is_allowed("portfolio") is True
    assert is_allowed("run-auto-paper-intraday-loop") is False
    assert is_allowed("") is False


# ---------------------------------------------------------------------------
# validate_request
# ---------------------------------------------------------------------------


# Per-command default args that satisfy each command's validator. Most
# commands accept an empty arg list; ``ibkr-news-fetch`` legitimately
# requires ``--symbols``.
_DEFAULT_ARGS_FOR: dict[str, tuple[str, ...]] = {
    "ibkr-news-fetch": ("--symbols", "AAPL,TSLA,NVDA", "--limit", "50"),
    "ibkr-session-status": (),
    "open-orders": (),
    "portfolio": (),
    "paper-daily-report": ("--latest",),
    "paper-weekly-report": ("--latest",),
    "strategy-info": ("mtf_smc",),
    "strategy-scan": ("--strategy", "mtf_smc"),
    "scan-intraday-smc": ("--symbol", "AAPL", "--ibkr"),
    "scan-intraday-smc-watchlist": ("--ibkr",),
    "fetch-candles": (
        "--symbol", "CRM",
        "--timeframe", "1min",
        "--start", "2026-04-01",
        "--end", "2026-04-24",
        "--ibkr",
    ),
    "backtest-intraday-smc": (
        "--symbol", "CRM",
        "--start", "2026-04-01",
        "--end", "2026-04-24",
        "--mode", "strict_and_aggressive",
    ),
    "backtest-intraday-smc-watchlist": (
        "--symbols", "CRM,AMZN",
        "--start", "2026-04-01",
        "--end", "2026-04-24",
        "--mode", "strict_and_aggressive",
    ),
    "backtest-report": ("--latest",),
    "build-edge-profile": (
        "--symbol", "CRM",
        "--start", "2026-04-01",
        "--end", "2026-04-24",
        "--strategy", "ict_smc_intraday_v1",
    ),
    "build-edge-profiles": (
        "--symbols", "AAPL,NVDA",
        "--start", "2026-04-01",
        "--end", "2026-04-24",
        "--strategy", "ict_smc_intraday_v1",
    ),
    "edge-profile-report": ("--latest",),
    "data-status": (),
    "data-cleanup": ("--dry-run",),
    "premarket-brief": ("--latest",),
}


@pytest.mark.parametrize(
    "command",
    sorted(ALLOWED_COMMANDS),
)
def test_validate_request_accepts_allowlisted(command: str) -> None:
    args = _DEFAULT_ARGS_FOR.get(command, ())
    accepted, reason = validate_request(CommandRequest(command=command, args=args))
    assert accepted is True, reason
    assert reason == ""


@pytest.mark.parametrize(
    "command",
    [
        "auto-paper-mtf",
        "run-auto-paper-mtf-loop",
        "run-auto-paper-intraday-loop",
        "telegram-listen",
        "place-order",
        "rm",
        "",
    ],
)
def test_validate_request_rejects_unknown_or_forbidden(command: str) -> None:
    accepted, reason = validate_request(CommandRequest(command=command))
    assert accepted is False
    assert reason


@pytest.mark.parametrize(
    "arg",
    ["foo;bar", "x && y", "x | y", "back`tick", "$(whoami)", "--live", "--enable-live-trading"],
)
def test_validate_request_rejects_dangerous_args(arg: str) -> None:
    accepted, reason = validate_request(
        CommandRequest(command="paper-reconcile", args=(arg,))
    )
    assert accepted is False
    assert reason


def test_validate_request_rejects_newlines() -> None:
    accepted, reason = validate_request(
        CommandRequest(command="paper-reconcile", args=("line1\nline2",))
    )
    assert accepted is False
    assert "newline" in reason.lower()


def test_validate_request_accepts_safe_args() -> None:
    accepted, reason = validate_request(
        CommandRequest(command="build-watchlist", args=("--limit", "30"))
    )
    assert accepted is True, reason


def test_data_cleanup_apply_rejected_from_ui_runner() -> None:
    accepted, reason = validate_request(
        CommandRequest(command="data-cleanup", args=("--apply",))
    )
    assert accepted is False
    assert "apply" in reason.lower()


# ---------------------------------------------------------------------------
# LocalCommandRunner end-to-end (use a fake CLI subcommand via python -c)
# ---------------------------------------------------------------------------


def test_local_runner_rejects_unallowed_without_executing(
    tmp_path: Path,
) -> None:
    """A rejected command must not start a subprocess at all."""
    runner = LocalCommandRunner(
        project_root=tmp_path,
        timeout_seconds=10,
        # python_executable that would crash if invoked, to prove we never run it
        python_executable="/nonexistent/python-binary",
        audit_file=tmp_path / "audit.jsonl",
    )
    result = runner.submit(CommandRequest(command="auto-paper-mtf"))
    assert result.accepted is False
    assert "forbidden" in result.rejected_reason.lower()
    assert result.exit_code is None
    # Audit file should still record the rejection.
    assert (tmp_path / "audit.jsonl").exists()
    line = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["accepted"] is False
    assert rec["command"] == "auto-paper-mtf"


def test_local_runner_executes_allowlisted_command_via_fake_cli(
    tmp_path: Path,
) -> None:
    """Run an allowlisted name but redirect bot.cli to a tiny stub.

    We do this by making a tmp project with a one-line ``bot/cli.py`` that
    just prints what it received and exits 0. This proves:
      * The runner uses the configured cwd and python executable.
      * Args are passed through without shell interpretation.
      * stdout/stderr are captured.
    """
    # Build a fake project layout
    proj = tmp_path / "fake-proj"
    bot_dir = proj / "bot"
    bot_dir.mkdir(parents=True)
    (bot_dir / "__init__.py").write_text("", encoding="utf-8")
    (bot_dir / "cli.py").write_text(
        "import sys\nimport json\n"
        "print(json.dumps({'argv': sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    runner = LocalCommandRunner(
        project_root=proj,
        timeout_seconds=10,
        python_executable=sys.executable,
        audit_file=proj / "audit.jsonl",
    )
    result = runner.submit(
        CommandRequest(command="paper-reconcile", args=("--limit", "5"))
    )
    assert result.accepted is True, result.rejected_reason
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["argv"] == ["paper-reconcile", "--limit", "5"]


def test_local_runner_forces_paper_account_mode_in_subprocess_env(tmp_path: Path) -> None:
    proj = tmp_path / "fake-proj"
    bot_dir = proj / "bot"
    bot_dir.mkdir(parents=True)
    (bot_dir / "__init__.py").write_text("", encoding="utf-8")
    (bot_dir / "cli.py").write_text(
        "import os\nprint(os.environ.get('IBKR_ACCOUNT_MODE', 'unset'))\n",
        encoding="utf-8",
    )
    runner = LocalCommandRunner(
        project_root=proj,
        python_executable=sys.executable,
        audit_file=proj / "audit.jsonl",
    )
    result = runner.submit(CommandRequest(command="paper-reconcile"))
    assert result.exit_code == 0
    assert result.stdout.strip() == "paper"


def test_local_runner_records_audit_jsonl(tmp_path: Path) -> None:
    proj = tmp_path / "fake-proj"
    bot_dir = proj / "bot"
    bot_dir.mkdir(parents=True)
    (bot_dir / "__init__.py").write_text("", encoding="utf-8")
    (bot_dir / "cli.py").write_text("print('hi')\n", encoding="utf-8")
    audit = proj / "audit.jsonl"
    runner = LocalCommandRunner(
        project_root=proj,
        python_executable=sys.executable,
        audit_file=audit,
    )
    runner.submit(CommandRequest(command="paper-reconcile"))
    runner.submit(CommandRequest(command="auto-paper-mtf"))  # rejected
    rows = [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines() if l]
    assert len(rows) == 2
    assert rows[0]["accepted"] is True
    assert rows[1]["accepted"] is False


def test_local_runner_list_recent_returns_newest_first(tmp_path: Path) -> None:
    proj = tmp_path / "fake-proj"
    bot_dir = proj / "bot"
    bot_dir.mkdir(parents=True)
    (bot_dir / "__init__.py").write_text("", encoding="utf-8")
    (bot_dir / "cli.py").write_text("print('hi')\n", encoding="utf-8")
    runner = LocalCommandRunner(
        project_root=proj,
        python_executable=sys.executable,
        audit_file=proj / "audit.jsonl",
    )
    runner.submit(CommandRequest(command="paper-reconcile"))
    runner.submit(CommandRequest(command="build-watchlist"))
    recent = runner.list_recent(limit=10)
    assert [r.request.command for r in recent] == ["build-watchlist", "paper-reconcile"]


# ---------------------------------------------------------------------------
# Remote placeholder
# ---------------------------------------------------------------------------


def test_remote_command_queue_is_placeholder() -> None:
    q = RemoteCommandQueue()
    with pytest.raises(NotImplementedError):
        q.submit(CommandRequest(command="paper-reconcile"))


def test_factory_local_returns_local_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRATEGY_LAB_BACKEND", "local")
    q = get_command_queue(tmp_path)
    assert isinstance(q, LocalCommandRunner)


def test_factory_remote_returns_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRATEGY_LAB_BACKEND", "remote")
    q = get_command_queue(tmp_path)
    assert isinstance(q, RemoteCommandQueue)
