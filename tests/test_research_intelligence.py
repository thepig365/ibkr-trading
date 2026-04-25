"""Tests for the Research Intelligence Layer v2 core (Prompt 13B PART A/D/E)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.research_intelligence import (
    MacroEvent,
    NewsCatalyst,
    ResearchEvent,
    ResearchInstruction,
    ResearchReport,
    aggregate_symbol_profiles,
    build_instruction,
    classify_headline,
    classify_news_catalysts,
    detect_themes,
    render_markdown_report,
    render_telegram_digest,
    write_research_artifacts,
)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------
def test_earnings_headline_classified() -> None:
    ev = classify_headline("Apple beats Q3 earnings; revenue tops estimates", symbol="AAPL")
    assert ev.category == "earnings"
    assert ev.action == "soft_flag"
    assert ev.symbol == "AAPL"
    assert ev.direction == "bullish"
    assert ev.summary_zh.startswith("财报相关")


def test_analyst_headline_classified() -> None:
    ev = classify_headline("Goldman upgrades NVDA, raises price target to $180", symbol="NVDA")
    assert ev.category == "analyst_rating"
    assert ev.action == "soft_flag"
    assert "评级" in ev.summary_zh


def test_ai_infrastructure_boosts_priority() -> None:
    ev = classify_headline("AMD wins major AI infrastructure deal with hyperscaler", symbol="AMD")
    assert ev.category == "AI_infrastructure"
    assert ev.action == "boost_priority"
    assert ev.scope == "single_stock"


def test_fed_macro_headline() -> None:
    ev = classify_headline("FOMC holds rates steady; Powell hints at September cut")
    assert ev.category in {"Fed_rates", "macro"}
    assert ev.scope == "market"
    assert ev.action == "soft_flag"


def test_ambiguous_headline_low_confidence_manual_review() -> None:
    ev = classify_headline("Company comments on possible developments", symbol="ABC")
    assert ev.confidence == "low"
    assert ev.action in {"manual_review", "soft_flag"}
    assert ev.summary_zh == "基于标题的初步摘要，需人工复核。"


def test_regulatory_legal_manual_review() -> None:
    ev = classify_headline("XYZ faces SEC investigation over disclosure", symbol="XYZ")
    assert ev.category == "regulatory_legal"
    assert ev.action == "manual_review"
    assert ev.impact_level == "high"


def test_classify_batch() -> None:
    catalysts = [
        NewsCatalyst(timestamp="", provider="BRFG", article_id="1", symbol="NVDA",
                     headline="NVDA earnings beat estimates"),
        NewsCatalyst(timestamp="", provider="BRFG", article_id="2", symbol="AMD",
                     headline="AMD AI infrastructure deal"),
    ]
    out = classify_news_catalysts(catalysts)
    assert len(out) == 2
    assert out[0].category == "earnings"
    assert out[1].category == "AI_infrastructure"


# ---------------------------------------------------------------------------
# Theme detection
# ---------------------------------------------------------------------------
def test_ai_infrastructure_theme_detected() -> None:
    events = [
        classify_headline("AMD AI infrastructure mega-deal", symbol="AMD"),
        classify_headline("NVDA data center revenue accelerates", symbol="NVDA"),
    ]
    themes = detect_themes(
        classified_events=events,
        watchlist_symbols=["NVDA", "AMD", "AVGO", "MSFT"],
    )
    names = {t.theme for t in themes}
    assert "AI infrastructure" in names
    ai = next(t for t in themes if t.theme == "AI infrastructure")
    assert ai.strength in {"high", "medium"}
    assert "AMD" in ai.symbols and "NVDA" in ai.symbols


# ---------------------------------------------------------------------------
# Aggregation + instruction
# ---------------------------------------------------------------------------
def _ev(symbol: str, action: str, category: str = "earnings") -> ResearchEvent:
    return ResearchEvent(
        timestamp="",
        source="test",
        provider="t",
        symbol=symbol,
        scope="single_stock",
        category=category,  # type: ignore[arg-type]
        impact_level="medium",
        direction="unknown",
        confidence="medium",
        title_en=f"{symbol} test",
        summary_zh="x",
        action=action,  # type: ignore[arg-type]
        reason="t",
    )


def test_aggregate_symbol_profiles_collects_buckets() -> None:
    events = [
        _ev("NVDA", "boost_priority", "AI_infrastructure"),
        _ev("AAPL", "soft_flag", "earnings"),
        _ev("XYZ", "hard_block", "regulatory_legal"),
        _ev("XYZ", "manual_review", "regulatory_legal"),
    ]
    profiles = aggregate_symbol_profiles(
        classified_events=events,
        themes=[],
        watchlist_symbols=["NVDA", "AAPL", "XYZ"],
    )
    by_sym = {p.symbol: p for p in profiles}
    assert by_sym["NVDA"].boost_reasons
    assert by_sym["AAPL"].soft_flags
    assert by_sym["XYZ"].hard_blocks
    assert by_sym["XYZ"].manual_review_reasons


def test_build_instruction_only_blocks_on_hard_block() -> None:
    profiles = aggregate_symbol_profiles(
        classified_events=[
            _ev("NVDA", "boost_priority", "AI_infrastructure"),
            _ev("AAPL", "soft_flag", "earnings"),
        ],
        themes=[],
        watchlist_symbols=["NVDA", "AAPL"],
    )
    inst = build_instruction(
        date="2026-04-25",
        market_regime={"market_regime": "bullish", "new_positions_allowed": True},
        macro_events=[],
        ibkr_news_provider_status={"ibkr_news_available": False},
        symbol_profiles=profiles,
        watchlist_symbols=["NVDA", "AAPL"],
        smc_summary={},
    )
    assert inst.auto_paper_allowed is True  # macro/news soft flags do NOT flip this
    assert inst.paper_only is True
    assert inst.blocked_symbols == []
    assert "AAPL" in inst.soft_flag_symbols
    assert "NVDA" in inst.priority_watchlist


def test_macro_events_are_soft_flag_by_default() -> None:
    macro = MacroEvent(
        date="2026-05-13",
        time_et="08:30",
        event="CPI",
        category="CPI",
        impact_level="high",
        handling="soft_flag",
        notes="",
    )
    ev = macro.to_research_event()
    assert ev.action == "soft_flag"  # not hard_block
    assert ev.category == "macro"


def test_vix_missing_does_not_hard_block() -> None:
    profiles = aggregate_symbol_profiles(
        classified_events=[],
        themes=[],
        watchlist_symbols=["NVDA"],
    )
    inst = build_instruction(
        date="2026-04-25",
        market_regime={
            "market_regime": "neutral",
            "new_positions_allowed": True,
            "market_data": {"missing_fields": ["vix"]},
        },
        macro_events=[],
        ibkr_news_provider_status={"ibkr_news_available": False},
        symbol_profiles=profiles,
        watchlist_symbols=["NVDA"],
        smc_summary={},
    )
    assert inst.auto_paper_allowed is True
    assert inst.blocked_symbols == []


def test_instruction_paper_only_invariant() -> None:
    inst = build_instruction(
        date="2026-04-25",
        market_regime={"new_positions_allowed": False},
        macro_events=[],
        ibkr_news_provider_status={},
        symbol_profiles=[],
        watchlist_symbols=[],
        smc_summary={},
    )
    assert inst.paper_only is True
    assert inst.auto_paper_allowed is True  # regime alone does not flip the hint
    assert any("new_positions_allowed=false" in n for n in inst.bot_notes)


def test_instruction_auto_paper_false_only_on_per_symbol_hard_block() -> None:
    profiles = aggregate_symbol_profiles(
        classified_events=[_ev("XYZ", "hard_block", "regulatory_legal")],
        themes=[],
        watchlist_symbols=["XYZ"],
    )
    inst = build_instruction(
        date="2026-04-25",
        market_regime={"market_regime": "neutral", "new_positions_allowed": True},
        macro_events=[],
        ibkr_news_provider_status={},
        symbol_profiles=profiles,
        watchlist_symbols=["XYZ"],
        smc_summary={},
    )
    assert inst.auto_paper_allowed is False
    assert inst.blocked_symbols == ["XYZ"]


def test_instruction_json_schema_fields() -> None:
    inst = build_instruction(
        date="2026-04-25",
        market_regime={"market_regime": "bullish", "new_positions_allowed": True},
        macro_events=[],
        ibkr_news_provider_status={"ibkr_news_available": False},
        symbol_profiles=[],
        watchlist_symbols=[],
        smc_summary={},
    )
    d = inst.to_dict()
    required = {
        "date", "market_regime", "macro_events", "ibkr_news_provider_status",
        "priority_watchlist", "blocked_symbols", "manual_review_symbols",
        "soft_flag_symbols", "theme_tags_by_symbol", "event_risk_symbols",
        "auto_paper_allowed", "paper_only", "bot_notes",
    }
    assert required.issubset(d)


# ---------------------------------------------------------------------------
# Markdown / Telegram rendering + persistence
# ---------------------------------------------------------------------------
def _sample_report() -> ResearchReport:
    inst = build_instruction(
        date="2026-04-25",
        market_regime={"market_regime": "bullish", "new_positions_allowed": True},
        macro_events=[],
        ibkr_news_provider_status={"ibkr_news_available": False},
        symbol_profiles=[],
        watchlist_symbols=["NVDA"],
        smc_summary={},
    )
    return ResearchReport(
        date="2026-04-25",
        generated_at_utc="2026-04-25T00:00:00Z",
        market_regime={"market_regime": "bullish", "new_positions_allowed": True,
                       "regime_confidence": "high"},
        macro_events=[],
        ibkr_news=[],
        earnings=[],
        analyst_ratings=[],
        themes=[],
        symbol_profiles=[],
        watchlist_today=["NVDA"],
        smc_summary={},
        ibkr_news_provider_status={"ibkr_news_available": False},
        instruction=inst,
        notes=["sample"],
    )


def test_render_markdown_has_ten_sections() -> None:
    md = render_markdown_report(_sample_report())
    for section in (
        "一、市场环境判断",
        "二、今日宏观事件",
        "三、IBKR 订阅新闻",
        "四、财报与业绩事件",
        "五、分析师评级 / 目标价",
        "六、板块与主题",
        "七、高成交量 / 高波动股票",
        "八、SMC/ICT 技术预扫描",
        "九、今日 Watchlist",
        "十、交易引擎指令",
    ):
        assert section in md


def test_render_telegram_digest_concise_chinese() -> None:
    text = render_telegram_digest(_sample_report())
    assert "研究简报" in text
    assert "市场:" in text
    assert len(text) <= 3500  # comfortably under Telegram cap


def test_write_research_artifacts(tmp_path: Path) -> None:
    report = _sample_report()
    research_dir = tmp_path / "data" / "research"
    memory_path = tmp_path / "memory" / "RESEARCH-REPORT.md"
    paths = write_research_artifacts(
        report,
        research_dir=research_dir,
        memory_path=memory_path,
    )
    assert Path(paths["report_json"]).exists()
    assert Path(paths["instruction_json"]).exists()
    assert Path(paths["markdown"]).exists()
    # JSON is valid + contains the key invariants
    data = json.loads(Path(paths["report_json"]).read_text(encoding="utf-8"))
    assert data["paper_only"] is True
    assert data["block_live_trading"] is True
    assert data["instruction"]["paper_only"] is True
    inst = json.loads(Path(paths["instruction_json"]).read_text(encoding="utf-8"))
    assert inst["paper_only"] is True


def test_telegram_digest_full_split_under_limit() -> None:
    """The render → split path used by `research-report --full` must
    keep every part under the Telegram limit so we never spam.
    """
    from bot.news_report_zh import split_for_telegram

    md = render_markdown_report(_sample_report())
    parts = split_for_telegram(md, limit=3500)
    assert parts
    for part in parts:
        assert len(part) <= 3500


# ---------------------------------------------------------------------------
# No broker.place_order path
# ---------------------------------------------------------------------------
def test_research_intelligence_does_not_import_broker() -> None:
    """research_intelligence and research_providers must NOT import the broker."""
    import ast
    import importlib

    for modname in (
        "bot.research_intelligence",
        "bot.research_providers",
        "bot.research_providers.ibkr_news_provider",
        "bot.research_providers.manual_macro_calendar",
    ):
        mod = importlib.import_module(modname)
        src = Path(mod.__file__).read_text(encoding="utf-8")
        # Static check: no broker imports.
        assert "from .broker" not in src
        assert "from ..broker" not in src
        assert "import bot.broker" not in src
        # Dynamic AST check: no Call to anything ending in `.place_order`
        # or a bare `place_order(...)`. Mentions in docstrings/comments
        # are fine and intentional.
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    getattr(func, "attr", None)
                    or getattr(func, "id", None)
                    or ""
                )
                assert name not in {"place_order", "cancel_order"}, (
                    f"{modname} must not call {name}() — research layer is "
                    "read-only with respect to the broker."
                )
