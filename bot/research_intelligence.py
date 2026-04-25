"""Research Intelligence Layer v2 — dataclasses, classifier, theme rules.

This module is the single source of truth for the *Market Intelligence
Research Layer*. It is **read-only with respect to the broker**:

* It never imports :mod:`bot.broker`.
* It never calls ``place_order`` / ``cancel_order``.
* It never establishes an IBKR / TWS connection by itself — that
  responsibility lives in :mod:`bot.research_providers.ibkr_news_provider`
  and is only triggered by an explicit CLI / worker command.

The layer is deterministic-first: ambiguous headlines become
``confidence='low'`` + ``action='manual_review'`` (or ``soft_flag``)
rather than overclaiming impact. Hard blocks are reserved for
account / safety / platform issues.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Mapping

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
EventScope = Literal["market", "sector", "single_stock"]
EventCategory = Literal[
    "macro",
    "earnings",
    "analyst_rating",
    "breaking_news",
    "AI_infrastructure",
    "semiconductor",
    "Fed_rates",
    "inflation",
    "jobs",
    "tariff_trade",
    "geopolitical_oil",
    "M&A",
    "regulatory_legal",
    "CEO_management",
    "offering_dilution",
    "product_partnership",
    "unknown",
]
ImpactLevel = Literal["high", "medium", "low"]
Direction = Literal["bullish", "bearish", "mixed", "unknown"]
Confidence = Literal["high", "medium", "low"]
Action = Literal[
    "add_to_watchlist",
    "boost_priority",
    "manual_review",
    "soft_flag",
    "hard_block",
    "ignore",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ResearchEvent:
    """Base research event (macro, news, earnings, analyst, etc.).

    Most consumers should use the more specific subclasses below; this
    base class is what flows through the report.
    """

    timestamp: str  # ISO8601 UTC ("YYYY-MM-DDTHH:MM:SSZ") or empty when unknown
    source: str  # human-readable origin: "ibkr_news", "manual_macro_calendar", ...
    provider: str  # provider code (e.g. "BRFG", "DJ-N", "manual")
    scope: EventScope
    category: EventCategory
    impact_level: ImpactLevel
    direction: Direction
    confidence: Confidence
    title_en: str
    summary_zh: str
    action: Action
    reason: str
    symbol: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["extra"] = dict(self.extra)
        return d


@dataclass(frozen=True)
class MacroEvent:
    """Manual macro calendar event."""

    date: str  # YYYY-MM-DD (US/Eastern conventional date)
    time_et: str  # HH:MM, may be empty when scheduled "all day"
    event: str
    category: str  # "CPI" / "FOMC" / "NFP" / ...
    impact_level: ImpactLevel
    handling: Action  # usually soft_flag; hard_block must be explicit in YAML
    notes: str = ""

    def to_research_event(self) -> ResearchEvent:
        ts = _to_utc_iso(self.date, self.time_et)
        return ResearchEvent(
            timestamp=ts,
            source="manual_macro_calendar",
            provider="manual",
            scope="market",
            category="macro",
            impact_level=self.impact_level,
            direction="mixed",
            confidence="high",
            title_en=f"{self.event} ({self.category})",
            summary_zh=_macro_summary_zh(self),
            action=self.handling,
            reason=self.notes or "manual_macro_calendar",
            symbol=None,
            extra={
                "date": self.date,
                "time_et": self.time_et,
                "macro_category": self.category,
            },
        )


@dataclass(frozen=True)
class NewsCatalyst:
    """Single news headline (typically from IBKR)."""

    timestamp: str  # ISO UTC
    provider: str
    article_id: str
    symbol: str | None
    headline: str
    raw_payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EarningsEvent:
    """Upcoming or just-released earnings (best-effort)."""

    symbol: str
    when: str  # "BMO" / "AMC" / ISO timestamp / empty
    fiscal_period: str = ""
    notes: str = ""


@dataclass(frozen=True)
class AnalystRatingEvent:
    """Analyst upgrade / downgrade / price-target change."""

    symbol: str
    firm: str
    action: str  # "upgrade" / "downgrade" / "initiate" / "price_target"
    new_rating: str = ""
    new_target: str = ""
    notes: str = ""


@dataclass(frozen=True)
class ThemeSignal:
    """Detected market theme + supporting symbols."""

    theme: str
    symbols: list[str]
    strength: ImpactLevel
    reason: str


@dataclass(frozen=True)
class ResearchSymbolProfile:
    """Per-symbol aggregate after running classification."""

    symbol: str
    catalysts: list[ResearchEvent] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    soft_flags: list[str] = field(default_factory=list)
    hard_blocks: list[str] = field(default_factory=list)
    boost_reasons: list[str] = field(default_factory=list)
    manual_review_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchInstruction:
    """Machine-readable instruction packet consumed by other modules.

    The shape mirrors PART F in the prompt. ``auto_paper_allowed`` is
    derived only from market regime + hard blocks; macro / news risks
    do not flip it (those become soft flags).
    """

    date: str
    market_regime: str
    macro_events: list[dict[str, Any]]
    ibkr_news_provider_status: dict[str, Any]
    priority_watchlist: list[str]
    blocked_symbols: list[str]
    manual_review_symbols: list[str]
    soft_flag_symbols: list[str]
    theme_tags_by_symbol: dict[str, list[str]]
    event_risk_symbols: list[str]
    auto_paper_allowed: bool
    paper_only: bool
    bot_notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchReport:
    """Top-level research bundle written to disk."""

    date: str
    generated_at_utc: str
    market_regime: dict[str, Any]
    macro_events: list[ResearchEvent]
    ibkr_news: list[ResearchEvent]
    earnings: list[EarningsEvent]
    analyst_ratings: list[AnalystRatingEvent]
    themes: list[ThemeSignal]
    symbol_profiles: list[ResearchSymbolProfile]
    watchlist_today: list[str]
    smc_summary: dict[str, Any]
    ibkr_news_provider_status: dict[str, Any]
    instruction: ResearchInstruction
    notes: list[str]
    paper_only: bool = True
    block_live_trading: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "generated_at_utc": self.generated_at_utc,
            "paper_only": self.paper_only,
            "block_live_trading": self.block_live_trading,
            "market_regime": self.market_regime,
            "macro_events": [e.to_dict() for e in self.macro_events],
            "ibkr_news": [e.to_dict() for e in self.ibkr_news],
            "earnings": [asdict(e) for e in self.earnings],
            "analyst_ratings": [asdict(r) for r in self.analyst_ratings],
            "themes": [asdict(t) for t in self.themes],
            "symbol_profiles": [asdict(p) for p in self.symbol_profiles],
            "watchlist_today": list(self.watchlist_today),
            "smc_summary": self.smc_summary,
            "ibkr_news_provider_status": self.ibkr_news_provider_status,
            "instruction": self.instruction.to_dict(),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Deterministic classifier
# ---------------------------------------------------------------------------
_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")


# Order matters: more specific categories first. Each entry is
# (category, keyword tokens). All matching uses lowercase substring.
_CATEGORY_RULES: tuple[tuple[EventCategory, tuple[str, ...]], ...] = (
    (
        "analyst_rating",
        (
            "upgrade", "upgraded", "upgrades",
            "downgrade", "downgraded", "downgrades",
            "price target", "raises target", "cuts target",
            "buy rating", "sell rating", "neutral rating",
            "outperform", "underperform", "overweight", "underweight",
            "initiates coverage",
        ),
    ),
    (
        "earnings",
        (
            "earnings", "guidance", "revenue beat", "revenue miss",
            "eps beat", "eps miss", "earnings beat", "earnings miss",
            " q1 ", " q2 ", " q3 ", " q4 ",
            "fiscal year", "fy2026", "fy2025",
            "preliminary results",
        ),
    ),
    (
        "AI_infrastructure",
        (
            "ai infrastructure", "data center", "data-center", "datacentre",
            "gpu cluster", "ai cluster", "hyperscaler", "training cluster",
            "ai capex", "ai workload", "inference cluster",
        ),
    ),
    (
        "semiconductor",
        (
            "chip", "semiconductor", "silicon", "wafer", "foundry",
            "lithography", "tsmc", "asml", "amd", "nvidia",
        ),
    ),
    (
        "Fed_rates",
        (
            "fed ", "federal reserve", "powell", "fomc",
            "rate cut", "rate hike", "interest rate", "interest-rate",
            "treasury yield", "10-year", "10y yield", "2-year yield",
            "dot plot", "yield curve",
        ),
    ),
    (
        "inflation",
        (
            "cpi", "ppi", "pce", "inflation", "core inflation",
            "consumer prices", "producer prices",
        ),
    ),
    (
        "jobs",
        (
            "jobs", "payroll", "payrolls", "non-farm", "non farm",
            "nonfarm", "nfp", "unemployment", "jobless claims",
            "initial claims", "continuing claims", "ism employment",
        ),
    ),
    (
        "tariff_trade",
        (
            "tariff", "tariffs", "trade war", "export control",
            "export-control", "export curb", "sanction", "sanctions",
            "restricts exports",
        ),
    ),
    (
        "geopolitical_oil",
        (
            "opec", "oil ", "crude", "brent", "wti",
            "war breaks", "missile strike", "ceasefire",
            "geopolit", "middle east", "israel ", "iran ",
            "russia ", "ukraine ", "houthi",
        ),
    ),
    (
        "M&A",
        (
            "merger", "merges with", "acquires", "to acquire",
            "acquisition", "takeover", "buyout", "go private",
            "leveraged buyout",
        ),
    ),
    (
        "regulatory_legal",
        (
            "sec investigation", "sec probe", "sec subpoena",
            "doj investigation", "doj probe", "doj subpoena",
            "lawsuit", "antitrust", "ftc ", "regulator",
            "fines", "settles charges", "consent decree",
        ),
    ),
    (
        "CEO_management",
        (
            "ceo resigns", "ceo steps down", "ceo fired", "ceo terminated",
            "ceo replaced", "names new ceo", "appoints ceo",
            "cfo resigns", "cfo steps down", "management shakeup",
        ),
    ),
    (
        "offering_dilution",
        (
            "secondary offering", "follow-on offering", "follow on offering",
            "convertible offering", "convertible notes",
            "share offering", "atm offering", "stock offering",
            "dilution",
        ),
    ),
    (
        "product_partnership",
        (
            "partnership", "strategic partnership", "joint venture",
            "multi-year deal", "long-term contract", "supply deal",
            "wins contract", "secures contract", "announces deal",
        ),
    ),
)


# Heuristic impact + direction hints (purely additive on top of category).
_BULLISH_HINTS = (
    "beat", "raises", "raised", "surge", "jumps", "soars",
    "wins", "secures", "strategic partnership", "buyback",
    "guidance raised", "upgrade", "outperform",
)
_BEARISH_HINTS = (
    "miss", "misses", "cuts", "slashes", "plunges", "tumbles",
    "downgrade", "underperform", "lawsuit", "probe", "investigation",
    "guidance cut", "guidance withdrawn", "halted", "fraud",
    "bankruptcy", "going concern", "resigns", "steps down", "fired",
    "dilution", "secondary offering", "convertible offering",
    "tariff", "sanction",
)
_HIGH_IMPACT_HINTS = (
    "fomc", "powell", "cpi", "pce", "nfp", "non-farm",
    "trading halt", "halted", "bankruptcy", "going concern",
    "sec investigation", "sec probe", "doj investigation",
    "guidance withdrawn", "guidance cut", "ceo resigns",
    "fed surprise", "emergency rate", "war breaks",
    "missile strike", "tariff shock",
)
_AMBIGUOUS_HINTS = (
    "report", "reports", "discusses", "comments",
    "according to", "may ", "could ", "considers",
    "weighs", "explores", "rumored", "speculation",
)


def _haystack(*parts: str) -> str:
    s = " ".join(p for p in parts if p)
    return f" {s.lower()} ".replace(".", " ").replace(",", " ")


def classify_headline(
    headline: str,
    *,
    summary: str = "",
    symbol: str | None = None,
    timestamp: str = "",
    source: str = "ibkr_news",
    provider: str = "",
    extra: Mapping[str, Any] | None = None,
) -> ResearchEvent:
    """Convert a raw headline into a structured :class:`ResearchEvent`.

    Pure deterministic rules. Ambiguous text becomes
    ``confidence='low'`` + ``action='manual_review'``.
    """
    text = _haystack(headline, summary)

    category: EventCategory = "unknown"
    for cat, kws in _CATEGORY_RULES:
        if any(kw in text for kw in kws):
            category = cat
            break

    direction: Direction = "unknown"
    if any(kw in text for kw in _BULLISH_HINTS) and not any(
        kw in text for kw in _BEARISH_HINTS
    ):
        direction = "bullish"
    elif any(kw in text for kw in _BEARISH_HINTS) and not any(
        kw in text for kw in _BULLISH_HINTS
    ):
        direction = "bearish"
    elif any(kw in text for kw in _BULLISH_HINTS) and any(
        kw in text for kw in _BEARISH_HINTS
    ):
        direction = "mixed"

    impact_level: ImpactLevel = "low"
    if any(kw in text for kw in _HIGH_IMPACT_HINTS):
        impact_level = "high"
    elif category in {
        "Fed_rates",
        "inflation",
        "jobs",
        "earnings",
        "analyst_rating",
        "M&A",
        "regulatory_legal",
        "CEO_management",
        "offering_dilution",
    }:
        impact_level = "medium"

    confidence: Confidence = "medium"
    if category == "unknown":
        confidence = "low"
    elif any(kw in text for kw in _AMBIGUOUS_HINTS) and direction == "unknown":
        confidence = "low"
    elif impact_level == "high":
        confidence = "high"

    if category in {"AI_infrastructure", "semiconductor", "product_partnership"}:
        action: Action = "boost_priority"
    elif category in {
        "regulatory_legal",
        "offering_dilution",
        "CEO_management",
    }:
        action = "manual_review"
    elif category in {"earnings", "analyst_rating"}:
        action = "soft_flag"
    elif category in {
        "macro",
        "Fed_rates",
        "inflation",
        "jobs",
        "tariff_trade",
        "geopolitical_oil",
    }:
        action = "soft_flag"
    elif category == "M&A":
        action = "manual_review"
    elif category == "unknown":
        action = "manual_review" if confidence == "low" else "soft_flag"
    else:
        action = "soft_flag"

    if category == "unknown" and confidence == "low":
        summary_zh = "基于标题的初步摘要，需人工复核。"
    else:
        summary_zh = _summary_zh_for(category, direction, impact_level, headline)

    scope: EventScope
    if symbol:
        scope = "single_stock"
    elif category in {"macro", "Fed_rates", "inflation", "jobs", "tariff_trade"}:
        scope = "market"
    elif category in {"AI_infrastructure", "semiconductor"}:
        scope = "sector"
    else:
        scope = "market"

    return ResearchEvent(
        timestamp=timestamp,
        source=source,
        provider=provider or "unknown",
        symbol=symbol.upper() if symbol else None,
        scope=scope,
        category=category,
        impact_level=impact_level,
        direction=direction,
        confidence=confidence,
        title_en=headline.strip(),
        summary_zh=summary_zh,
        action=action,
        reason=f"category={category} direction={direction} impact={impact_level}",
        extra=dict(extra or {}),
    )


def classify_news_catalysts(
    catalysts: Iterable[NewsCatalyst],
) -> list[ResearchEvent]:
    """Classify a batch of :class:`NewsCatalyst` items."""
    out: list[ResearchEvent] = []
    for c in catalysts:
        out.append(
            classify_headline(
                c.headline,
                symbol=c.symbol,
                timestamp=c.timestamp,
                source="ibkr_news",
                provider=c.provider,
                extra={"article_id": c.article_id},
            )
        )
    return out


# ---------------------------------------------------------------------------
# Theme detection
# ---------------------------------------------------------------------------
# Hand-curated mapping of themes to ticker hints. Used as a fallback when
# headline / volume signals are weak. Kept conservative on purpose.
_THEME_TICKERS: dict[str, frozenset[str]] = {
    "AI infrastructure": frozenset(
        {"NVDA", "AMD", "AVGO", "TSM", "ASML", "MU", "ARM", "SMCI", "CRWV"}
    ),
    "semiconductors": frozenset(
        {"NVDA", "AMD", "AVGO", "TSM", "ASML", "MU", "ARM", "SMCI", "INTC", "QCOM"}
    ),
    "mega-cap tech": frozenset(
        {"AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"}
    ),
    "software": frozenset(
        {"MSFT", "ORCL", "CRM", "ADBE", "NOW", "PLTR", "SNOW", "DDOG"}
    ),
    "crypto-related": frozenset({"COIN", "MSTR", "MARA", "RIOT", "HUT"}),
    "banks": frozenset({"JPM", "BAC", "WFC", "C", "GS", "MS"}),
    "energy/oil": frozenset({"XOM", "CVX", "COP", "SLB", "OXY"}),
    "defense": frozenset({"LMT", "RTX", "NOC", "GD", "BA"}),
    "China ADR": frozenset({"BABA", "JD", "PDD", "BIDU", "NIO"}),
    "healthcare": frozenset({"LLY", "UNH", "JNJ", "MRK", "PFE"}),
}

_THEME_HEADLINE_HINTS: dict[str, tuple[str, ...]] = {
    "AI infrastructure": ("ai infrastructure", "data center", "gpu cluster"),
    "semiconductors": ("chip", "semiconductor", "wafer", "foundry"),
    "Fed/rates": ("fed", "powell", "rate cut", "rate hike", "fomc"),
    "energy/oil": ("opec", "oil ", "crude", "brent", "wti"),
    "tariff/trade": ("tariff", "trade war", "export control"),
}


def detect_themes(
    *,
    classified_events: Iterable[ResearchEvent],
    watchlist_symbols: Iterable[str],
    top_volume_symbols: Iterable[str] = (),
) -> list[ThemeSignal]:
    """Return active themes ranked by combined evidence."""
    wl = {s.upper() for s in watchlist_symbols if s}
    tv = {s.upper() for s in top_volume_symbols if s}

    headline_hits: dict[str, int] = {}
    for ev in classified_events:
        text = ev.title_en.lower()
        for theme, hints in _THEME_HEADLINE_HINTS.items():
            if any(h in text for h in hints):
                headline_hits[theme] = headline_hits.get(theme, 0) + 1

    themes: list[ThemeSignal] = []
    for theme, tickers in _THEME_TICKERS.items():
        present_wl = sorted(tickers & wl)
        present_tv = sorted(tickers & tv)
        n_news = headline_hits.get(theme, 0)
        n_news += headline_hits.get("Fed/rates", 0) if theme == "Fed/rates" else 0
        n_signal = len(present_wl) + len(present_tv) + n_news
        if n_signal == 0:
            continue
        if n_signal >= 4:
            strength: ImpactLevel = "high"
        elif n_signal >= 2:
            strength = "medium"
        else:
            strength = "low"
        symbols = sorted(set(present_wl) | set(present_tv))
        reasons: list[str] = []
        if present_wl:
            reasons.append(f"{len(present_wl)} in watchlist")
        if present_tv:
            reasons.append(f"{len(present_tv)} in top volume")
        if n_news:
            reasons.append(f"{n_news} matching headlines")
        themes.append(
            ThemeSignal(
                theme=theme,
                symbols=symbols,
                strength=strength,
                reason=", ".join(reasons) or "static ticker mapping",
            )
        )

    themes.sort(
        key=lambda t: (
            {"high": 3, "medium": 2, "low": 1}[t.strength],
            len(t.symbols),
        ),
        reverse=True,
    )
    return themes


# ---------------------------------------------------------------------------
# Per-symbol aggregation + instruction synthesis
# ---------------------------------------------------------------------------
def aggregate_symbol_profiles(
    *,
    classified_events: Iterable[ResearchEvent],
    themes: Iterable[ThemeSignal],
    watchlist_symbols: Iterable[str],
) -> list[ResearchSymbolProfile]:
    """Roll classified events + themes up per symbol."""
    by_symbol: dict[str, dict[str, Any]] = {}
    for sym in watchlist_symbols:
        by_symbol.setdefault(sym.upper(), _empty_symbol_record())

    for ev in classified_events:
        if not ev.symbol:
            continue
        rec = by_symbol.setdefault(ev.symbol, _empty_symbol_record())
        rec["catalysts"].append(ev)
        if ev.action == "soft_flag":
            rec["soft_flags"].append(_short_reason(ev))
        elif ev.action == "hard_block":
            rec["hard_blocks"].append(_short_reason(ev))
        elif ev.action == "boost_priority":
            rec["boost_reasons"].append(_short_reason(ev))
        elif ev.action == "manual_review":
            rec["manual_review_reasons"].append(_short_reason(ev))

    for t in themes:
        for sym in t.symbols:
            rec = by_symbol.setdefault(sym, _empty_symbol_record())
            rec["themes"].append(t.theme)
            if t.strength in {"medium", "high"}:
                rec["boost_reasons"].append(f"theme:{t.theme}({t.strength})")

    profiles = []
    for sym in sorted(by_symbol):
        r = by_symbol[sym]
        profiles.append(
            ResearchSymbolProfile(
                symbol=sym,
                catalysts=list(r["catalysts"]),
                themes=sorted(set(r["themes"])),
                soft_flags=list(r["soft_flags"]),
                hard_blocks=list(r["hard_blocks"]),
                boost_reasons=list(r["boost_reasons"]),
                manual_review_reasons=list(r["manual_review_reasons"]),
            )
        )
    return profiles


def build_instruction(
    *,
    date: str,
    market_regime: dict[str, Any],
    macro_events: Iterable[ResearchEvent],
    ibkr_news_provider_status: dict[str, Any],
    symbol_profiles: Iterable[ResearchSymbolProfile],
    watchlist_symbols: Iterable[str],
    smc_summary: dict[str, Any],
    extra_notes: Iterable[str] = (),
) -> ResearchInstruction:
    """Synthesise the machine-readable instruction packet.

    Decision rule (deliberately conservative):
    * Hard blocks come *only* from per-symbol ``hard_blocks``. Macro / VIX
      missing / news risk never flip ``auto_paper_allowed`` here.
    * ``auto_paper_allowed`` is also gated on the regime's
      ``new_positions_allowed`` field (defaults to True if missing).
    """
    profiles = list(symbol_profiles)
    blocked = sorted({p.symbol for p in profiles if p.hard_blocks})
    manual_review = sorted(
        {p.symbol for p in profiles if p.manual_review_reasons}
    )
    soft_flagged = sorted({p.symbol for p in profiles if p.soft_flags})
    boosted = sorted({p.symbol for p in profiles if p.boost_reasons})
    theme_tags: dict[str, list[str]] = {
        p.symbol: list(p.themes) for p in profiles if p.themes
    }
    event_risk = sorted(
        {
            p.symbol
            for p in profiles
            if any(e.impact_level in {"medium", "high"} for e in p.catalysts)
        }
    )

    wl = [s.upper() for s in watchlist_symbols if s]
    priority_pool = list(dict.fromkeys(boosted + wl))
    priority_pool = [s for s in priority_pool if s not in blocked]

    regime_allows = bool(market_regime.get("new_positions_allowed", True))
    auto_paper_allowed = regime_allows and not blocked

    notes = list(extra_notes)
    if not regime_allows:
        notes.append("market regime blocks new positions (new_positions_allowed=false)")
    if blocked:
        notes.append(f"{len(blocked)} hard-blocked symbol(s)")
    if not ibkr_news_provider_status.get("ibkr_news_available", False):
        notes.append("IBKR news entitlement unavailable; using soft-flags only")

    return ResearchInstruction(
        date=date,
        market_regime=str(market_regime.get("market_regime") or "unknown"),
        macro_events=[ev.to_dict() for ev in macro_events],
        ibkr_news_provider_status=dict(ibkr_news_provider_status),
        priority_watchlist=priority_pool,
        blocked_symbols=blocked,
        manual_review_symbols=manual_review,
        soft_flag_symbols=soft_flagged,
        theme_tags_by_symbol=theme_tags,
        event_risk_symbols=event_risk,
        auto_paper_allowed=auto_paper_allowed,
        paper_only=True,
        bot_notes=notes,
    )


# ---------------------------------------------------------------------------
# Markdown rendering (Chinese, 10 sections per PART F)
# ---------------------------------------------------------------------------
def render_markdown_report(report: ResearchReport) -> str:
    """Render the Chinese Markdown research report (PART F sections 一~十)."""
    lines: list[str] = []
    lines.append(f"# 研究报告 {report.date}")
    lines.append(
        f"_生成时间 (UTC): {report.generated_at_utc} ｜ paper_only={report.paper_only} ｜ "
        f"block_live_trading={report.block_live_trading}_"
    )
    lines.append("")

    lines.append("## 一、市场环境判断")
    md = report.market_regime
    if not md:
        lines.append("- 暂无 market_regime 数据。")
    else:
        lines.append(f"- 市场状态: **{md.get('market_regime', 'unknown')}**")
        lines.append(f"- 置信度: {md.get('regime_confidence', 'n/a')}")
        lines.append(
            f"- 允许新仓: {'是' if md.get('new_positions_allowed') else '否'}"
        )
        lines.append(
            f"- 允许研究扫描: {'是' if md.get('research_scans_allowed') else '否'}"
        )
        lines.append(f"- 原因: {md.get('reason') or '-'}")
    lines.append("")

    lines.append("## 二、今日宏观事件")
    if not report.macro_events:
        lines.append("- 无 / 未配置 `config/macro_calendar.yaml`。")
    else:
        for ev in report.macro_events:
            tag = ev.extra.get("macro_category") if isinstance(ev.extra, dict) else ""
            t = ev.extra.get("time_et") if isinstance(ev.extra, dict) else ""
            lines.append(
                f"- [{ev.impact_level}] {t or '全天'} ET — {ev.title_en} "
                f"({tag or ev.category}) → 处理: `{ev.action}`"
            )
            if ev.summary_zh:
                lines.append(f"  - {ev.summary_zh}")
    lines.append("")

    lines.append("## 三、IBKR 订阅新闻 / Breaking News")
    status = report.ibkr_news_provider_status
    lines.append(
        f"- 提供商可用: {'是' if status.get('ibkr_news_available') else '否'}"
    )
    if status.get("providers_detected"):
        lines.append(
            f"- 已检测到提供商: {', '.join(status.get('providers_detected') or [])}"
        )
    if status.get("missing_entitlements"):
        lines.append(
            "- 缺少订阅: " + ", ".join(status.get("missing_entitlements") or [])
        )
    if not report.ibkr_news:
        lines.append("- 暂无 IBKR 新闻条目。")
    else:
        for ev in report.ibkr_news[:30]:
            sym = f" [{ev.symbol}]" if ev.symbol else ""
            lines.append(
                f"- [{ev.impact_level}/{ev.confidence}]{sym} {ev.title_en} "
                f"→ `{ev.action}`"
            )
            if ev.summary_zh:
                lines.append(f"  - {ev.summary_zh}")
    lines.append("")

    lines.append("## 四、财报与业绩事件")
    if not report.earnings:
        lines.append("- 暂无（v2 阶段不主动抓取财报日历，需后续接入）。")
    else:
        for e in report.earnings:
            lines.append(
                f"- {e.symbol} — {e.when} {e.fiscal_period} {e.notes}".rstrip()
            )
    lines.append("")

    lines.append("## 五、分析师评级 / 目标价")
    if not report.analyst_ratings:
        lines.append("- 暂无（v2 阶段不主动抓取评级源，需后续接入）。")
    else:
        for r in report.analyst_ratings:
            lines.append(
                f"- {r.symbol}: {r.firm} {r.action} "
                f"{('→ ' + r.new_rating) if r.new_rating else ''} "
                f"{('target ' + r.new_target) if r.new_target else ''}".rstrip()
            )
    lines.append("")

    lines.append("## 六、板块与主题")
    if not report.themes:
        lines.append("- 暂无明显主题。")
    else:
        for t in report.themes:
            lines.append(
                f"- **{t.theme}** ({t.strength}) — "
                f"{', '.join(t.symbols) or '(无 watchlist 命中)'} ｜ {t.reason}"
            )
    lines.append("")

    lines.append("## 七、高成交量 / 高波动股票")
    top_vol = report.smc_summary.get("top_dollar_volume") if report.smc_summary else None
    if not top_vol:
        lines.append("- v2 阶段直接复用 dynamic watchlist 排序; 详见第九节。")
    else:
        for row in top_vol[:10]:
            lines.append(f"- {row}")
    lines.append("")

    lines.append("## 八、SMC/ICT 技术预扫描")
    smc = report.smc_summary or {}
    if not smc:
        lines.append("- 无最新 MTF SMC 摘要。")
    else:
        counts = smc.get("counts") or {}
        lines.append(f"- 已扫描标的: {smc.get('symbols_scanned', 0)}")
        if counts:
            lines.append("- 类别分布:")
            for k, v in counts.items():
                lines.append(f"  - {k}: {v}")
        eligible = smc.get("eligible_for_future_paper_trade") or []
        if eligible:
            lines.append(f"- 进入纸面候选: {', '.join(map(str, eligible))}")
    lines.append("")

    lines.append("## 九、今日 Watchlist")
    if not report.watchlist_today:
        lines.append("- 无 dynamic watchlist 文件。")
    else:
        lines.append(f"- 共 {len(report.watchlist_today)} 个标的")
        lines.append("- " + ", ".join(report.watchlist_today[:30]))
    lines.append("")

    lines.append("## 十、交易引擎指令")
    inst = report.instruction
    lines.append(f"- 自动纸面交易允许: {'是' if inst.auto_paper_allowed else '否'}")
    lines.append(f"- Paper-only: {'是' if inst.paper_only else '否'}")
    if inst.priority_watchlist:
        lines.append(
            f"- 优先 watchlist ({len(inst.priority_watchlist)}): "
            + ", ".join(inst.priority_watchlist[:30])
        )
    if inst.blocked_symbols:
        lines.append(
            f"- 硬禁止 ({len(inst.blocked_symbols)}): "
            + ", ".join(inst.blocked_symbols)
        )
    if inst.manual_review_symbols:
        lines.append(
            f"- 需人工复核 ({len(inst.manual_review_symbols)}): "
            + ", ".join(inst.manual_review_symbols[:20])
        )
    if inst.soft_flag_symbols:
        lines.append(
            f"- 软提醒 ({len(inst.soft_flag_symbols)}): "
            + ", ".join(inst.soft_flag_symbols[:20])
        )
    if inst.event_risk_symbols:
        lines.append(
            f"- 事件风险 ({len(inst.event_risk_symbols)}): "
            + ", ".join(inst.event_risk_symbols[:20])
        )
    if inst.bot_notes:
        lines.append("- 备注:")
        for n in inst.bot_notes:
            lines.append(f"  - {n}")
    lines.append("")
    lines.append("_本报告永不下单；仅作研究、风险标注与执行优先级建议。_")
    lines.append("")
    return "\n".join(lines)


def render_telegram_digest(report: ResearchReport) -> str:
    """Short Telegram-friendly Chinese digest (under default 3500 chars)."""
    lines: list[str] = []
    lines.append(f"📊 研究简报 {report.date}")
    md = report.market_regime or {}
    lines.append(
        f"市场: {md.get('market_regime', '未知')} ｜ "
        f"置信度 {md.get('regime_confidence', 'n/a')} ｜ "
        f"新仓 {'允许' if md.get('new_positions_allowed') else '禁止'}"
    )

    if report.macro_events:
        lines.append("")
        lines.append("📅 今日宏观:")
        for ev in report.macro_events[:5]:
            t = ev.extra.get("time_et") if isinstance(ev.extra, dict) else ""
            lines.append(
                f"- {t or '全天'} ET · {ev.title_en} [{ev.impact_level}]"
            )

    high_news = [
        ev
        for ev in report.ibkr_news
        if ev.impact_level in {"high", "medium"}
    ][:5]
    if high_news:
        lines.append("")
        lines.append("📰 重要新闻:")
        for ev in high_news:
            sym = f"[{ev.symbol}] " if ev.symbol else ""
            lines.append(f"- {sym}{ev.title_en[:80]}")

    if report.themes:
        lines.append("")
        lines.append("🔥 活跃主题:")
        for t in report.themes[:4]:
            lines.append(
                f"- {t.theme} ({t.strength}): "
                f"{', '.join(t.symbols[:6]) or 'n/a'}"
            )

    inst = report.instruction
    if inst.priority_watchlist:
        lines.append("")
        lines.append(
            "🎯 优先 Tier1: " + ", ".join(inst.priority_watchlist[:8])
        )
    if inst.blocked_symbols:
        lines.append("⛔ 硬禁: " + ", ".join(inst.blocked_symbols))
    if inst.soft_flag_symbols:
        lines.append(
            "⚠️ 软提醒: " + ", ".join(inst.soft_flag_symbols[:8])
        )

    lines.append("")
    lines.append(
        f"机器人指令: 自动纸面={'ON' if inst.auto_paper_allowed else 'OFF'} ｜ paper_only=ON"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def write_research_artifacts(
    report: ResearchReport,
    *,
    research_dir,  # pathlib.Path
    memory_path,  # pathlib.Path
) -> dict[str, str]:
    """Write report JSON + instruction JSON + Markdown. Returns paths."""
    research_dir.mkdir(parents=True, exist_ok=True)
    memory_path.parent.mkdir(parents=True, exist_ok=True)

    report_path = research_dir / f"{report.date}-research-report.json"
    inst_path = research_dir / f"{report.date}-research-instructions.json"

    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
    with inst_path.open("w", encoding="utf-8") as f:
        json.dump(report.instruction.to_dict(), f, indent=2, ensure_ascii=False)

    md = render_markdown_report(report)
    memory_path.write_text(md, encoding="utf-8")

    return {
        "report_json": str(report_path),
        "instruction_json": str(inst_path),
        "markdown": str(memory_path),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _empty_symbol_record() -> dict[str, Any]:
    return {
        "catalysts": [],
        "themes": [],
        "soft_flags": [],
        "hard_blocks": [],
        "boost_reasons": [],
        "manual_review_reasons": [],
    }


def _short_reason(ev: ResearchEvent) -> str:
    title = ev.title_en[:60].rstrip()
    return f"{ev.category}:{title}"


def _to_utc_iso(date: str, time_et: str) -> str:
    """Best-effort ET→UTC conversion. Falls back to date-only midnight UTC."""
    if not date:
        return ""
    try:
        if time_et and ":" in time_et:
            from zoneinfo import ZoneInfo  # noqa: PLC0415

            naive = datetime.strptime(f"{date} {time_et}", "%Y-%m-%d %H:%M")
            et = ZoneInfo("America/New_York")
            return (
                naive.replace(tzinfo=et)
                .astimezone(timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            )
        return datetime.strptime(date, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        ).strftime("%Y-%m-%dT00:00:00Z")
    except ValueError:
        return ""


def _macro_summary_zh(ev: MacroEvent) -> str:
    cat = ev.category.upper()
    if ev.handling == "hard_block":
        return f"{cat} 事件被显式标记为硬阻断（来自 macro_calendar.yaml）。"
    if ev.handling == "manual_review":
        return f"{cat} 事件需人工复核窗口期交易。"
    return f"{cat} 事件，时间窗附近交易请打软提醒标签；默认不阻断纸面测试。"


def _summary_zh_for(
    category: EventCategory,
    direction: Direction,
    impact: ImpactLevel,
    headline: str,
) -> str:
    bd = {"bullish": "偏多", "bearish": "偏空", "mixed": "混合", "unknown": "中性"}[
        direction
    ]
    impact_zh = {"high": "高", "medium": "中", "low": "低"}[impact]
    cat_zh = {
        "macro": "宏观事件",
        "earnings": "财报相关",
        "analyst_rating": "分析师评级/目标价",
        "breaking_news": "突发新闻",
        "AI_infrastructure": "AI 基建",
        "semiconductor": "半导体",
        "Fed_rates": "美联储/利率",
        "inflation": "通胀数据",
        "jobs": "就业数据",
        "tariff_trade": "关税/贸易",
        "geopolitical_oil": "地缘/油价",
        "M&A": "并购",
        "regulatory_legal": "监管/法律",
        "CEO_management": "高管变动",
        "offering_dilution": "增发/稀释",
        "product_partnership": "产品/合作",
        "unknown": "其他",
    }.get(category, "其他")
    short = headline[:80].rstrip()
    return f"{cat_zh} · 影响 {impact_zh} · {bd}：{short}"


__all__ = [
    "Action",
    "AnalystRatingEvent",
    "Confidence",
    "Direction",
    "EarningsEvent",
    "EventCategory",
    "EventScope",
    "ImpactLevel",
    "MacroEvent",
    "NewsCatalyst",
    "ResearchEvent",
    "ResearchInstruction",
    "ResearchReport",
    "ResearchSymbolProfile",
    "ThemeSignal",
    "aggregate_symbol_profiles",
    "build_instruction",
    "classify_headline",
    "classify_news_catalysts",
    "detect_themes",
    "render_markdown_report",
    "render_telegram_digest",
    "write_research_artifacts",
]
