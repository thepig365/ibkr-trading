"""Pre-open major news report orchestrator.

Workflow name: ``pre_open_news``.

Given a connected (or absent) IBKR client and the app configuration,
this module assembles a single pre-open risk briefing. It:

1. Collects holdings / open orders / account mode from IBKR.
2. Fetches daily-close history for VIX, VIX3M, SPY and QQQ to derive
   the market regime via :mod:`bot.market_regime`. Missing fields are
   recorded under ``market_data.missing_fields``.
3. Pulls IBKR historical news for holdings + watchlist when the
   subscription is available; cleans, deduplicates and categorises
   each headline.
4. Calls Perplexity for external research when
   ``PERPLEXITY_API_KEY`` is set (and degrades gracefully otherwise).
5. Applies deterministic risk rules to produce ``trade_allowed``,
   ``new_positions_allowed``, ``blocked_symbols`` and
   ``manual_review_required``.
6. Persists the report to ``memory/NEWS-REPORT.md`` and
   ``data/pre_open_news/YYYY-MM-DD.json``.
7. Sends a privacy-aware Telegram digest (with fallback).

**The report never places orders.** It only tightens risk posture.
The risk engine in :mod:`bot.risk_engine` remains the sole gatekeeper
for any future order-submission path.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import AppConfig
from .ibkr_client import IBKRClient, LiveTradingBlocked
from .market_regime import (
    MarketInputs,
    Regime,
    build_market_data,
    classify_regime,
    evaluate_regime,
    regime_is_defensive,
)
from .news_filters import (
    categorize_headline,
    classify_severity,
    clean_ibkr_headline,
    dedupe_headlines,
)
from .news_report_zh import (
    build_news_items,
    news_report_config,
    render_full_chinese_report,
    report_title_zh,
    split_for_telegram,
    telegram_language,
)
from .notifications import notify_event, send_telegram_message
from .research import PerplexityClient, ResearchRequest, ResearchResult

logger = logging.getLogger(__name__)


WORKFLOW_NAME = "pre_open_news"
NY_TZ = ZoneInfo("America/New_York")
MARKET_OPEN_HHMM = (9, 30)

# Severity ordering used when sorting headlines into "top N" lists.
_SEVERITY_RANK = {"high": 2, "medium": 1, "low": 0}

# Keywords used to mark symbols as blocked when high-severity news
# mentions them. Matched case-insensitively against headlines + summaries.
_BLOCK_KEYWORDS = (
    "trading halt",
    "halted",
    "halt",
    "fraud",
    "sec probe",
    "sec investigation",
    "doj probe",
    "doj investigation",
    "subpoena",
    "guidance cut",
    "guidance withdrawn",
    "slashes guidance",
    "bankruptcy",
    "chapter 11",
    "liquidity crisis",
    "going concern",
    "restatement",
)

# Keywords that should trigger "manual review" even if severity is
# only medium.
_MANUAL_REVIEW_KEYWORDS = (
    "earnings",
    "guidance",
    "downgrade",
    "upgrade",
    "gap",
    "short report",
    "recall",
    "lawsuit",
)

# Friendly labels for missing market-data fields.
_MARKET_FIELD_LABELS = {
    "vix": "VIX",
    "vix3m": "VIX3M",
    "spy": "SPY price",
    "spy_200ma": "SPY 200MA",
    "qqq": "QQQ price",
    "qqq_200ma": "QQQ 200MA",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class PreOpenReport:
    """Structured, JSON-serialisable pre-open report."""

    date: str
    run_time_new_york: str = "08:30"
    market_regime: Regime = "unknown"
    regime_confidence: str = "low"
    regime_research_scans_allowed: bool = False
    regime_reason: str = ""
    trade_allowed: bool = True
    new_positions_allowed: bool = True
    research_available: bool = True
    ibkr_news_available: bool = True
    external_research_available: bool = True
    blocked_symbols: list[str] = field(default_factory=list)
    manual_review_required: list[str] = field(default_factory=list)
    market_data: dict[str, Any] = field(default_factory=dict)
    major_news: list[dict[str, Any]] = field(default_factory=list)
    analyst_ratings: list[dict[str, Any]] = field(default_factory=list)
    earnings_news: list[dict[str, Any]] = field(default_factory=list)
    macro_news: list[dict[str, Any]] = field(default_factory=list)
    macro_events: list[dict[str, Any]] = field(default_factory=list)
    holdings_risk: list[dict[str, Any]] = field(default_factory=list)
    watchlist_catalysts: list[dict[str, Any]] = field(default_factory=list)
    bot_instruction: str = ""

    # Internal metadata (not serialised; kept for debugging/tests).
    _notes: list[str] = field(default_factory=list)

    def to_dict(self, *, cfg: "AppConfig | None" = None) -> dict[str, Any]:
        # Deterministic bilingual enrichment. Passing ``cfg`` lets the
        # Chinese block pick up user overrides (caps, include flags);
        # when ``cfg`` is None we fall back to the documented defaults.
        lang = telegram_language(cfg)
        try:
            full_zh = render_full_chinese_report(self, cfg=cfg)
        except Exception:  # noqa: BLE001 - never break JSON serialisation
            full_zh = ""
        try:
            news_items = build_news_items(self, cfg=cfg)
        except Exception:  # noqa: BLE001 - never break JSON serialisation
            news_items = []

        return {
            "date": self.date,
            "run_time_new_york": self.run_time_new_york,
            "market_regime": self.market_regime,
            "regime_confidence": self.regime_confidence,
            "regime_research_scans_allowed": self.regime_research_scans_allowed,
            "regime_reason": self.regime_reason,
            "trade_allowed": self.trade_allowed,
            "new_positions_allowed": self.new_positions_allowed,
            "research_available": self.research_available,
            "ibkr_news_available": self.ibkr_news_available,
            "external_research_available": self.external_research_available,
            "blocked_symbols": sorted(set(self.blocked_symbols)),
            "manual_review_required": sorted(set(self.manual_review_required)),
            "market_data": self.market_data,
            "major_news": self.major_news,
            "analyst_ratings": self.analyst_ratings,
            "earnings_news": self.earnings_news,
            "macro_news": self.macro_news,
            "macro_events": self.macro_events,
            "holdings_risk": self.holdings_risk,
            "watchlist_catalysts": self.watchlist_catalysts,
            "bot_instruction": self.bot_instruction,
            # --- Prompt 9.2: Chinese-first Telegram reports ---
            "language": "zh",
            "telegram_report_language": lang,
            "full_chinese_report": full_zh,
            "news_items": news_items,
            # Hard-coded research-only flags so downstream consumers
            # (Telegram command logger, scheduler) can rely on them.
            "execution_allowed": False,
            "research_only": True,
        }


@dataclass
class _Gathered:
    """All the raw inputs needed before we derive risk posture."""

    holdings_symbols: list[str]
    open_orders: list[dict[str, Any]]
    account_mode: str
    ibkr_connected: bool
    ibkr_news: list[dict[str, Any]]
    ibkr_news_available: bool
    research: ResearchResult
    market: MarketInputs
    market_notes: list[str]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def generate_report(
    cfg: AppConfig,
    ibkr_client: IBKRClient | None = None,
    perplexity_client: PerplexityClient | None = None,
    now: datetime | None = None,
    connect: bool = True,
) -> PreOpenReport:
    """Build a :class:`PreOpenReport` from all configured sources."""
    now = now or datetime.now(NY_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=NY_TZ)

    news_cfg = cfg.news.get("pre_open_news", {}) or {}

    report = PreOpenReport(
        date=now.astimezone(NY_TZ).strftime("%Y-%m-%d"),
        run_time_new_york=str(news_cfg.get("schedule_time_new_york", "08:30")),
    )

    gathered = _gather_inputs(
        cfg=cfg,
        news_cfg=news_cfg,
        ibkr_client=ibkr_client,
        perplexity_client=perplexity_client,
        connect=connect,
    )

    report._notes = list(gathered.market_notes)

    # Full regime evaluation (label + confidence + flags + market_data).
    regime_cfg = cfg.settings.market_regime.model_dump()
    regime_eval = evaluate_regime(gathered.market, regime_cfg)
    report.market_data = regime_eval.market_data
    report.market_regime = regime_eval.market_regime
    report.regime_confidence = regime_eval.regime_confidence
    report.regime_research_scans_allowed = regime_eval.research_scans_allowed
    report.regime_reason = regime_eval.reason

    # Research availability flags.
    report.ibkr_news_available = gathered.ibkr_news_available
    report.external_research_available = gathered.research.available
    report.research_available = bool(
        gathered.research.available or gathered.ibkr_news_available
    )

    # Build categorised news buckets from both LLM and IBKR sources.
    _populate_news_buckets(
        report=report,
        llm_payload=gathered.research.payload,
        ibkr_headlines=gathered.ibkr_news,
    )

    # Pull through LLM-only structured payloads.
    report.macro_events = list(gathered.research.payload.get("macro_events", []))
    report.holdings_risk = list(gathered.research.payload.get("holdings_risk", []))
    report.watchlist_catalysts = list(
        gathered.research.payload.get("watchlist_catalysts", [])
    )

    _apply_risk_rules(
        report=report,
        now_ny=now.astimezone(NY_TZ),
        ibkr_connected=gathered.ibkr_connected,
        news_cfg=news_cfg,
    )

    return report


# ---------------------------------------------------------------------------
# Gathering helpers
# ---------------------------------------------------------------------------
def _gather_inputs(
    cfg: AppConfig,
    news_cfg: dict[str, Any],
    ibkr_client: IBKRClient | None,
    perplexity_client: PerplexityClient | None,
    connect: bool,
) -> _Gathered:
    holdings: list[str] = []
    open_orders: list[dict[str, Any]] = []
    ibkr_news: list[dict[str, Any]] = []
    ibkr_news_available = False
    account_mode = cfg.settings.account.mode

    market_notes: list[str] = []
    market = MarketInputs()

    owns_client = False
    client = ibkr_client
    if client is None and connect:
        try:
            client = IBKRClient(cfg)
            client.connect()
            owns_client = True
        except LiveTradingBlocked as exc:
            market_notes.append(f"IBKR refused connection: {exc}")
            client = None
        except Exception as exc:  # noqa: BLE001
            market_notes.append(f"IBKR connection failed: {exc!r}")
            client = None

    if client is not None and client.is_connected:
        try:
            holdings = sorted({p.symbol for p in client.get_positions() if p.position})
        except Exception as exc:  # noqa: BLE001
            market_notes.append(f"get_positions failed: {exc!r}")
        try:
            open_orders = [o.to_dict() for o in client.get_open_orders()]
        except Exception as exc:  # noqa: BLE001
            market_notes.append(f"get_open_orders failed: {exc!r}")

        market, more_notes = _fetch_market_inputs(client)
        market_notes.extend(more_notes)

        if (news_cfg.get("ibkr_news", {}) or {}).get("enabled", True):
            ibkr_news, ibkr_news_available = _fetch_ibkr_news(
                client, cfg, news_cfg, holdings
            )
            if not ibkr_news_available:
                market_notes.append("IBKR news unavailable")

    ibkr_connected = client is not None and client.is_connected

    research = _fetch_research(
        cfg=cfg,
        news_cfg=news_cfg,
        perplexity_client=perplexity_client,
        holdings=holdings,
    )

    if owns_client and client is not None:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    return _Gathered(
        holdings_symbols=holdings,
        open_orders=open_orders,
        account_mode=account_mode,
        ibkr_connected=ibkr_connected,
        ibkr_news=ibkr_news,
        ibkr_news_available=ibkr_news_available,
        research=research,
        market=market,
        market_notes=market_notes,
    )


def _fetch_market_inputs(client: IBKRClient) -> tuple[MarketInputs, list[str]]:
    """Fetch VIX/VIX3M/SPY/QQQ data; failures degrade silently.

    Errors (subscription denied, contract not found, etc.) are not
    re-raised - the missing fields are recorded under
    ``market_data.missing_fields`` so the digest can flag them.
    """
    notes: list[str] = []
    vix = _safe_latest(client, "VIX", "IND", "CBOE")
    vix3m = _safe_latest(client, "VIX3M", "IND", "CBOE")
    spy = _safe_latest(client, "SPY", "STK", "ARCA")
    qqq = _safe_latest(client, "QQQ", "STK", "NASDAQ")
    spy_200 = _safe_sma(client, "SPY", 200, "STK", "ARCA")
    qqq_200 = _safe_sma(client, "QQQ", 200, "STK", "NASDAQ")

    for name, val in (
        ("VIX", vix),
        ("VIX3M", vix3m),
        ("SPY", spy),
        ("SPY 200MA", spy_200),
        ("QQQ", qqq),
        ("QQQ 200MA", qqq_200),
    ):
        if val is None:
            notes.append(f"{name} unavailable")

    return (
        MarketInputs(
            vix=vix,
            vix3m=vix3m,
            spy=spy,
            spy_200ma=spy_200,
            qqq=qqq,
            qqq_200ma=qqq_200,
        ),
        notes,
    )


def _safe_latest(
    client: IBKRClient, symbol: str, sec_type: str, exchange: str
) -> float | None:
    try:
        return client.get_latest_close(
            symbol, sec_type=sec_type, exchange=exchange
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("latest close failed for %s: %s", symbol, exc)
        return None


def _safe_sma(
    client: IBKRClient,
    symbol: str,
    window: int,
    sec_type: str,
    exchange: str,
) -> float | None:
    try:
        return client.get_simple_moving_average(
            symbol, window=window, sec_type=sec_type, exchange=exchange
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("SMA fetch failed for %s: %s", symbol, exc)
        return None


def _build_market_data(market: MarketInputs) -> dict[str, Any]:
    """Deprecated backwards-compat helper.

    New code should call :func:`bot.market_regime.evaluate_regime`
    and use its ``market_data`` dict. This wrapper is retained so
    external tests / scripts that still import it keep working.
    """
    return build_market_data(market)


def _fetch_ibkr_news(
    client: IBKRClient,
    cfg: AppConfig,
    news_cfg: dict[str, Any],
    holdings: list[str],
) -> tuple[list[dict[str, Any]], bool]:
    providers = client.get_news_providers()
    if not providers:
        return [], False

    ibkr_cfg = news_cfg.get("ibkr_news", {}) or {}
    max_per = int(ibkr_cfg.get("max_headlines_per_symbol", 5))

    watchlist = _combined_symbol_universe(cfg, news_cfg)
    target_symbols = sorted(set(holdings + watchlist))

    collected: list[dict[str, Any]] = []
    for sym in target_symbols:
        try:
            rows = client.get_historical_news(
                sym, provider_codes=providers, max_results=max_per
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("historical news failed for %s: %s", sym, exc)
            continue
        collected.extend(r.to_dict() for r in rows)
    return collected, True


def _fetch_research(
    cfg: AppConfig,
    news_cfg: dict[str, Any],
    perplexity_client: PerplexityClient | None,
    holdings: list[str],
) -> ResearchResult:
    if perplexity_client is None:
        px_cfg = (news_cfg.get("perplexity") or {})
        perplexity_client = PerplexityClient(
            api_key=cfg.perplexity.api_key,
            model=str(px_cfg.get("model", "sonar")),
            timeout_seconds=float(px_cfg.get("timeout_seconds", 30.0)),
        )

    if not perplexity_client.is_configured:
        return ResearchResult.empty(error="PERPLEXITY_API_KEY not set")

    indices = list(news_cfg.get("indices_and_etfs", []) or [])
    megacap = list(news_cfg.get("mega_cap_watchlist", []) or [])
    extras = _watchlist_symbols(cfg)
    topics = list(news_cfg.get("macro_topics", []) or [])

    req = ResearchRequest(
        today_iso=datetime.now(NY_TZ).strftime("%Y-%m-%d"),
        indices_and_etfs=indices,
        mega_cap_watchlist=megacap,
        extra_watchlist=extras,
        holdings_symbols=holdings,
        macro_topics=topics,
    )
    return perplexity_client.research(req)


def _combined_symbol_universe(
    cfg: AppConfig, news_cfg: dict[str, Any]
) -> list[str]:
    indices = list(news_cfg.get("indices_and_etfs", []) or [])
    megacap = list(news_cfg.get("mega_cap_watchlist", []) or [])
    extras = _watchlist_symbols(cfg)
    return sorted({s.upper() for s in indices + megacap + extras if s})


def _watchlist_symbols(cfg: AppConfig) -> list[str]:
    out: list[str] = []
    for item in cfg.watchlist.get("equities", []) or []:
        if isinstance(item, dict) and item.get("symbol"):
            out.append(str(item["symbol"]).upper())
    return out


# ---------------------------------------------------------------------------
# News normalisation / categorisation
# ---------------------------------------------------------------------------
def _normalise_llm_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    headline = (raw.get("headline") or "").strip()
    if not headline:
        return None
    severity = (raw.get("severity") or "").strip().lower()
    summary = (raw.get("summary") or "").strip()
    if severity not in ("low", "medium", "high"):
        severity = classify_severity(headline, summary)
    symbols = [str(s).upper() for s in (raw.get("symbols") or []) if s]
    return {
        "headline": headline,
        "source": str(raw.get("source") or "perplexity"),
        "symbols": symbols,
        "asset_classes": list(raw.get("asset_classes") or []),
        "impact": str(raw.get("impact") or "unknown"),
        "severity": severity,
        "confidence": str(raw.get("confidence") or "medium"),
        "summary": summary,
        "category": categorize_headline(headline, summary),
    }


def _normalise_ibkr_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    cleaned = clean_ibkr_headline(raw.get("headline") or "")
    if not cleaned:
        return None
    sym = (raw.get("symbol") or "").upper()
    severity = classify_severity(cleaned)
    return {
        "headline": cleaned,
        "source": str(raw.get("provider_code") or "IBKR"),
        "symbols": [sym] if sym else [],
        "asset_classes": ["equity"],
        "impact": "unknown",
        "severity": severity,
        "confidence": "low",
        "summary": "",
        "category": categorize_headline(cleaned),
    }


def _populate_news_buckets(
    report: PreOpenReport,
    llm_payload: dict[str, Any],
    ibkr_headlines: list[dict[str, Any]],
) -> None:
    """Combine LLM + IBKR headlines and split into category buckets."""
    items: list[dict[str, Any]] = []
    for raw in llm_payload.get("major_news", []) or []:
        norm = _normalise_llm_item(raw)
        if norm is not None:
            items.append(norm)
    for raw in ibkr_headlines:
        norm = _normalise_ibkr_item(raw)
        if norm is not None:
            items.append(norm)

    items = dedupe_headlines(items)

    def _sort_key(it: dict[str, Any]) -> tuple[int, str]:
        return (-_SEVERITY_RANK.get(it.get("severity", "low"), 0), it["headline"])

    by_cat: dict[str, list[dict[str, Any]]] = {
        "major": [],
        "analyst": [],
        "earnings": [],
        "macro": [],
    }
    for it in items:
        bucket = it.get("category", "major")
        by_cat.setdefault(bucket, []).append(it)
    for bucket in by_cat.values():
        bucket.sort(key=_sort_key)

    report.major_news = by_cat["major"]
    report.analyst_ratings = by_cat["analyst"]
    report.earnings_news = by_cat["earnings"]
    report.macro_news = by_cat["macro"]


# ---------------------------------------------------------------------------
# Risk rules
# ---------------------------------------------------------------------------
def _apply_risk_rules(
    report: PreOpenReport,
    now_ny: datetime,
    ibkr_connected: bool,
    news_cfg: dict[str, Any],
) -> None:
    blocked: set[str] = set()
    manual: set[str] = set()
    reasons: list[str] = []

    # 1. Scan all categorised news for blocks / manual review.
    for item in (
        report.major_news
        + report.earnings_news
        + report.macro_news
        + report.analyst_ratings
    ):
        headline = (item.get("headline") or "").lower()
        summary = (item.get("summary") or "").lower()
        text = f"{headline} {summary}"
        severity = (item.get("severity") or "low").lower()
        symbols = [s.upper() for s in item.get("symbols") or []]
        impact = (item.get("impact") or "unknown").lower()

        if any(kw in text for kw in _BLOCK_KEYWORDS):
            blocked.update(symbols)
        if severity == "high" and impact in {"negative", "mixed"}:
            blocked.update(symbols)
        if severity in {"medium", "high"} and any(
            kw in text for kw in _MANUAL_REVIEW_KEYWORDS
        ):
            manual.update(symbols)
        if severity == "high":
            manual.update(symbols)

    # 2. Holdings risk from Perplexity.
    for h in report.holdings_risk:
        sev = (h.get("severity") or "low").lower()
        sym = (h.get("symbol") or "").upper()
        if not sym:
            continue
        if sev == "high":
            manual.add(sym)
        rec = (h.get("recommendation") or "").lower()
        if any(kw in rec for kw in ("reduce", "exit", "hedge", "close")):
            manual.add(sym)

    # 3. Watchlist catalysts: earnings-today style events require review.
    for c in report.watchlist_catalysts:
        sev = (c.get("severity") or "low").lower()
        sym = (c.get("symbol") or "").upper()
        catalyst = (c.get("catalyst") or "").lower()
        if not sym:
            continue
        if "earnings" in catalyst or sev == "high":
            manual.add(sym)

    # 4. Macro events within the first 30 minutes after open.
    block_minutes = int(news_cfg.get("minutes_after_open_block", 30))
    if _high_severity_macro_near_open(report.macro_events, block_minutes):
        reasons.append(
            f"high-severity macro event within {block_minutes}m of the US open"
        )

    # 5. Regime-driven blocks.
    if regime_is_defensive(report.market_regime):
        reasons.append(f"defensive regime: {report.market_regime}")

    # 6. Critical market data missing -> block.
    missing = report.market_data.get("missing_fields", []) if report.market_data else []
    if "SPY 200MA" in missing and "QQQ 200MA" in missing:
        reasons.append("trend reference data missing (no SPY or QQQ 200MA)")

    # 7. External research gate.
    if not report.external_research_available:
        reasons.append("external research unavailable")

    # 8. IBKR connection.
    if not ibkr_connected:
        reasons.append("IBKR connection failed")

    report.blocked_symbols = sorted(blocked)
    report.manual_review_required = sorted(manual | set(report.blocked_symbols))

    trade_allowed = True  # existing positions stay managed; only new entries gate here.
    new_entries_allowed = not reasons

    report.trade_allowed = trade_allowed
    report.new_positions_allowed = new_entries_allowed
    report.bot_instruction = _compose_instruction(
        report=report,
        reasons=reasons,
        new_entries_allowed=new_entries_allowed,
    )


def _compose_instruction(
    report: PreOpenReport,
    reasons: list[str],
    new_entries_allowed: bool,
) -> str:
    if not new_entries_allowed:
        head = (
            "Research incomplete; block new entries unless manually reviewed."
            if report.market_regime == "unknown"
            or not report.external_research_available
            else "Block new entries."
        )
        return f"{head} Reasons: {'; '.join(reasons)}."
    if report.manual_review_required:
        return (
            "New entries permitted; review flagged symbols before taking them: "
            + ", ".join(report.manual_review_required)
        )
    return (
        "New entries permitted under existing risk caps; "
        f"regime={report.market_regime}."
    )


def _high_severity_macro_near_open(
    macro_events: list[dict[str, Any]], block_minutes: int
) -> bool:
    for ev in macro_events:
        if (ev.get("severity") or "").lower() != "high":
            continue
        t = (ev.get("time_new_york") or "").strip()
        mins = _minutes_after_open(t)
        if mins is None:
            continue
        if 0 <= mins <= block_minutes:
            return True
    return False


def _minutes_after_open(hhmm: str) -> int | None:
    """Return minutes past 09:30 America/New_York, or ``None`` on parse failure."""
    if not hhmm:
        return None
    try:
        hh, mm = hhmm.split(":", 1)
        h = int(hh)
        m = int(mm[:2])
    except Exception:  # noqa: BLE001
        return None
    return (h - MARKET_OPEN_HHMM[0]) * 60 + (m - MARKET_OPEN_HHMM[1])


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def save_report_json(cfg: AppConfig, report: PreOpenReport) -> Path:
    news_cfg = cfg.news.get("pre_open_news", {}) or {}
    json_dir = cfg.absolute(
        (news_cfg.get("output") or {}).get("json_dir", "data/pre_open_news")
    )
    json_dir.mkdir(parents=True, exist_ok=True)
    path = json_dir / f"{report.date}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(report.to_dict(cfg=cfg), f, indent=2, ensure_ascii=False)
    return path


def append_report_markdown(cfg: AppConfig, report: PreOpenReport) -> Path:
    news_cfg = cfg.news.get("pre_open_news", {}) or {}
    md_path = cfg.absolute(
        (news_cfg.get("output") or {}).get("markdown_file", "memory/NEWS-REPORT.md")
    )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_en = _render_markdown(report)
    # Prompt 9.2: always append the Chinese full briefing next to the
    # legacy English block so the on-disk memory mirrors Telegram.
    try:
        md_zh = render_full_chinese_report(report, cfg=cfg)
    except Exception:  # noqa: BLE001 - never block the English write
        md_zh = ""
    with md_path.open("a", encoding="utf-8") as f:
        f.write(md_en)
        if md_zh:
            f.write("\n\n### 中文完整报告\n\n")
            f.write(md_zh)
            f.write("\n")
    return md_path


def _fmt_headline_line(it: dict[str, Any]) -> str:
    syms = ", ".join(it.get("symbols") or []) or "-"
    src = it.get("source", "") or "-"
    # Square brackets are reserved for IBKR provider tags in the raw
    # feed, so we use parentheses here to avoid round-trip collisions
    # with the headline cleaner / "no raw metadata" assertions.
    return (
        f"- ({it.get('severity','?')}) {it.get('headline','')} "
        f"(source: {src}; symbols: {syms})"
    )


def _render_markdown(report: PreOpenReport) -> str:
    lines: list[str] = []
    lines.append(f"\n## Pre-Open Major News Report - {report.date}")
    lines.append(f"_run_time_new_york_: {report.run_time_new_york}")

    # --- Market regime
    md = report.market_data or {}
    lines.append("\n### Market regime")
    lines.append(f"- regime: **{report.market_regime}**")
    lines.append(f"- regime_confidence: **{report.regime_confidence}**")
    lines.append(
        f"- research_scans_allowed: "
        f"**{'yes' if report.regime_research_scans_allowed else 'no'}**"
    )
    lines.append(
        f"- new_positions_allowed: **{'yes' if report.new_positions_allowed else 'no'}**"
    )
    if report.regime_reason:
        lines.append(f"- regime_reason: {report.regime_reason}")
    lines.append(
        f"- research_available: {report.research_available} "
        f"(ibkr_news={report.ibkr_news_available}, "
        f"external={report.external_research_available})"
    )
    if md.get("vix") is not None:
        lines.append(f"- VIX: {md.get('vix')}  VIX3M: {md.get('vix3m')}  "
                     f"ratio: {md.get('vix_vix3m_ratio')}")
    if md.get("spy_above_200ma") is not None:
        lines.append(
            f"- SPY above 200MA: {md.get('spy_above_200ma')}  "
            f"QQQ above 200MA: {md.get('qqq_above_200ma')}"
        )

    # --- Missing market data
    missing = md.get("missing_fields", []) if md else []
    if missing:
        lines.append("\n### Missing market data")
        for m in missing:
            lines.append(f"- {m}")

    if report.blocked_symbols:
        lines.append("\n### Blocked symbols")
        lines.append("- " + ", ".join(report.blocked_symbols))
    if report.manual_review_required:
        lines.append("\n### Manual review required")
        lines.append("- " + ", ".join(report.manual_review_required))

    # --- Major news (top cleaned headlines)
    if report.major_news:
        lines.append("\n### Major news")
        for it in report.major_news[:10]:
            lines.append(_fmt_headline_line(it))

    # --- Earnings
    if report.earnings_news:
        lines.append("\n### Earnings news")
        for it in report.earnings_news[:10]:
            lines.append(_fmt_headline_line(it))

    # --- Macro
    if report.macro_news or report.macro_events:
        lines.append("\n### Macro")
        for it in report.macro_news[:10]:
            lines.append(_fmt_headline_line(it))
        for ev in report.macro_events:
            lines.append(
                f"- {ev.get('time_new_york','??:??')} "
                f"({ev.get('severity','?')}) "
                f"{ev.get('event','')}: {ev.get('market_relevance','')}"
            )

    # --- Analyst rating updates (max 5)
    if report.analyst_ratings:
        lines.append(
            f"\n### Analyst rating updates ({len(report.analyst_ratings)} total)"
        )
        for it in report.analyst_ratings[:5]:
            lines.append(_fmt_headline_line(it))
        if len(report.analyst_ratings) > 5:
            lines.append(
                f"- ... ({len(report.analyst_ratings) - 5} more, see JSON)"
            )

    # --- Holdings risk
    if report.holdings_risk:
        lines.append("\n### Holdings risk")
        for h in report.holdings_risk:
            lines.append(
                f"- {h.get('symbol','?')} ({h.get('severity','?')}): "
                f"{h.get('summary','')} -> {h.get('recommendation','')}"
            )

    # --- Watchlist catalysts
    if report.watchlist_catalysts:
        lines.append("\n### Watchlist catalysts")
        for c in report.watchlist_catalysts:
            lines.append(
                f"- {c.get('symbol','?')} ({c.get('severity','?')}): "
                f"{c.get('catalyst','')} - {c.get('summary','')}"
            )

    # --- Bot instruction
    lines.append("\n### Bot instruction")
    lines.append(report.bot_instruction or "(no instruction)")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram delivery
# ---------------------------------------------------------------------------
def _render_english_digest(cfg: AppConfig, report: PreOpenReport) -> str:
    """Legacy short English digest (kept for ``telegram_language: en``)."""
    news_cfg = cfg.news.get("pre_open_news", {}) or {}
    t_cfg = news_cfg.get("telegram", {}) or {}
    max_news = max(1, int(t_cfg.get("max_news_items", 3)))
    max_manual = int(t_cfg.get("max_manual_review_items", 10))
    max_blocked = int(t_cfg.get("max_blocked_items", 10))

    lines: list[str] = []
    lines.append(
        f"Market regime: {report.market_regime} "
        f"(confidence={report.regime_confidence})"
    )
    lines.append(
        f"New positions allowed: {'yes' if report.new_positions_allowed else 'no'}"
    )

    missing = (report.market_data or {}).get("missing_fields", [])
    if missing:
        lines.append("Missing market data: " + ", ".join(missing))
        if "VIX" in missing and (
            (report.market_data or {}).get("spy_above_200ma") is not None
            or (report.market_data or {}).get("qqq_above_200ma") is not None
        ):
            lines.append(
                "VIX/VIX3M unavailable; using SPY/QQQ trend fallback."
            )

    # IBKR vs external research are separate signals (Prompt 9.2).
    lines.append(
        "IBKR news data: "
        + ("available" if report.ibkr_news_available else "unavailable")
    )
    if not report.external_research_available:
        lines.append(
            "External research unavailable; using IBKR headlines only."
        )

    if report.blocked_symbols:
        lines.append(
            "Blocked: " + ", ".join(report.blocked_symbols[:max_blocked])
        )
    if report.manual_review_required:
        lines.append(
            "Manual review: "
            + ", ".join(report.manual_review_required[:max_manual])
        )

    top_pool = [m for m in report.major_news if m.get("severity") == "high"]
    if not top_pool:
        top_pool = list(report.major_news)
    top = top_pool[:max_news]
    if top:
        lines.append("")
        lines.append(f"Top major news (showing {len(top)}):")
        for it in top:
            syms = ", ".join(it.get("symbols") or []) or "-"
            lines.append(
                f"- [{it.get('severity','?')}] {it.get('headline','')} ({syms})"
            )

    if report.earnings_news:
        lines.append("")
        lines.append(
            f"Earnings news: {len(report.earnings_news)} items in full report."
        )

    if report.analyst_ratings:
        lines.append(
            f"Analyst ratings: {len(report.analyst_ratings)} updates detected, "
            "see full report."
        )

    lines.append("")
    lines.append(f"Bot instruction: {report.bot_instruction}")
    return "\n".join(lines)


def _send_chinese_report(
    cfg: AppConfig,
    report: PreOpenReport,
    journal=None,
    *,
    now: datetime | None = None,
) -> bool:
    """Send the full seven-section Chinese report, splitting if needed.

    Returns True when **every** part was delivered. A partial delivery
    returns False and the undelivered parts fall through to the
    existing Telegram fallback (DAILY-SUMMARY.md) via
    :func:`send_telegram_message`.
    """
    title = report_title_zh(report, now=now)
    body = render_full_chinese_report(
        report, cfg=cfg, now=now, include_title=False
    )
    tele_cfg = (cfg.telegram_cfg or {}).get("command_interface") or {}
    limit = int(tele_cfg.get("max_message_length", 3500))
    parts = split_for_telegram(body, limit=limit, header=title)

    all_ok = True
    for part in parts:
        ok = send_telegram_message(part, cfg=cfg, journal=journal)
        all_ok = all_ok and ok
    return all_ok


def notify_report(
    cfg: AppConfig,
    report: PreOpenReport,
    journal=None,
    *,
    now: datetime | None = None,
) -> bool:
    """Send the pre-open Telegram digest.

    When ``news_report.telegram_language`` is ``"zh"`` (the Prompt 9.2
    default) we send the full Chinese report, splitting into multiple
    messages if needed. Privacy redaction and credential fallback still
    happen inside :func:`bot.notifications.send_telegram_message`.
    """
    lang = telegram_language(cfg)
    if lang == "zh":
        return _send_chinese_report(cfg, report, journal=journal, now=now)

    severity = "info"
    if not report.new_positions_allowed or report.blocked_symbols:
        severity = "warning"
    if report.market_regime == "crisis":
        severity = "urgent"

    return notify_event(
        event_type=WORKFLOW_NAME,
        title=f"Pre-Open Major News Report - {report.date}",
        body=_render_english_digest(cfg, report),
        severity=severity,  # type: ignore[arg-type]
        cfg=cfg,
        journal=journal,
    )


__all__ = [
    "PreOpenReport",
    "WORKFLOW_NAME",
    "generate_report",
    "save_report_json",
    "append_report_markdown",
    "notify_report",
]
