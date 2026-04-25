"""Tests for the manual macro calendar (Prompt 13B PART C)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from bot.cli import app as cli_app
from bot.research_providers.manual_macro_calendar import (
    DEFAULT_MACRO_CATEGORIES,
    MacroCalendar,
    load_macro_calendar,
    render_calendar_zh,
)


def _write_calendar(path: Path, events: list[dict]) -> None:
    path.write_text(
        yaml.safe_dump({"events": events}, sort_keys=False),
        encoding="utf-8",
    )


def test_load_missing_file_returns_empty_with_note(tmp_project: Path) -> None:
    cal = load_macro_calendar(path=tmp_project / "config" / "macro_calendar.yaml")
    assert isinstance(cal, MacroCalendar)
    assert cal.events == []
    assert cal.notes  # explanatory note included


def test_load_well_formed_yaml(tmp_project: Path) -> None:
    p = tmp_project / "config" / "macro_calendar.yaml"
    _write_calendar(
        p,
        [
            {
                "date": "2026-04-25",
                "time_et": "08:30",
                "event": "Initial Jobless Claims",
                "category": "jobless_claims",
                "impact_level": "medium",
                "handling": "soft_flag",
                "notes": "test",
            },
            {
                "date": "2026-05-13",
                "time_et": "08:30",
                "event": "CPI",
                "category": "CPI",
                "impact_level": "high",
                "handling": "soft_flag",
            },
        ],
    )
    cal = load_macro_calendar(path=p)
    assert len(cal.events) == 2
    assert cal.events[0].date == "2026-04-25"
    assert cal.events[0].handling == "soft_flag"
    assert cal.for_date("2026-05-13")[0].category == "CPI"


def test_macro_event_defaults_to_soft_flag(tmp_project: Path) -> None:
    """A macro event should NOT hard-block paper testing by default."""
    p = tmp_project / "config" / "macro_calendar.yaml"
    _write_calendar(
        p,
        [
            {
                "date": "2026-04-25",
                "time_et": "08:30",
                "event": "Initial Jobless Claims",
                "category": "jobless_claims",
                # No `handling` -> defaults to soft_flag
            }
        ],
    )
    cal = load_macro_calendar(path=p)
    ev = cal.events[0]
    assert ev.handling == "soft_flag"
    re = ev.to_research_event()
    assert re.action == "soft_flag"
    assert re.action != "hard_block"


def test_invalid_yaml_does_not_crash(tmp_project: Path) -> None:
    p = tmp_project / "config" / "macro_calendar.yaml"
    p.write_text("this: is: not: valid", encoding="utf-8")
    cal = load_macro_calendar(path=p)
    assert cal.events == []
    assert cal.notes  # explanatory note


def test_default_categories_present() -> None:
    for c in ("CPI", "PPI", "PCE", "NFP", "FOMC", "GDP", "ISM"):
        assert c in DEFAULT_MACRO_CATEGORIES


def test_render_calendar_zh_handles_empty() -> None:
    text = render_calendar_zh([], target_label="今日")
    assert "暂无安排" in text


def test_macro_calendar_cli_works(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`python -m bot.cli macro-calendar --date YYYY-MM-DD` returns 0."""
    p = tmp_project / "config" / "macro_calendar.yaml"
    _write_calendar(
        p,
        [
            {
                "date": "2026-05-13",
                "time_et": "08:30",
                "event": "CPI",
                "category": "CPI",
                "impact_level": "high",
                "handling": "soft_flag",
            }
        ],
    )
    monkeypatch.chdir(tmp_project)
    monkeypatch.setattr("bot.config.PROJECT_ROOT", tmp_project, raising=False)
    runner = CliRunner()
    result = runner.invoke(cli_app, ["macro-calendar", "--date", "2026-05-13"])
    assert result.exit_code == 0, result.output
    assert "CPI" in result.output


def test_macro_calendar_cli_today_no_crash(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_project)
    monkeypatch.setattr("bot.config.PROJECT_ROOT", tmp_project, raising=False)
    runner = CliRunner()
    result = runner.invoke(cli_app, ["macro-calendar", "--today"])
    assert result.exit_code == 0
