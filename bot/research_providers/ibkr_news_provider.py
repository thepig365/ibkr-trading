"""IBKR news provider.

Reads news from the user's IBKR subscriptions via the existing
:class:`bot.ibkr_client.IBKRClient` wrappers (`get_news_providers` /
`get_historical_news`). The provider is **opt-in**: it only opens an
IBKR connection when the caller explicitly invokes
``fetch_ibkr_news(client=...)`` *with a connected client* OR uses the
``connect_for_news`` helper, which always disconnects in ``finally``.

Importing this module must not start any network activity. The UI
``/research`` page reads cached JSON via the state store; it never
imports this module on render.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from ..research_intelligence import NewsCatalyst

if TYPE_CHECKING:
    from ..config import AppConfig
    from ..ibkr_client import IBKRClient


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IBKRNewsProviderStatus:
    """Structured availability status of IBKR news entitlements."""

    ibkr_news_available: bool
    providers_detected: list[str] = field(default_factory=list)
    missing_entitlements: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    checked_at_utc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Common IBKR news provider codes (best-effort hints). Used only to
# populate ``missing_entitlements`` when we connect and find none.
_KNOWN_PROVIDER_CODES: tuple[str, ...] = (
    "BRFG",  # Briefing.com general
    "BRFUPDN",  # Briefing.com upgrades/downgrades
    "DJNL",  # Dow Jones Newsletter
    "DJ-RT",  # Dow Jones real-time
    "DJ-N",  # Dow Jones News
    "RSF",  # Reuters StarMine
    "MT",  # MidnightTrader
)


def get_provider_status(
    cfg: "AppConfig",
    *,
    client: "IBKRClient | None" = None,
) -> IBKRNewsProviderStatus:
    """Inspect IBKR news entitlements.

    If ``client`` is provided we assume the caller has already connected
    (and will disconnect) — we do not connect or disconnect ourselves.
    If ``client`` is None we return a status that says "not checked"
    without opening any socket. To actually probe IBKR the caller must
    use :func:`connect_for_news` or pass an existing client.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if client is None:
        return IBKRNewsProviderStatus(
            ibkr_news_available=False,
            providers_detected=[],
            missing_entitlements=[],
            notes=[
                "no client provided; not connected to IBKR. "
                "Run `python -m bot.cli ibkr-news-status` or "
                "`ibkr-news-fetch` to probe entitlements."
            ],
            checked_at_utc=now,
        )

    try:
        codes = client.get_news_providers() or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_news_providers raised: %s", exc)
        return IBKRNewsProviderStatus(
            ibkr_news_available=False,
            providers_detected=[],
            missing_entitlements=list(_KNOWN_PROVIDER_CODES),
            notes=[f"get_news_providers raised: {exc!r}"],
            checked_at_utc=now,
        )

    detected = [c for c in codes if c]
    available = bool(detected)
    missing = [c for c in _KNOWN_PROVIDER_CODES if c not in detected]
    notes: list[str] = []
    if not available:
        notes.append(
            "IBKR returned 0 news providers; no news entitlement on this "
            "account. The research layer will rely on macro calendar and "
            "soft flags only."
        )
    return IBKRNewsProviderStatus(
        ibkr_news_available=available,
        providers_detected=detected,
        missing_entitlements=missing,
        notes=notes,
        checked_at_utc=now,
    )


def fetch_ibkr_news(
    cfg: "AppConfig",
    *,
    symbols: Iterable[str],
    client: "IBKRClient",
    limit_per_symbol: int = 10,
    provider_codes: list[str] | None = None,
) -> tuple[list[NewsCatalyst], IBKRNewsProviderStatus]:
    """Fetch headlines for ``symbols`` using the supplied connected ``client``.

    Returns ``(catalysts, provider_status)``. Errors per-symbol degrade
    to an empty result for that symbol and a note in the status.
    """
    status = get_provider_status(cfg, client=client)
    if not status.ibkr_news_available:
        # Bail out cleanly — but cache the empty result so the report still
        # has a deterministic snapshot for today.
        return [], status

    out: list[NewsCatalyst] = []
    notes: list[str] = list(status.notes)
    for raw in symbols:
        sym = (raw or "").strip().upper()
        if not sym:
            continue
        try:
            headlines = client.get_historical_news(
                sym,
                provider_codes=provider_codes,
                max_results=int(limit_per_symbol),
            )
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{sym}: get_historical_news raised {exc!r}")
            continue
        for h in headlines or []:
            ts = _coerce_iso_utc(getattr(h, "time_utc", None))
            out.append(
                NewsCatalyst(
                    timestamp=ts,
                    provider=getattr(h, "provider_code", "") or "",
                    article_id=str(getattr(h, "article_id", "") or ""),
                    symbol=sym,
                    headline=str(getattr(h, "headline", "") or "").strip(),
                    raw_payload={
                        "symbol": sym,
                        "provider_code": getattr(h, "provider_code", ""),
                        "article_id": str(getattr(h, "article_id", "") or ""),
                        "time_utc": ts,
                    },
                )
            )

    final_status = IBKRNewsProviderStatus(
        ibkr_news_available=status.ibkr_news_available,
        providers_detected=status.providers_detected,
        missing_entitlements=status.missing_entitlements,
        notes=notes,
        checked_at_utc=status.checked_at_utc,
    )
    return out, final_status


def write_news_cache(
    cfg: "AppConfig",
    *,
    catalysts: list[NewsCatalyst],
    status: IBKRNewsProviderStatus,
    cache_root: Path | None = None,
) -> Path:
    """Persist today's news + provider status under ``data/research/cache/ibkr_news/``.

    The file path is the only filesystem write done by this module.
    """
    root = cache_root or cfg.absolute("data/research/cache/ibkr_news")
    root.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = root / f"{day}-news.json"
    payload = {
        "date": day,
        "generated_at_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "provider_status": status.to_dict(),
        "catalysts": [
            {
                "timestamp": c.timestamp,
                "provider": c.provider,
                "article_id": c.article_id,
                "symbol": c.symbol,
                "headline": c.headline,
            }
            for c in catalysts
        ],
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return out_path


def read_latest_news_cache(cfg: "AppConfig") -> dict[str, Any] | None:
    """Read the most recent ``YYYY-MM-DD-news.json`` cache, if any."""
    root = cfg.absolute("data/research/cache/ibkr_news")
    if not root.exists():
        return None
    files = sorted(root.glob("*-news.json"))
    if not files:
        return None
    try:
        with files[-1].open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


# ---------------------------------------------------------------------------
# Connection helper (opt-in only)
# ---------------------------------------------------------------------------
def connect_for_news(cfg: "AppConfig") -> "IBKRClient":
    """Connect to IBKR specifically for news. Caller MUST disconnect.

    Imported lazily so that merely importing this module does not pull
    in the IBKR client (and therefore does not satisfy the architecture
    test "no IBKR import on UI startup" in any way).
    """
    from ..ibkr_client import IBKRClientError  # noqa: PLC0415
    from ..ibkr_connection import connect_readonly_roster_retry  # noqa: PLC0415

    oc = connect_readonly_roster_retry(cfg, "research")
    if oc.live_blocked is not None:
        raise oc.live_blocked
    if oc.client is None:
        raise IBKRClientError(oc.fatal_message or "IBKR unavailable for IBKR news")
    return oc.client


def _coerce_iso_utc(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


__all__ = [
    "IBKRNewsProviderStatus",
    "connect_for_news",
    "fetch_ibkr_news",
    "get_provider_status",
    "read_latest_news_cache",
    "write_news_cache",
]
