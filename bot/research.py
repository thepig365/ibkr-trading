"""External research adapter (Perplexity).

The bot uses Perplexity's chat-completions API to produce a structured
pre-open research bundle. The design follows the same fail-safe
contract as the Telegram adapter:

* Missing ``PERPLEXITY_API_KEY`` never crashes; the client simply
  reports ``is_configured == False`` and the orchestrator marks
  external research as unavailable.
* Network errors, HTTP 4xx/5xx, and malformed JSON all return ``None``
  with a logged warning. Trading posture is then hardened (see
  ``news_report.py``).
* LLM output is treated as untrusted input: we never act on it
  without additional deterministic checks. The news report may only
  tighten risk posture, never relax it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PERPLEXITY_ENDPOINT = "https://api.perplexity.ai/chat/completions"

# The JSON schema we ask Perplexity to follow. We do not rely on the
# model honouring this exactly - ``_normalise_payload`` defensively
# coerces the response into the schema we persist to disk.
RESEARCH_JSON_HINT = {
    "major_news": [
        {
            "headline": "string",
            "source": "string",
            "symbols": ["string"],
            "asset_classes": ["string"],
            "impact": "positive|negative|mixed|unknown",
            "severity": "low|medium|high",
            "confidence": "low|medium|high",
            "summary": "string",
        }
    ],
    "macro_events": [
        {
            "event": "string",
            "time_new_york": "HH:MM",
            "severity": "low|medium|high",
            "market_relevance": "string",
        }
    ],
    "holdings_risk": [
        {
            "symbol": "string",
            "summary": "string",
            "severity": "low|medium|high",
            "recommendation": "string",
        }
    ],
    "watchlist_catalysts": [
        {
            "symbol": "string",
            "catalyst": "string",
            "severity": "low|medium|high",
            "summary": "string",
        }
    ],
}


@dataclass
class ResearchResult:
    """Outcome of a research call.

    ``available`` is ``False`` whenever we don't have a usable bundle
    (missing key, network error, JSON parse failure). ``payload`` is
    guaranteed to be schema-shaped even on partial failures.
    """

    available: bool
    payload: dict[str, list[dict[str, Any]]]
    error: str | None = None
    raw_text: str | None = None

    @classmethod
    def empty(cls, error: str | None = None) -> "ResearchResult":
        return cls(
            available=False,
            payload={
                "major_news": [],
                "macro_events": [],
                "holdings_risk": [],
                "watchlist_catalysts": [],
            },
            error=error,
        )


@dataclass
class ResearchRequest:
    today_iso: str
    indices_and_etfs: list[str]
    mega_cap_watchlist: list[str]
    extra_watchlist: list[str] = field(default_factory=list)
    holdings_symbols: list[str] = field(default_factory=list)
    macro_topics: list[str] = field(default_factory=list)


def build_prompt(req: ResearchRequest) -> str:
    """Build the user prompt Perplexity receives.

    The prompt asks for JSON only and enumerates the symbols / macro
    topics from the config so the response is deterministic in shape.
    """
    watchlist = sorted(set(req.indices_and_etfs + req.mega_cap_watchlist + req.extra_watchlist))
    macro = "\n".join(f"- {t}" for t in req.macro_topics)

    schema_hint = json.dumps(RESEARCH_JSON_HINT, indent=2)

    lines = [
        "You are a financial-news research assistant for a paper trading bot.",
        f"Today's date (US/Eastern): {req.today_iso}.",
        "Summarise US pre-open market context. Focus on news published in the last 24 hours.",
        "",
        "Required coverage:",
        macro,
        "",
        "Index / ETF universe:",
        ", ".join(req.indices_and_etfs) or "(none)",
        "",
        "Mega-cap / AI-infrastructure watchlist:",
        ", ".join(sorted(set(req.mega_cap_watchlist + req.extra_watchlist))) or "(none)",
        "",
        "Current holdings to analyse individually (if any):",
        ", ".join(req.holdings_symbols) or "(none)",
        "",
        "Reply with a single JSON object (no prose, no code fences) matching this shape:",
        schema_hint,
        "",
        "Rules:",
        "* Use only the enum values shown for severity/impact/confidence.",
        "* 'major_news' must contain the five to fifteen most market-moving stories.",
        "* 'macro_events' times MUST be in 24h HH:MM New York time.",
        "* 'holdings_risk' must be keyed on the holdings list above; leave empty if none.",
        "* If you are unsure, set confidence to 'low' rather than inventing details.",
        "* Never include personal account numbers, API keys, or internal identifiers.",
    ]
    return "\n".join(lines)


class PerplexityClient:
    """Minimal chat-completions wrapper with strict failure isolation."""

    def __init__(
        self,
        api_key: str | None,
        model: str = "sonar",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def research(self, req: ResearchRequest) -> ResearchResult:
        if not self.is_configured:
            return ResearchResult.empty(error="PERPLEXITY_API_KEY not set")

        prompt = build_prompt(req)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a precise financial news summariser. "
                        "You respond with JSON only, matching the schema in the user message."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            # Many Perplexity models support json_object response_format;
            # for ones that don't, normalisation below still copes.
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }

        try:
            resp = httpx.post(
                PERPLEXITY_ENDPOINT,
                headers=headers,
                json=body,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Perplexity request failed: %s", exc)
            return ResearchResult.empty(error=f"network error: {exc!r}")

        if resp.status_code != 200:
            logger.warning(
                "Perplexity returned HTTP %s: %s", resp.status_code, resp.text[:200]
            )
            return ResearchResult.empty(
                error=f"HTTP {resp.status_code}: {resp.text[:200]}"
            )

        try:
            body_json = resp.json()
            content = body_json["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Perplexity response missing content: %s", exc)
            return ResearchResult.empty(error=f"malformed envelope: {exc!r}")

        parsed = _extract_json(content)
        if parsed is None:
            logger.warning("Perplexity content was not valid JSON: %r", content[:200])
            result = ResearchResult.empty(error="non-json content")
            result.raw_text = content
            return result

        normalised = _normalise_payload(parsed)
        return ResearchResult(available=True, payload=normalised, raw_text=content)


# ---------------------------------------------------------------------------
# Defensive parsers
# ---------------------------------------------------------------------------
def _extract_json(text: str) -> dict | None:
    """Parse JSON, tolerating a leading/trailing code fence."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip ```json ... ```
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        # Last resort: try to find the outermost {...} block.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            obj = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


_ALLOWED_SEVERITY = {"low", "medium", "high"}
_ALLOWED_IMPACT = {"positive", "negative", "mixed", "unknown"}
_ALLOWED_CONFIDENCE = {"low", "medium", "high"}


def _coerce_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return str(v)


def _coerce_list_str(v: Any) -> list[str]:
    if isinstance(v, list):
        return [_coerce_str(x) for x in v if x is not None]
    if v is None or v == "":
        return []
    return [_coerce_str(v)]


def _coerce_enum(v: Any, allowed: set[str], default: str) -> str:
    s = _coerce_str(v).strip().lower()
    return s if s in allowed else default


def _normalise_major_news(items: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append(
            {
                "headline": _coerce_str(it.get("headline")),
                "source": _coerce_str(it.get("source")),
                "symbols": [s.upper() for s in _coerce_list_str(it.get("symbols"))],
                "asset_classes": _coerce_list_str(it.get("asset_classes")),
                "impact": _coerce_enum(it.get("impact"), _ALLOWED_IMPACT, "unknown"),
                "severity": _coerce_enum(it.get("severity"), _ALLOWED_SEVERITY, "low"),
                "confidence": _coerce_enum(
                    it.get("confidence"), _ALLOWED_CONFIDENCE, "low"
                ),
                "summary": _coerce_str(it.get("summary")),
            }
        )
    return out


def _normalise_macro_events(items: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append(
            {
                "event": _coerce_str(it.get("event")),
                "time_new_york": _coerce_str(it.get("time_new_york")).strip(),
                "severity": _coerce_enum(
                    it.get("severity"), _ALLOWED_SEVERITY, "low"
                ),
                "market_relevance": _coerce_str(it.get("market_relevance")),
            }
        )
    return out


def _normalise_holdings_risk(items: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append(
            {
                "symbol": _coerce_str(it.get("symbol")).upper(),
                "summary": _coerce_str(it.get("summary")),
                "severity": _coerce_enum(
                    it.get("severity"), _ALLOWED_SEVERITY, "low"
                ),
                "recommendation": _coerce_str(it.get("recommendation")),
            }
        )
    return out


def _normalise_watchlist_catalysts(items: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append(
            {
                "symbol": _coerce_str(it.get("symbol")).upper(),
                "catalyst": _coerce_str(it.get("catalyst")),
                "severity": _coerce_enum(
                    it.get("severity"), _ALLOWED_SEVERITY, "low"
                ),
                "summary": _coerce_str(it.get("summary")),
            }
        )
    return out


def _normalise_payload(raw: dict) -> dict[str, list[dict[str, Any]]]:
    return {
        "major_news": _normalise_major_news(raw.get("major_news")),
        "macro_events": _normalise_macro_events(raw.get("macro_events")),
        "holdings_risk": _normalise_holdings_risk(raw.get("holdings_risk")),
        "watchlist_catalysts": _normalise_watchlist_catalysts(
            raw.get("watchlist_catalysts")
        ),
    }


__all__ = [
    "PerplexityClient",
    "ResearchRequest",
    "ResearchResult",
    "build_prompt",
]
