"""Chinese full-report renderer for the pre-open / ad-hoc news workflow.

Prompt 9.2 Part A. Everything here is **deterministic** and
**template-based**: we never call an LLM, we never invent facts that
are not already in the headline text or the structured
``PreOpenReport``. When we cannot infer a confident summary we tell the
operator that the summary is a headline-level placeholder that needs
manual review ("基于标题的初步摘要，需人工复核").

The module produces three things:

* :func:`build_news_items` - a list of enriched per-headline records
  (``headline_en`` / ``summary_zh`` / ``market_impact_zh`` /
  ``manual_review_required``) suitable for the ``news_items`` field in
  the JSON report.
* :func:`render_full_chinese_report` - the seven-section Chinese
  report string we ship in Telegram (and append to ``NEWS-REPORT.md``).
* :func:`split_for_telegram` - safe splitting of long reports into
  ``Part 1 / N``, ``Part 2 / N`` ... chunks so we never silently
  truncate a message.

No function in this module ever places an order. ``execution_allowed``
and ``research_only`` are not toggles here - they are facts we print
at the bottom of every report.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable
from zoneinfo import ZoneInfo

if TYPE_CHECKING:  # pragma: no cover
    from .config import AppConfig
    from .news_report import PreOpenReport


NY_TZ = ZoneInfo("America/New_York")
MARKET_OPEN_HHMM = (9, 30)

# Default caps mirror the documented news_report block. We still call
# out to ``news_report_config`` so a user-provided YAML overrides them.
_DEFAULT_NEWS_REPORT_CFG: dict[str, Any] = {
    "telegram_language": "zh",
    "report_depth": "full",
    "max_major_news_items": 20,
    "max_analyst_rating_items": 20,
    "max_earnings_items": 10,
    "include_english_headline": True,
    "include_chinese_summary": True,
}

# Telegram hard limit is 4096. We stay well below to leave room for
# HTML escaping / severity prefixes / part counters.
_DEFAULT_TELEGRAM_LIMIT = 3500


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def news_report_config(cfg: "AppConfig | None") -> dict[str, Any]:
    """Return the merged ``news_report`` config block.

    The defaults make Chinese the primary Telegram language so a missing
    or older news.yaml still produces a Chinese briefing.
    """
    merged = dict(_DEFAULT_NEWS_REPORT_CFG)
    if cfg is None:
        return merged
    raw = (cfg.news or {}).get("news_report") or {}
    for k, v in raw.items():
        if v is not None:
            merged[k] = v
    return merged


def telegram_language(cfg: "AppConfig | None") -> str:
    lang = str(news_report_config(cfg).get("telegram_language", "zh")).lower()
    return "en" if lang == "en" else "zh"


# ---------------------------------------------------------------------------
# Deterministic Chinese summary templates
#
# Each tuple is (regex, summary_template, impact_template). Templates
# may reference ``{symbols}`` which we substitute with the comma-joined
# list of tickers. The regex is matched case-insensitively against the
# English headline. We deliberately keep patterns conservative so we do
# not "translate" something we cannot recognise.
# ---------------------------------------------------------------------------
_SYM = "{symbols}"

_TEMPLATES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    # --- High-signal deal / AI infrastructure patterns (checked first so
    #     "AMD soars on AI infrastructure deal" doesn't fall into the
    #     generic "股价大幅上涨" bucket).
    (
        re.compile(
            r"\b(ai|artificial intelligence)\s+(infrastructure|deal|partnership|chip|gpu)\b",
            re.I,
        ),
        "AI / 基础设施相关消息，涉及 " + _SYM + "。",
        "可能影响 AI 芯片、数据中心基础设施及相关半导体板块情绪；需人工复核发布时间和盘前价格反应。",
    ),
    (
        re.compile(
            r"\b(multi[- ]year|multiyear)\s+(ai|deal|contract|agreement)\b",
            re.I,
        ),
        "多年合作 / 合同消息，涉及 " + _SYM + "。",
        "可能对中长期营收指引形成支撑；需人工复核合同金额和业务驱动。",
    ),
    # --- Analyst ratings
    (
        re.compile(r"\b(upgrade[sd]?|raised to|reiterated buy|overweight)\b", re.I),
        "分析师上调/重申买入评级，涉及 " + _SYM + "。",
        "偏利多；需人工复核评级机构、新目标价和盘前价格反应。",
    ),
    (
        re.compile(r"\b(downgrade[sd]?|cut to|lowered to|sell rating)\b", re.I),
        "分析师下调评级，涉及 " + _SYM + "。",
        "偏利空；需人工复核下调幅度和新目标价。",
    ),
    (
        re.compile(r"\b(price target|target|pt)\b.*\b(raised|lifted|lift|increase)\b", re.I),
        "分析师上调目标价，涉及 " + _SYM + "。",
        "偏利多；关注盘前跳空和成交量。",
    ),
    (
        re.compile(r"\b(price target|target|pt)\b.*\b(cut|lowered|reduce)\b", re.I),
        "分析师下调目标价，涉及 " + _SYM + "。",
        "偏利空；关注盘前跳空和支撑位。",
    ),
    # --- Earnings
    (
        re.compile(r"\bearnings\b.*\b(beat|top|exceed)\b", re.I),
        "财报业绩超预期，涉及 " + _SYM + "。",
        "偏利多；关注 EPS / 营收细节和盘后反应。",
    ),
    (
        re.compile(r"\bearnings\b.*\b(miss|fall short|below)\b", re.I),
        "财报业绩低于预期，涉及 " + _SYM + "。",
        "偏利空；需关注盘前跳空和指引变动。",
    ),
    (
        re.compile(r"\b(raises?|lift|increase[sd]?)\s+(full[- ]year\s+)?guidance\b", re.I),
        "上调业绩指引，涉及 " + _SYM + "。",
        "偏利多；关注上调幅度和业务驱动。",
    ),
    (
        re.compile(r"\b(cut|lower|slash)[s]?\s+(full[- ]year\s+)?guidance\b", re.I),
        "下调业绩指引，涉及 " + _SYM + "。",
        "偏利空；需重点关注下调原因。",
    ),
    (
        re.compile(r"\bearnings\b", re.I),
        "财报相关新闻，涉及 " + _SYM + "。",
        "基于标题的初步摘要，需人工复核 EPS / 营收 / 指引具体内容。",
    ),
    # --- M&A / deals / partnerships
    (
        re.compile(r"\b(acqui[rs]|acquisition|merger|buyout|takeover)\b", re.I),
        "并购 / 收购相关消息，涉及 " + _SYM + "。",
        "对标的与买方走势均可能造成较大影响；需人工复核交易条款。",
    ),
    (
        re.compile(
            r"\b(deal|partnership|agreement|collaboration)\b.*\b(ai|infrastructure|cloud|gpu|chip)\b",
            re.I,
        ),
        "与 AI / 基础设施相关的合作或协议，涉及 " + _SYM + "。",
        "可能影响 AI 芯片、数据中心基础设施及相关半导体板块情绪；需人工复核发布时间和盘前价格反应。",
    ),
    (
        re.compile(r"\b(contract win|awarded|wins? contract)\b", re.I),
        "重要合同 / 中标消息，涉及 " + _SYM + "。",
        "偏利多；关注合同金额与执行周期。",
    ),
    # --- Regulatory / negative
    (
        re.compile(r"\b(sec probe|sec investigation|doj probe|subpoena|investigation)\b", re.I),
        "监管调查相关消息，涉及 " + _SYM + "。",
        "偏利空；需立即人工复核是否构成强制阻止。",
    ),
    (
        re.compile(r"\b(halt(ed)?|trading halt)\b", re.I),
        "交易暂停相关消息，涉及 " + _SYM + "。",
        "风险事件；该标的应进入阻止清单，人工复核后再处理。",
    ),
    (
        re.compile(r"\b(bankruptcy|chapter\s*11|liquidity crisis|going concern)\b", re.I),
        "破产 / 流动性风险相关消息，涉及 " + _SYM + "。",
        "严重利空；建议立即阻止相关仓位。",
    ),
    (
        re.compile(r"\b(recall|lawsuit|class action|settlement)\b", re.I),
        "产品召回 / 诉讼 / 和解相关消息，涉及 " + _SYM + "。",
        "偏利空；需人工复核金额与业务影响范围。",
    ),
    # --- Price action / technicals
    (
        re.compile(r"\b(soars?|surges?|rallies|jumps?|rips?)\b", re.I),
        "股价大幅上涨相关消息，涉及 " + _SYM + "。",
        "偏利多；需复核触发原因（基本面 vs 技术面）。",
    ),
    (
        re.compile(r"\b(plunge[sd]?|tumbles?|crashe[sd]?|slides?|sinks?)\b", re.I),
        "股价大幅下跌相关消息，涉及 " + _SYM + "。",
        "偏利空；需复核下跌原因与关键技术位。",
    ),
    # --- Macro
    (
        re.compile(r"\b(cpi|inflation|ppi)\b", re.I),
        "通胀 / CPI / PPI 相关宏观消息。",
        "对全市场风险偏好和利率预期有直接影响。",
    ),
    (
        re.compile(r"\b(fed|fomc|powell|rate decision|interest rate)\b", re.I),
        "美联储 / 利率相关宏观消息。",
        "对全市场 beta、科技股估值和 VIX 有直接影响。",
    ),
    (
        re.compile(r"\b(jobs report|payroll|unemployment|nfp)\b", re.I),
        "就业 / 非农相关宏观消息。",
        "影响全市场风险偏好与美联储政策预期。",
    ),
)


_GENERIC_SUMMARY = (
    "基于标题的初步摘要，需人工复核。"
)

_GENERIC_IMPACT = (
    "潜在影响未知；建议人工复核新闻真实性、发布时间和盘前价格反应。"
)


def _fmt_symbols(symbols: Iterable[str] | None) -> str:
    items = [s for s in (symbols or []) if s]
    if not items:
        return "-"
    return ", ".join(items)


def summarize_zh(headline: str, symbols: Iterable[str] | None = None) -> str:
    """Return a deterministic Chinese summary for ``headline``.

    Returns ``_GENERIC_SUMMARY`` (with a "needs manual review" note) if
    no template matches, so we never hallucinate.
    """
    text = headline or ""
    sym_str = _fmt_symbols(symbols)
    for pattern, summary_t, _impact_t in _TEMPLATES:
        if pattern.search(text):
            return summary_t.format(symbols=sym_str)
    return _GENERIC_SUMMARY


def infer_impact_zh(
    headline: str,
    severity: str = "low",
    symbols: Iterable[str] | None = None,
) -> str:
    """Return a deterministic Chinese impact note.

    The note is purely suggestive - the actual risk posture lives in
    ``PreOpenReport.bot_instruction``.
    """
    text = headline or ""
    sym_str = _fmt_symbols(symbols)
    for pattern, _summary_t, impact_t in _TEMPLATES:
        if pattern.search(text):
            return impact_t.format(symbols=sym_str)
    if severity == "high":
        return "高优先级：" + _GENERIC_IMPACT
    return _GENERIC_IMPACT


# ---------------------------------------------------------------------------
# News item enrichment
# ---------------------------------------------------------------------------
def _needs_manual_review(
    item: dict[str, Any],
    *,
    manual_review_symbols: set[str],
) -> bool:
    sev = str(item.get("severity", "low")).lower()
    cat = str(item.get("category", "")).lower()
    if sev in {"medium", "high"}:
        return True
    if cat in {"earnings", "analyst"}:
        return True
    for s in item.get("symbols") or []:
        if s in manual_review_symbols:
            return True
    return False


def build_news_items(
    report: "PreOpenReport",
    *,
    cfg: "AppConfig | None" = None,
) -> list[dict[str, Any]]:
    """Produce the ``news_items`` list (bilingual, with review flags)."""
    manual = set(report.manual_review_required or [])
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    # Iterate majors first so high-severity items surface at the top of
    # the list. We include earnings + analyst so the JSON is a single
    # unified feed callers can sort / filter on.
    for bucket in (report.major_news, report.earnings_news, report.analyst_ratings):
        for raw in bucket or []:
            headline = str(raw.get("headline") or "").strip()
            if not headline:
                continue
            symbols = [str(s).upper() for s in (raw.get("symbols") or []) if s]
            key = (headline.lower(), ",".join(symbols))
            if key in seen:
                continue
            seen.add(key)
            severity = str(raw.get("severity") or "low").lower()
            item = {
                "symbol": symbols[0] if symbols else "-",
                "symbols": symbols,
                "severity": severity,
                "category": raw.get("category") or "",
                "source": raw.get("source") or "",
                "headline_en": headline,
                "summary_zh": summarize_zh(headline, symbols),
                "market_impact_zh": infer_impact_zh(headline, severity, symbols),
                "manual_review_required": _needs_manual_review(
                    raw, manual_review_symbols=manual
                ),
            }
            out.append(item)

    out.sort(
        key=lambda it: (
            {"high": 0, "medium": 1, "low": 2}.get(str(it["severity"]), 3),
            str(it["category"] or ""),
        )
    )
    return out


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------
def _group_analyst_by_symbol(ratings: list[dict[str, Any]]) -> list[tuple[str, list[str]]]:
    """Group analyst ratings by symbol -> list of headlines."""
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for it in ratings or []:
        syms = [s for s in (it.get("symbols") or []) if s] or ["-"]
        for sym in syms:
            if sym not in groups:
                order.append(sym)
                groups[sym] = []
            headline = str(it.get("headline") or "").strip()
            if headline:
                groups[sym].append(headline)
    return [(sym, groups[sym]) for sym in order]


# ---------------------------------------------------------------------------
# Title / timestamp helpers
# ---------------------------------------------------------------------------
def is_pre_open(now: datetime | None = None) -> bool:
    """Return True when ``now`` (America/New_York) is before 09:30 ET."""
    n = now or datetime.now(NY_TZ)
    if n.tzinfo is None:
        n = n.replace(tzinfo=NY_TZ)
    n_ny = n.astimezone(NY_TZ)
    hh, mm = MARKET_OPEN_HHMM
    return (n_ny.hour, n_ny.minute) < (hh, mm)


def report_title_zh(report: "PreOpenReport", *, now: datetime | None = None) -> str:
    """Return the Chinese report title (pre-open vs intraday)."""
    n = now or datetime.now(NY_TZ)
    if n.tzinfo is None:
        n = n.replace(tzinfo=NY_TZ)
    n_ny = n.astimezone(NY_TZ)
    if is_pre_open(n_ny):
        return f"【盘前重大市场新闻报告】{report.date}"
    return f"【即时重大市场新闻报告】{report.date} {n_ny.strftime('%H:%M')}"


# ---------------------------------------------------------------------------
# Full Chinese report rendering
# ---------------------------------------------------------------------------
def render_full_chinese_report(
    report: "PreOpenReport",
    *,
    cfg: "AppConfig | None" = None,
    now: datetime | None = None,
    include_title: bool = True,
) -> str:
    """Render the seven-section Chinese Telegram / markdown briefing."""
    rc = news_report_config(cfg)
    lines: list[str] = []

    if include_title:
        lines.append(report_title_zh(report, now=now))
        lines.append("")

    # --- Section 1: 市场机制判断
    md = report.market_data or {}
    missing = md.get("missing_fields") or []
    confidence_zh = {
        "high": "高置信度",
        "medium": "中等置信度",
        "low": "低置信度",
    }.get(str(report.regime_confidence).lower(), report.regime_confidence or "-")
    lines.append("一、市场机制判断")
    lines.append(
        f"- 市场状态：{report.market_regime}（{confidence_zh}）"
    )
    lines.append(
        f"- 是否允许新开仓：{'是' if report.new_positions_allowed else '否'}"
    )
    lines.append(
        f"- 是否允许研究扫描：{'是' if report.regime_research_scans_allowed else '否'}"
    )
    if missing:
        lines.append("- 缺失数据：" + "、".join(missing))
    else:
        lines.append("- 缺失数据：无")
    if missing and "VIX" in missing:
        spy = md.get("spy_above_200ma")
        qqq = md.get("qqq_above_200ma")
        if spy is not None or qqq is not None:
            lines.append(
                "- 说明：VIX/VIX3M 当前不可用，系统使用 SPY/QQQ 200MA 趋势作为 fallback。"
                "研究扫描允许，但交易执行仍然关闭。"
            )
        else:
            lines.append(
                "- 说明：VIX/VIX3M 当前不可用，且 SPY/QQQ 趋势数据不足；"
                "研究扫描允许，但交易执行仍然关闭。"
            )
    if report.regime_reason:
        lines.append(f"- 机制原因：{report.regime_reason}")

    # Data availability (distinguish IBKR vs external - requirement 6).
    lines.append(
        f"- IBKR 新闻数据：{'可用' if report.ibkr_news_available else '不可用'}"
    )
    lines.append(
        f"- 外部研究数据：{'可用' if report.external_research_available else '未启用 / 不可用'}"
    )
    if report.ibkr_news_available and not report.external_research_available:
        lines.append(
            "- 当前报告基于 IBKR headlines，不代表完整外部新闻覆盖。"
        )

    # --- Section 2: 今日重点新闻摘要
    items = build_news_items(report, cfg=cfg)
    majors = [it for it in items if str(it.get("category", "")).lower() != "analyst"]
    max_major = int(rc.get("max_major_news_items", 20) or 20)
    include_en = bool(rc.get("include_english_headline", True))
    include_zh = bool(rc.get("include_chinese_summary", True))

    lines.append("")
    lines.append("二、今日重点新闻摘要")
    if not majors:
        lines.append("- 暂无重点新闻（IBKR headlines 为空或被过滤为低严重度噪音）。")
    else:
        for idx, it in enumerate(majors[:max_major], 1):
            symbol = it.get("symbol") or "-"
            severity = it.get("severity") or "-"
            lines.append(f"{idx}. {symbol} — {severity}")
            if include_en:
                lines.append(f"  英文标题：{it.get('headline_en', '')}")
            if include_zh:
                lines.append(f"  中文摘要：{it.get('summary_zh', '')}")
            lines.append(f"  潜在影响：{it.get('market_impact_zh', '')}")
            lines.append(
                "  处理：" + (
                    "加入人工观察，不自动交易。"
                    if it.get("manual_review_required")
                    else "仅记录，不自动交易。"
                )
            )
        if len(majors) > max_major:
            lines.append(f"… 另有 {len(majors) - max_major} 条，请查看完整 JSON 报告。")

    # --- Section 3: 财报 / 业绩相关新闻
    lines.append("")
    lines.append("三、财报 / 业绩相关新闻")
    max_earnings = int(rc.get("max_earnings_items", 10) or 10)
    earnings = report.earnings_news or []
    if not earnings:
        lines.append("- 暂无财报相关新闻。")
    else:
        for it in earnings[:max_earnings]:
            syms = _fmt_symbols(it.get("symbols"))
            headline = it.get("headline") or ""
            sev = it.get("severity") or "-"
            lines.append(f"- {syms} ({sev}): {headline}")
            if include_zh:
                lines.append(
                    f"  中文摘要：{summarize_zh(headline, it.get('symbols'))}"
                )
        if len(earnings) > max_earnings:
            lines.append(
                f"… 另有 {len(earnings) - max_earnings} 条财报新闻，请查看完整 JSON 报告。"
            )

    # --- Section 4: 分析师评级 / 目标价更新
    lines.append("")
    lines.append("四、分析师评级 / 目标价更新")
    max_analyst = int(rc.get("max_analyst_rating_items", 20) or 20)
    analyst = report.analyst_ratings or []
    if not analyst:
        lines.append("- 暂无分析师评级更新。")
    else:
        grouped = _group_analyst_by_symbol(analyst)
        shown = 0
        for sym, headlines in grouped:
            if shown >= max_analyst:
                break
            take = min(len(headlines), max_analyst - shown)
            lines.append(f"- {sym}:")
            for h in headlines[:take]:
                lines.append(f"    · {h}")
            shown += take
        if len(analyst) > shown:
            lines.append(
                f"… 另有 {len(analyst) - shown} 条评级更新，请查看完整 JSON 报告。"
            )

    # --- Section 5: 需要人工复核的股票
    lines.append("")
    lines.append("五、需要人工复核的股票")
    manual = sorted({s for s in (report.manual_review_required or []) if s})
    # Augment with any per-item review flags so the section always
    # matches the item-level recommendations in section 2.
    for it in items:
        if it.get("manual_review_required"):
            for s in it.get("symbols") or []:
                if s:
                    manual.append(s)
    manual = sorted(set(manual))
    if not manual:
        lines.append("- 暂无需要人工复核的股票。")
    else:
        lines.append("- " + "、".join(manual))

    # --- Section 6: 被阻止 / 不应交易的股票
    lines.append("")
    lines.append("六、被阻止 / 不应交易的股票")
    if not report.blocked_symbols:
        lines.append("- 暂无强制阻止股票。")
    else:
        lines.append("- " + "、".join(sorted(set(report.blocked_symbols))))

    # --- Section 7: Bot 指令
    lines.append("")
    lines.append("七、Bot 指令")
    lines.append("- 研究数据不完整时，不允许新开仓。")
    lines.append("- 当前仅允许研究扫描和人工复核。")
    lines.append("- 不自动下单。")
    lines.append("- execution_allowed=false；research_only=true。")
    if report.bot_instruction:
        lines.append(f"- 系统说明：{report.bot_instruction}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram splitting
# ---------------------------------------------------------------------------
def split_for_telegram(
    text: str,
    *,
    limit: int = _DEFAULT_TELEGRAM_LIMIT,
    header: str | None = None,
) -> list[str]:
    """Split ``text`` into ``Part i / N`` chunks <= ``limit`` chars.

    We split on newlines so we never break a section mid-sentence. If a
    single line is already longer than ``limit`` it is hard-sliced into
    multiple parts (never silently truncated). The first part keeps
    ``header`` (e.g. the Chinese title) verbatim followed by a
    ``(Part i/N)`` marker; subsequent parts start with the marker only.

    The ``limit`` applies to the **final** emitted message including
    the header + ``(Part i/N)`` prefix, so callers can pass the
    Telegram API cap directly and trust we'll stay under it.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    limit = max(200, limit)  # sane floor

    body = text if text is not None else ""
    header_str = header or ""

    # Quick path: body + optional header fits in one message without
    # any Part i/N marker.
    single = _prepend(header_str, body) if header_str else body
    if len(single) <= limit:
        return [single]

    # Otherwise: reserve room for the header (first part only) and for
    # the ``(Part i/N)`` marker on every part. We do a two-pass
    # calculation: first estimate N with a conservative marker width,
    # then re-split if the final N differs.
    def _effective_limit(part_idx: int, total: int, *, worst: bool = False) -> int:
        marker = f"(Part {part_idx}/{total})\n" if not worst else f"(Part 99/99)\n"
        overhead = len(marker)
        if part_idx == 1 and header_str:
            overhead += len(header_str) + 1  # +1 for the newline
        return max(50, limit - overhead)

    # Iterate until N stabilises (one or two rounds in practice).
    estimate = 2
    while True:
        chunks = _greedy_chunks(
            body,
            per_chunk_limit=lambda i, n=estimate: _effective_limit(i, n),
        )
        if len(chunks) == estimate:
            break
        estimate = len(chunks)
        if estimate > 200:  # pathological safety
            break

    total = len(chunks)
    out: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        marker = f"(Part {i}/{total})"
        if i == 1 and header_str:
            out.append(f"{header_str}\n{marker}\n{chunk}")
        else:
            out.append(f"{marker}\n{chunk}")
    return out


def _greedy_chunks(
    body: str,
    *,
    per_chunk_limit,
) -> list[str]:
    """Line-based greedy splitter.

    ``per_chunk_limit`` is called with the 1-based chunk index and
    returns the maximum number of characters allowed for that chunk
    (excluding the Part marker we'll add later).
    """
    chunks: list[str] = []
    current = ""
    idx = 1
    limit = per_chunk_limit(idx)
    for raw_line in body.splitlines():
        line = raw_line
        # Hard-slice individually long lines.
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
                idx += 1
                limit = per_chunk_limit(idx)
            chunks.append(line[:limit])
            line = line[limit:]
            idx += 1
            limit = per_chunk_limit(idx)
        candidate = line if not current else current + "\n" + line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
            idx += 1
            limit = per_chunk_limit(idx)
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _prepend(header: str, body: str) -> str:
    if not header:
        return body
    return f"{header}\n{body}" if body else header


__all__ = [
    "build_news_items",
    "infer_impact_zh",
    "is_pre_open",
    "news_report_config",
    "render_full_chinese_report",
    "report_title_zh",
    "split_for_telegram",
    "summarize_zh",
    "telegram_language",
]
