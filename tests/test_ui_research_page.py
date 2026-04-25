"""Tests for the /research UI page (Prompt 13B PART H/I/J).

Hard requirements verified here:

* Rendering /research never imports ``bot.ibkr_client`` and never opens
  any IBKR socket. The page reads JSON files from the state store only.
* The page returns 200 on an empty project.
* When research artefacts exist, the page surfaces report metadata.
* The state store's ``get_research_summary()`` handles missing files
  and detects stale reports.
* The command runner allowlist accepts research commands and rejects
  unsafe ``ibkr-news-fetch`` payloads.
"""

from __future__ import annotations

import json
import sys
from typing import Any
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import (
    CommandRequest,
    LocalCommandRunner,
    validate_request,
)
from bot_ui.services.state_store import LocalFileStateStore, ResearchSummary


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "memory").mkdir()
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


def _write_report(project_root: Path, *, date: str, paper_only: bool = True) -> dict:
    research_dir = project_root / "data" / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": date,
        "generated_at_utc": f"{date}T00:00:00Z",
        "paper_only": paper_only,
        "block_live_trading": True,
        "market_regime": {
            "market_regime": "bullish",
            "regime_confidence": "high",
            "new_positions_allowed": True,
        },
        "macro_events": [
            {
                "timestamp": f"{date}T12:30:00Z",
                "source": "manual_macro_calendar",
                "provider": "manual",
                "scope": "market",
                "category": "macro",
                "impact_level": "medium",
                "direction": "mixed",
                "confidence": "high",
                "title_en": "Initial Jobless Claims (jobless_claims)",
                "summary_zh": "JOBLESS_CLAIMS 事件",
                "action": "soft_flag",
                "reason": "manual",
                "symbol": None,
                "extra": {"time_et": "08:30", "macro_category": "jobless_claims"},
            }
        ],
        "ibkr_news": [
            {
                "timestamp": "",
                "source": "ibkr_news",
                "provider": "BRFG",
                "scope": "single_stock",
                "category": "AI_infrastructure",
                "impact_level": "medium",
                "direction": "bullish",
                "confidence": "medium",
                "title_en": "AMD wins major AI infrastructure deal",
                "summary_zh": "AI 基建 · 影响 中 · 偏多: AMD wins major AI...",
                "action": "boost_priority",
                "reason": "n/a",
                "symbol": "AMD",
                "extra": {},
            }
        ],
        "earnings": [],
        "analyst_ratings": [],
        "themes": [
            {
                "theme": "AI infrastructure",
                "symbols": ["AMD", "NVDA"],
                "strength": "high",
                "reason": "2 in watchlist, 1 matching headlines",
            }
        ],
        "symbol_profiles": [],
        "watchlist_today": ["AMD", "NVDA", "AAPL"],
        "smc_summary": {},
        "ibkr_news_provider_status": {
            "ibkr_news_available": True,
            "providers_detected": ["BRFG"],
            "missing_entitlements": [],
            "notes": [],
            "checked_at_utc": f"{date}T00:00:00Z",
        },
        "instruction": {
            "date": date,
            "market_regime": "bullish",
            "macro_events": [],
            "ibkr_news_provider_status": {"ibkr_news_available": True},
            "priority_watchlist": ["AMD", "NVDA"],
            "blocked_symbols": [],
            "manual_review_symbols": [],
            "soft_flag_symbols": [],
            "theme_tags_by_symbol": {"AMD": ["AI infrastructure"]},
            "event_risk_symbols": ["AMD"],
            "auto_paper_allowed": True,
            "paper_only": True,
            "bot_notes": ["sample"],
        },
        "notes": ["sample"],
    }
    with (research_dir / f"{date}-research-report.json").open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with (research_dir / f"{date}-research-instructions.json").open("w", encoding="utf-8") as f:
        json.dump(payload["instruction"], f, ensure_ascii=False, indent=2)
    return payload


# ---------------------------------------------------------------------------
# /research route
# ---------------------------------------------------------------------------
def test_research_page_returns_200_with_empty_project(project: Path) -> None:
    r = _client(project).get("/research")
    assert r.status_code == 200, r.text
    assert "Research" in r.text
    # Empty-state copy must appear since no report exists yet.
    assert "No research report yet" in r.text


def test_research_page_does_not_import_ibkr_on_render(project: Path) -> None:
    """Rendering /research must not pull in bot.ibkr_client or ib_async."""
    removed: dict[str, Any] = {}
    for key in ("bot.ibkr_client", "ib_async", "bot.broker"):
        if key in sys.modules:
            removed[key] = sys.modules.pop(key)
    try:
        client = _client(project)
        r = client.get("/research")
        assert r.status_code == 200

        assert "bot.ibkr_client" not in sys.modules, (
            "rendering /research must not import bot.ibkr_client"
        )
        assert "ib_async" not in sys.modules, (
            "rendering /research must not import ib_async"
        )
        assert "bot.broker" not in sys.modules, (
            "rendering /research must not import bot.broker"
        )
    finally:
        # Other tests re-use ``bot.cli``'s class bindings; leave sys.modules
        # consistent for monkeypatch on ``IBKRClient`` (reconciliation, etc.).
        sys.modules.update(removed)


def test_research_page_surfaces_report_metadata(project: Path) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _write_report(project, date=today)
    r = _client(project).get("/research")
    assert r.status_code == 200
    body = r.text
    assert today in body
    assert "AI infrastructure" in body
    assert "AMD" in body
    assert "auto-paper allowed" in body
    assert "paper-only" in body


def test_research_page_buttons_use_command_runner(project: Path) -> None:
    r = _client(project).get("/research")
    assert r.status_code == 200
    # Each safe research button posts to the allowlisted runner endpoint
    # via _command_form.html. Verify they are present.
    body = r.text
    for snippet in (
        'value="research-report"',
        'value="research-status"',
        'value="ibkr-news-status"',
        'value="macro-calendar"',
        '/api/commands/run',
    ):
        assert snippet in body, snippet


# ---------------------------------------------------------------------------
# State store: get_research_summary()
# ---------------------------------------------------------------------------
def test_get_research_summary_missing_files(project: Path) -> None:
    store = LocalFileStateStore(project)
    summary = store.get_research_summary()
    assert isinstance(summary, ResearchSummary)
    assert summary.is_empty
    assert summary.is_stale is True
    assert summary.report_path is None
    assert summary.instruction_path is None
    assert summary.macro_events == []
    assert summary.ibkr_news == []
    assert summary.market_regime == {}
    assert summary.paper_only is True


def test_get_research_summary_resolves_latest_report(project: Path) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _write_report(project, date="2026-01-01")
    _write_report(project, date=today)
    store = LocalFileStateStore(project)
    summary = store.get_research_summary()
    assert summary.report_path
    assert summary.report_path.endswith(f"{today}-research-report.json")
    assert summary.is_stale is False
    assert summary.date == today


def test_get_research_summary_detects_stale_report(project: Path) -> None:
    _write_report(project, date="2026-01-01")
    store = LocalFileStateStore(project)
    summary = store.get_research_summary()
    assert summary.report_path
    assert summary.is_stale is True


def test_get_research_summary_reads_markdown_excerpt(project: Path) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _write_report(project, date=today)
    md_path = project / "memory" / "RESEARCH-REPORT.md"
    md_path.write_text("# 研究报告 hello\n", encoding="utf-8")
    store = LocalFileStateStore(project)
    summary = store.get_research_summary()
    assert summary.markdown_path
    assert "研究报告 hello" in summary.markdown_excerpt


# ---------------------------------------------------------------------------
# Command queue allowlist + tight ibkr-news-fetch validation
# ---------------------------------------------------------------------------
def test_command_runner_allows_research_commands() -> None:
    for cmd in ("research-report", "research-status", "macro-calendar", "ibkr-news-status"):
        ok, reason = validate_request(CommandRequest(command=cmd))
        assert ok is True, reason


def test_command_runner_allows_ibkr_news_fetch_with_valid_args() -> None:
    ok, reason = validate_request(
        CommandRequest(
            command="ibkr-news-fetch",
            args=("--symbols", "AAPL,TSLA,NVDA", "--limit", "50"),
        )
    )
    assert ok is True, reason


@pytest.mark.parametrize(
    "args,why",
    [
        ((), "missing --symbols"),
        (("--symbols",), "missing value"),
        (("--symbols", "aapl,tsla"), "lowercase rejected"),
        (("--symbols", "AAPL TSLA"), "space rejected"),
        (("--symbols", "AAPL,TSLA;rm"), "shell metacharacter"),
        (("--symbols", "AAPL", "--limit", "0"), "limit too low"),
        (("--symbols", "AAPL", "--limit", "999"), "limit too high"),
        (("--symbols", "AAPL", "--limit", "abc"), "non-int limit"),
        (("--symbols", "AAPL", "--unknown"), "unknown flag"),
    ],
)
def test_command_runner_rejects_unsafe_ibkr_news_fetch(args, why) -> None:
    ok, reason = validate_request(CommandRequest(command="ibkr-news-fetch", args=args))
    assert ok is False, why
    assert reason


def test_command_runner_rejects_live_trading_commands() -> None:
    for cmd in ("auto-paper-mtf", "run-auto-paper-mtf-loop", "place-order", "live"):
        ok, reason = validate_request(CommandRequest(command=cmd))
        assert ok is False, f"{cmd} unexpectedly allowed: {reason}"


def test_research_report_arg_validator_rejects_unknown_flags() -> None:
    ok, _ = validate_request(
        CommandRequest(command="research-report", args=("--enable-live-trading",))
    )
    assert ok is False
    ok, _ = validate_request(
        CommandRequest(command="research-report", args=("--telegram",))
    )
    assert ok is True


def test_macro_calendar_arg_validator_strict_date_format() -> None:
    ok, _ = validate_request(
        CommandRequest(command="macro-calendar", args=("--date", "2026-04-25"))
    )
    assert ok is True
    for bad in (("--date", "2026/04/25"), ("--date", "abc"), ("--unknown",)):
        ok, _ = validate_request(CommandRequest(command="macro-calendar", args=bad))
        assert ok is False
