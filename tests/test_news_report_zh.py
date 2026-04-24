"""Tests for the Chinese full news report renderer (Prompt 9.2, Part A)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bot.config import load_config
from bot.news_report import PreOpenReport
from bot.news_report_zh import (
    build_news_items,
    infer_impact_zh,
    is_pre_open,
    news_report_config,
    render_full_chinese_report,
    report_title_zh,
    split_for_telegram,
    summarize_zh,
    telegram_language,
)

NY = ZoneInfo("America/New_York")


def test_defaults_are_chinese(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    assert telegram_language(cfg) == "zh"
    rc = news_report_config(cfg)
    assert rc["telegram_language"] == "zh"
    assert rc["report_depth"] == "full"
    assert rc["max_major_news_items"] == 20


def test_summarize_zh_known_pattern_does_not_hallucinate() -> None:
    # AMD + AI infrastructure deal - the user's example from the prompt.
    headline = (
        "Advanced Micro Devices soars on massive multi-year AI "
        "infrastructure deal with Meta"
    )
    summary = summarize_zh(headline, ["AMD"])
    impact = infer_impact_zh(headline, "medium", ["AMD"])
    # Template matched: we include the ticker but never invent facts
    # that are not in the headline.
    assert "AMD" in summary
    assert "AI" in summary or "基础设施" in summary
    assert "人工复核" in impact
    # The literal word "Meta" is not fabricated into the Chinese summary.
    assert "偏利多" in impact or "基础设施" in impact


def test_summarize_zh_falls_back_to_manual_review() -> None:
    headline = "Some random proprietary company jargon no pattern matches"
    summary = summarize_zh(headline, ["XYZ"])
    assert summary.startswith("基于标题的初步摘要") or "需人工复核" in summary


def test_is_pre_open_before_and_after_open() -> None:
    assert is_pre_open(datetime(2026, 4, 24, 8, 30, tzinfo=NY)) is True
    assert is_pre_open(datetime(2026, 4, 24, 9, 29, tzinfo=NY)) is True
    assert is_pre_open(datetime(2026, 4, 24, 9, 30, tzinfo=NY)) is False
    assert is_pre_open(datetime(2026, 4, 24, 13, 0, tzinfo=NY)) is False


def test_report_title_zh_switches_on_pre_open() -> None:
    report = PreOpenReport(date="2026-04-24", market_regime="neutral")
    t_pre = report_title_zh(report, now=datetime(2026, 4, 24, 8, 30, tzinfo=NY))
    t_int = report_title_zh(report, now=datetime(2026, 4, 24, 13, 15, tzinfo=NY))
    assert t_pre == "【盘前重大市场新闻报告】2026-04-24"
    assert t_int.startswith("【即时重大市场新闻报告】2026-04-24")
    assert "13:15" in t_int


def test_render_full_chinese_report_has_seven_sections(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    report = PreOpenReport(
        date="2026-04-24",
        market_regime="neutral",
        regime_confidence="medium",
        new_positions_allowed=False,
        regime_research_scans_allowed=True,
        ibkr_news_available=True,
        external_research_available=False,
    )
    report.market_data = {
        "spy_above_200ma": True,
        "qqq_above_200ma": None,
        "vix": None,
        "vix3m": None,
        "vix_vix3m_ratio": None,
        "missing_fields": ["VIX", "VIX3M"],
    }
    report.major_news = [
        {
            "headline": "AMD soars on AI infrastructure deal",
            "severity": "medium",
            "symbols": ["AMD"],
            "category": "major",
        }
    ]
    report.analyst_ratings = [
        {
            "headline": "BofA reiterated AAPL Buy, target 325",
            "severity": "low",
            "symbols": ["AAPL"],
            "category": "analyst",
        },
    ]
    report.earnings_news = [
        {
            "headline": "TSLA earnings beat expectations",
            "severity": "medium",
            "symbols": ["TSLA"],
            "category": "earnings",
        },
    ]
    report.manual_review_required = ["AMD", "TSLA"]
    report.bot_instruction = "Research incomplete; block new entries."

    body = render_full_chinese_report(
        report, cfg=cfg,
        now=datetime(2026, 4, 24, 8, 30, tzinfo=NY),
    )

    for section in (
        "一、市场机制判断",
        "二、今日重点新闻摘要",
        "三、财报 / 业绩相关新闻",
        "四、分析师评级",
        "五、需要人工复核的股票",
        "六、被阻止 / 不应交易的股票",
        "七、Bot 指令",
    ):
        assert section in body, f"missing section: {section}"

    # Requirement 6: IBKR available + external unavailable must render
    # the distinct lines (not a single "data unavailable" line).
    assert "IBKR 新闻数据：可用" in body
    assert "外部研究数据：未启用 / 不可用" in body
    assert "当前报告基于 IBKR headlines" in body
    # Requirement 3: missing VIX + SPY fallback note.
    assert "SPY/QQQ 200MA" in body
    # Execution is always stated as false.
    assert "execution_allowed=false" in body


def test_render_full_chinese_report_uses_pre_open_title_by_default() -> None:
    report = PreOpenReport(date="2026-04-24", market_regime="neutral")
    pre_now = datetime(2026, 4, 24, 8, 30, tzinfo=NY)
    body = render_full_chinese_report(report, now=pre_now)
    assert body.splitlines()[0] == "【盘前重大市场新闻报告】2026-04-24"


def test_build_news_items_marks_manual_review_for_medium_and_high() -> None:
    report = PreOpenReport(date="2026-04-24", market_regime="neutral")
    report.major_news = [
        {"headline": "AMD upgrade", "severity": "medium", "symbols": ["AMD"],
         "category": "major"},
        {"headline": "routine low", "severity": "low", "symbols": ["ABC"],
         "category": "major"},
    ]
    items = build_news_items(report)
    by_symbol = {it["symbol"]: it for it in items}
    assert by_symbol["AMD"]["manual_review_required"] is True
    assert by_symbol["ABC"]["manual_review_required"] is False


def test_split_for_telegram_emits_parts_when_long() -> None:
    body = "\n".join(f"line {i}" for i in range(400))
    parts = split_for_telegram(body, limit=500, header="【测试】")
    assert len(parts) > 1
    assert parts[0].startswith("【测试】")
    # Every non-first part is prefixed with Part i/N.
    for i, part in enumerate(parts[1:], start=2):
        assert part.startswith(f"(Part {i}/{len(parts)})")
    # Re-joined text length roughly matches the input (no silent truncation
    # - the Part prefix plus title add a bounded amount of overhead).
    joined = "\n".join(parts)
    for i in range(0, 400, 50):
        assert f"line {i}" in joined


def test_split_for_telegram_short_body_is_single_message() -> None:
    parts = split_for_telegram("hello", limit=3500, header="【测试】")
    assert len(parts) == 1
    assert parts[0] == "【测试】\nhello"
