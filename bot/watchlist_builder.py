"""Daily dynamic research watchlist builder.

The builder merges four sources into a single, deduplicated list of
symbols that the SMC research pipeline can later scan:

1. a static "core" list of mega-cap / index names taken from
   ``config/watchlist.yaml``;
2. names ranked by **current-day dollar volume** (when available);
3. names ranked by **20-day average dollar volume**;
4. names ranked by **high volatility proxy** (ATR% or 20-day
   realised volatility) when beta is not available.

Everything is research-only. This module:

* never imports :mod:`bot.broker`;
* never calls ``Broker.place_order``;
* never flips ``execution_allowed`` — the persisted JSON always
  carries ``execution_allowed: false`` and ``research_only: true``;
* tolerates every missing data point (no current volume, no beta,
  symbol lookup failure) by marking them on the row and carrying on.

Configuration is read from the ``watchlist.dynamic`` block in
``config/watchlist.yaml`` (see :data:`DEFAULT_DYNAMIC_CFG` for every
knob). Candidates are chosen per category first, then merged and
deduped, then the liquidity filters run, and finally the remaining
rows are clipped to ``max_symbols`` by
``volume_rank_score`` descending so the worst names fall off first.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .config import AppConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------
DEFAULT_STATIC_CORE: tuple[str, ...] = (
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META",
    "TSLA", "AVGO", "AMD", "PLTR", "SMCI", "ARM", "MU", "TSM",
    "ORCL", "CRWV",
)

DEFAULT_DYNAMIC_CFG: dict[str, Any] = {
    "enabled": True,
    "max_symbols": 50,
    "min_price": 10.0,
    "min_avg_20d_dollar_volume": 20_000_000.0,
    "min_current_dollar_volume": 10_000_000.0,
    "include_static_core": True,
    "include_high_volume": True,
    "include_high_relative_volume": True,
    "include_high_beta_or_volatility": True,
    "min_relative_volume_for_relvol_bucket": 1.5,
    "strong_relative_volume": 2.0,
    "top_current_dollar_volume_count": 30,
    "top_relative_volume_count": 30,
    "top_avg_dollar_volume_count": 30,
    "top_high_volatility_count": 30,
    "high_beta_threshold": 1.5,
    "high_atr_pct_threshold": 4.0,
    "high_realized_vol_20d_threshold": 50.0,
    "exclude_leveraged_etfs": True,
    "exclude_otc": True,
    "exclude_blocked_symbols": True,
}

# Symbols we never want in the research universe even if they clear
# liquidity filters. Leveraged / inverse ETFs mostly; extend via
# config ``leveraged_etf_blocklist`` if needed.
DEFAULT_LEVERAGED_ETF_BLOCKLIST: frozenset[str] = frozenset({
    "TQQQ", "SQQQ", "UPRO", "SPXU", "SPXL", "SPXS",
    "SOXL", "SOXS", "UVXY", "SVXY", "TMF", "TMV",
    "TNA", "TZA", "UDOW", "SDOW", "BOIL", "KOLD",
    "LABU", "LABD", "JNUG", "JDST", "NUGT", "DUST",
    "YINN", "YANG", "WEBL", "WEBS", "FAS", "FAZ",
    "DPST", "DRN", "DRV", "TSLL", "TSLQ", "NVDL",
    "NVDS", "BITX", "BITI",
})


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------
@dataclass
class WatchlistCandidate:
    """One row in the dynamic watchlist."""

    symbol: str
    reason: list[str] = field(default_factory=list)
    latest_price: float | None = None
    current_volume: float | None = None
    current_dollar_volume: float | None = None
    avg_20d_volume: float | None = None
    avg_20d_dollar_volume: float | None = None
    relative_volume: float | None = None
    volume_activity: str = "unknown"  # strong/elevated/normal/unknown
    volume_rank_score: float | None = None
    beta: float | None = None
    atr_pct: float | None = None
    realized_vol_20d: float | None = None
    blocked: bool = False
    block_reason: str | None = None
    missing_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "reason": sorted(set(self.reason)),
            "latest_price": self.latest_price,
            "current_volume": self.current_volume,
            "current_dollar_volume": self.current_dollar_volume,
            "avg_20d_volume": self.avg_20d_volume,
            "avg_20d_dollar_volume": self.avg_20d_dollar_volume,
            "relative_volume": self.relative_volume,
            "volume_activity": self.volume_activity,
            "volume_rank_score": self.volume_rank_score,
            "beta": self.beta,
            "atr_pct": self.atr_pct,
            "realized_vol_20d": self.realized_vol_20d,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "missing_fields": sorted(set(self.missing_fields)),
        }


@dataclass
class DynamicWatchlist:
    """Full builder output, ready to serialise."""

    date: str
    source: str
    symbols: list[WatchlistCandidate] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "source": self.source,
            "symbols": [c.to_dict() for c in self.symbols],
            "missing_data": sorted(set(self.missing_data)),
            "research_only": True,
            "execution_allowed": False,
        }


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------
def _rolling_avg(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / float(window)


def _atr_pct_from_bars(bars: Sequence[dict[str, Any]], window: int = 20) -> float | None:
    """Classic Wilder ATR% using ``(H-L, |H-Cprev|, |L-Cprev|)``.

    Returns ``None`` when there are fewer than ``window + 1`` usable
    bars. The value is expressed as a percentage of the latest close
    so operators can reason about it without knowing the symbol's
    price level.
    """
    if len(bars) < window + 1:
        return None
    trs: list[float] = []
    prev_close = float(bars[0].get("close") or 0.0)
    for b in bars[1:]:
        high = float(b.get("high") or 0.0)
        low = float(b.get("low") or 0.0)
        close = float(b.get("close") or 0.0)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        prev_close = close
    if len(trs) < window:
        return None
    atr = sum(trs[-window:]) / float(window)
    latest_close = float(bars[-1].get("close") or 0.0)
    if latest_close <= 0:
        return None
    return round(atr / latest_close * 100.0, 4)


def _realized_vol_20d(bars: Sequence[dict[str, Any]]) -> float | None:
    """Annualised realised vol (%) over the trailing 20 bars."""
    if len(bars) < 21:
        return None
    closes = [float(b.get("close") or 0.0) for b in bars]
    returns: list[float] = []
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        if prev <= 0 or cur <= 0:
            continue
        returns.append(math.log(cur / prev))
    if len(returns) < 20:
        return None
    window_returns = returns[-20:]
    if len(window_returns) < 2:
        return None
    stdev = statistics.stdev(window_returns)
    # 252 trading days.
    return round(stdev * math.sqrt(252.0) * 100.0, 4)


def _avg_20d_volume(bars: Sequence[dict[str, Any]]) -> float | None:
    vols = [float(b.get("volume") or 0.0) for b in bars[-20:]]
    if len(vols) < 20:
        return None
    return sum(vols) / float(len(vols))


def _latest(bars: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    return bars[-1] if bars else None


def build_candidate_from_bars(
    symbol: str,
    bars: Sequence[dict[str, Any]],
    *,
    current_volume: float | None = None,
    beta: float | None = None,
) -> WatchlistCandidate:
    """Compute all metrics we can from a single symbol's daily bars.

    The ``bars`` sequence is expected to be oldest-first. ``None`` is
    returned on any metric that cannot be computed from the available
    data; the ``missing_fields`` list records the reason so downstream
    code can render a clean table.
    """
    candidate = WatchlistCandidate(symbol=symbol.upper())
    if not bars:
        candidate.missing_fields.append("bars")
        return candidate

    latest = _latest(bars) or {}
    latest_close = float(latest.get("close") or 0.0) or None
    candidate.latest_price = latest_close

    avg_vol = _avg_20d_volume(bars)
    candidate.avg_20d_volume = avg_vol
    if avg_vol is not None and latest_close is not None:
        candidate.avg_20d_dollar_volume = round(avg_vol * latest_close, 2)
    else:
        candidate.missing_fields.append("avg_20d_dollar_volume")

    latest_bar_volume = float(latest.get("volume") or 0.0)
    # The builder accepts an override for ``current_volume`` because
    # IBKR's daily bar volume lags intraday; if the caller has live
    # tick snapshot, they pass it explicitly. Otherwise we fall back
    # to the latest bar's recorded volume (still useful as a rough
    # relative-volume proxy).
    cur_vol = current_volume if current_volume is not None else (
        latest_bar_volume or None
    )
    candidate.current_volume = cur_vol
    if cur_vol is None:
        candidate.missing_fields.append("current_volume")
    if cur_vol is not None and latest_close is not None:
        candidate.current_dollar_volume = round(cur_vol * latest_close, 2)

    if avg_vol and cur_vol and avg_vol > 0:
        candidate.relative_volume = round(cur_vol / avg_vol, 4)
    else:
        candidate.missing_fields.append("relative_volume")

    candidate.atr_pct = _atr_pct_from_bars(bars, 20)
    if candidate.atr_pct is None:
        candidate.missing_fields.append("atr_pct")
    candidate.realized_vol_20d = _realized_vol_20d(bars)
    if candidate.realized_vol_20d is None:
        candidate.missing_fields.append("realized_vol_20d")

    candidate.beta = beta
    if beta is None:
        candidate.missing_fields.append("beta")

    candidate.volume_activity = classify_relative_volume(
        candidate.relative_volume
    )
    return candidate


def classify_relative_volume(
    relative_volume: float | None,
    *,
    strong: float = 2.0,
    elevated: float = 1.5,
) -> str:
    if relative_volume is None:
        return "unknown"
    if relative_volume >= strong:
        return "strong_activity"
    if relative_volume >= elevated:
        return "elevated_activity"
    return "normal_activity"


def compute_volume_rank_score(
    candidate: WatchlistCandidate,
    *,
    max_cur_dv: float | None,
    max_avg_dv: float | None,
    max_rel_vol: float | None,
    max_vol_proxy: float | None,
) -> float | None:
    """Produce a ``[0, 1]`` score, or ``None`` if nothing usable.

    The weight mix degrades gracefully as inputs go missing. When
    current volume and relative volume are both missing we fall back
    to ``0.70 * avg20d + 0.30 * vol_proxy`` so a beta-free high-vol
    name still ranks.
    """
    def _norm(value: float | None, denom: float | None) -> float:
        if value is None or not denom or denom <= 0:
            return 0.0
        return max(0.0, min(1.0, value / denom))

    cur_dv = _norm(candidate.current_dollar_volume, max_cur_dv)
    avg_dv = _norm(candidate.avg_20d_dollar_volume, max_avg_dv)
    rel = _norm(candidate.relative_volume, max_rel_vol)
    vol_proxy = _norm(
        candidate.atr_pct or candidate.realized_vol_20d, max_vol_proxy
    )

    if candidate.current_dollar_volume is None and candidate.relative_volume is None:
        score = 0.70 * avg_dv + 0.30 * vol_proxy
    else:
        score = 0.45 * cur_dv + 0.35 * rel + 0.20 * avg_dv
    if score <= 0:
        return None
    return round(score, 6)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
@dataclass
class FilterConfig:
    """Subset of :class:`DEFAULT_DYNAMIC_CFG` the filters consume."""

    min_price: float = 10.0
    min_avg_20d_dollar_volume: float = 20_000_000.0
    min_current_dollar_volume: float = 10_000_000.0
    exclude_leveraged_etfs: bool = True
    exclude_otc: bool = True
    exclude_blocked_symbols: bool = True
    leveraged_etf_blocklist: frozenset[str] = DEFAULT_LEVERAGED_ETF_BLOCKLIST


def _apply_liquidity_filters(
    candidate: WatchlistCandidate,
    cfg: FilterConfig,
    blocked_symbols: set[str],
) -> WatchlistCandidate:
    """Mark the candidate as blocked if it fails any liquidity filter.

    Filters never mutate numeric fields — they only flip ``blocked``
    and fill ``block_reason``. Callers decide whether to drop blocked
    rows entirely or keep them for visibility.
    """
    sym = candidate.symbol.upper()
    if cfg.exclude_blocked_symbols and sym in blocked_symbols:
        candidate.blocked = True
        candidate.block_reason = "symbol_blocked_by_news_or_policy"
        return candidate

    if cfg.exclude_leveraged_etfs and sym in cfg.leveraged_etf_blocklist:
        candidate.blocked = True
        candidate.block_reason = "leveraged_etf_excluded"
        return candidate

    if cfg.exclude_otc and ("." in sym or sym.endswith("F")):
        # A weak OTC heuristic; IBKR exchange metadata gives us the
        # real answer but this is defensive so we never surface a
        # pink-sheet ticker even when exchange data is missing.
        if sym.endswith("F") and len(sym) == 5 and sym not in {"NVDA", "TSLA"}:
            candidate.blocked = True
            candidate.block_reason = "possible_otc_suffix"
            return candidate

    if candidate.latest_price is not None and candidate.latest_price < cfg.min_price:
        candidate.blocked = True
        candidate.block_reason = f"price<{cfg.min_price}"
        return candidate

    if (
        candidate.avg_20d_dollar_volume is not None
        and candidate.avg_20d_dollar_volume < cfg.min_avg_20d_dollar_volume
    ):
        candidate.blocked = True
        candidate.block_reason = (
            f"avg_20d_dollar_volume<${cfg.min_avg_20d_dollar_volume:,.0f}"
        )
        return candidate

    if (
        candidate.current_dollar_volume is not None
        and candidate.current_dollar_volume < cfg.min_current_dollar_volume
    ):
        candidate.blocked = True
        candidate.block_reason = (
            f"current_dollar_volume<${cfg.min_current_dollar_volume:,.0f}"
        )
        return candidate

    return candidate


# ---------------------------------------------------------------------------
# Bucket selection
# ---------------------------------------------------------------------------
def _top_by(
    rows: Iterable[WatchlistCandidate],
    key: Callable[[WatchlistCandidate], float | None],
    n: int,
) -> list[WatchlistCandidate]:
    pool = [r for r in rows if key(r) is not None]
    pool.sort(key=lambda r: key(r) or 0.0, reverse=True)
    return pool[:n]


def _merge_candidates(
    *groups: tuple[str, Iterable[WatchlistCandidate]],
) -> list[WatchlistCandidate]:
    """Merge per-bucket candidate lists, deduplicating by symbol.

    The first appearance of a symbol owns the canonical dataclass;
    the merger only pushes reason tags from subsequent appearances
    so we don't lose bucket membership information.
    """
    index: dict[str, WatchlistCandidate] = {}
    for tag, group in groups:
        for row in group:
            sym = row.symbol.upper()
            existing = index.get(sym)
            if existing is None:
                row.reason.append(tag)
                index[sym] = row
            else:
                if tag not in existing.reason:
                    existing.reason.append(tag)
    return list(index.values())


# ---------------------------------------------------------------------------
# Builder entry points
# ---------------------------------------------------------------------------
def build_dynamic_watchlist(
    *,
    universe_candidates: Sequence[WatchlistCandidate],
    static_core: Sequence[str],
    cfg: dict[str, Any] | None = None,
    blocked_symbols: Iterable[str] = (),
    today: str | None = None,
    source: str = "ibkr",
) -> DynamicWatchlist:
    """Assemble the daily watchlist from pre-computed candidates.

    ``universe_candidates`` is the full set of rows the caller could
    compute (typically "every symbol in the configured universe with
    daily bars"). This function does **not** call IBKR; fetching is
    the CLI's responsibility so tests can pass synthetic data.
    """
    effective = {**DEFAULT_DYNAMIC_CFG, **(cfg or {})}

    filter_cfg = FilterConfig(
        min_price=float(effective["min_price"]),
        min_avg_20d_dollar_volume=float(
            effective["min_avg_20d_dollar_volume"]
        ),
        min_current_dollar_volume=float(
            effective["min_current_dollar_volume"]
        ),
        exclude_leveraged_etfs=bool(effective["exclude_leveraged_etfs"]),
        exclude_otc=bool(effective["exclude_otc"]),
        exclude_blocked_symbols=bool(effective["exclude_blocked_symbols"]),
    )
    blocked_set = {s.upper() for s in blocked_symbols}

    # 1) Per-bucket selection BEFORE filtering so the user can see why
    #    a name was picked even if it is later blocked.
    high_volume_cands: list[WatchlistCandidate] = []
    if effective["include_high_volume"]:
        high_volume_cands = _top_by(
            universe_candidates,
            lambda r: r.current_dollar_volume,
            int(effective["top_current_dollar_volume_count"]),
        )

    relvol_cands: list[WatchlistCandidate] = []
    if effective["include_high_relative_volume"]:
        relvol_threshold = float(
            effective["min_relative_volume_for_relvol_bucket"]
        )
        eligible = [
            r for r in universe_candidates
            if r.relative_volume is not None
            and r.relative_volume >= relvol_threshold
        ]
        relvol_cands = _top_by(
            eligible,
            lambda r: r.relative_volume,
            int(effective["top_relative_volume_count"]),
        )

    avg_dv_cands = _top_by(
        universe_candidates,
        lambda r: r.avg_20d_dollar_volume,
        int(effective["top_avg_dollar_volume_count"]),
    )

    vol_proxy_cands: list[WatchlistCandidate] = []
    if effective["include_high_beta_or_volatility"]:
        beta_thr = float(effective["high_beta_threshold"])
        atr_thr = float(effective["high_atr_pct_threshold"])
        rv_thr = float(effective["high_realized_vol_20d_threshold"])
        eligible = [
            r for r in universe_candidates
            if (
                (r.beta is not None and r.beta >= beta_thr)
                or (r.atr_pct is not None and r.atr_pct >= atr_thr)
                or (
                    r.realized_vol_20d is not None
                    and r.realized_vol_20d >= rv_thr
                )
            )
        ]
        vol_proxy_cands = _top_by(
            eligible,
            lambda r: (
                (r.atr_pct or 0.0)
                + (r.realized_vol_20d or 0.0) / 10.0
            ),
            int(effective["top_high_volatility_count"]),
        )

    # Static core.
    core_cands: list[WatchlistCandidate] = []
    if effective["include_static_core"]:
        by_sym = {c.symbol.upper(): c for c in universe_candidates}
        for sym in static_core:
            existing = by_sym.get(sym.upper())
            if existing is not None:
                core_cands.append(existing)
            else:
                core_cands.append(
                    WatchlistCandidate(
                        symbol=sym.upper(),
                        missing_fields=["bars"],
                    )
                )

    merged = _merge_candidates(
        ("static_core", core_cands),
        ("high_current_dollar_volume", high_volume_cands),
        ("high_relative_volume", relvol_cands),
        ("high_avg_dollar_volume", avg_dv_cands),
        ("high_volatility_proxy", vol_proxy_cands),
    )

    # 2) Apply liquidity filters. We keep blocked rows in the output
    #    so the operator can see *why* a name was dropped; the SMC
    #    scanner will skip blocked rows when reading the file.
    for row in merged:
        _apply_liquidity_filters(row, filter_cfg, blocked_set)

    # 3) Compute the rank score using the final population as the
    #    normalisation base so weights stay comparable across runs.
    max_cur_dv = max((r.current_dollar_volume or 0.0) for r in merged) or None
    max_avg_dv = max((r.avg_20d_dollar_volume or 0.0) for r in merged) or None
    max_rel = max((r.relative_volume or 0.0) for r in merged) or None
    max_vol = max(
        max((r.atr_pct or 0.0) for r in merged),
        max((r.realized_vol_20d or 0.0) for r in merged),
    ) or None
    for row in merged:
        row.volume_rank_score = compute_volume_rank_score(
            row,
            max_cur_dv=max_cur_dv,
            max_avg_dv=max_avg_dv,
            max_rel_vol=max_rel,
            max_vol_proxy=max_vol,
        )

    # 4) Ranking + max_symbols clip. Static-core rows are never
    #    dropped by the clip because they also carry the "static_core"
    #    reason tag which wins ties when the score is missing.
    def _sort_key(r: WatchlistCandidate) -> tuple[int, int, float]:
        is_static = 1 if "static_core" in r.reason else 0
        is_blocked = 0 if r.blocked else 1
        score = r.volume_rank_score or 0.0
        return (is_blocked, is_static, score)

    merged.sort(key=_sort_key, reverse=True)

    max_symbols = int(effective["max_symbols"])
    # Ensure static_core is not trimmed: cap is applied to non-core
    # rows first.
    core_syms = {s.upper() for s in static_core} if effective["include_static_core"] else set()
    kept: list[WatchlistCandidate] = []
    non_core: list[WatchlistCandidate] = []
    for r in merged:
        if r.symbol in core_syms:
            kept.append(r)
        else:
            non_core.append(r)
    remaining_slots = max(0, max_symbols - len(kept))
    kept.extend(non_core[:remaining_slots])

    # Missing data summary across the batch.
    missing_data: list[str] = []
    if any("current_volume" in r.missing_fields for r in kept):
        missing_data.append("current_volume")
    if any("beta" in r.missing_fields for r in kept):
        missing_data.append("beta")
    if any("relative_volume" in r.missing_fields for r in kept):
        missing_data.append("relative_volume")

    return DynamicWatchlist(
        date=today or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        source=source,
        symbols=kept,
        missing_data=missing_data,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def save_dynamic_watchlist(
    cfg: AppConfig, watchlist: DynamicWatchlist, *, directory: str = "data/watchlists"
) -> Path:
    out_dir = cfg.absolute(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{watchlist.date}-dynamic-watchlist.json"
    path.write_text(
        json.dumps(watchlist.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )
    return path


def load_dynamic_watchlist(
    cfg: AppConfig, *, date: str | None = None, directory: str = "data/watchlists"
) -> DynamicWatchlist | None:
    """Read a previously saved dynamic watchlist, or ``None``."""
    out_dir = cfg.absolute(directory)
    if not out_dir.is_dir():
        return None
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = out_dir / f"{date}-dynamic-watchlist.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rows = [
        WatchlistCandidate(
            symbol=str(r.get("symbol") or "").upper(),
            reason=list(r.get("reason") or []),
            latest_price=r.get("latest_price"),
            current_volume=r.get("current_volume"),
            current_dollar_volume=r.get("current_dollar_volume"),
            avg_20d_volume=r.get("avg_20d_volume"),
            avg_20d_dollar_volume=r.get("avg_20d_dollar_volume"),
            relative_volume=r.get("relative_volume"),
            volume_activity=str(r.get("volume_activity") or "unknown"),
            volume_rank_score=r.get("volume_rank_score"),
            beta=r.get("beta"),
            atr_pct=r.get("atr_pct"),
            realized_vol_20d=r.get("realized_vol_20d"),
            blocked=bool(r.get("blocked")),
            block_reason=r.get("block_reason"),
            missing_fields=list(r.get("missing_fields") or []),
        )
        for r in (payload.get("symbols") or [])
    ]
    return DynamicWatchlist(
        date=str(payload.get("date") or date),
        source=str(payload.get("source") or "cache"),
        symbols=rows,
        missing_data=list(payload.get("missing_data") or []),
    )


__all__ = [
    "DEFAULT_STATIC_CORE",
    "DEFAULT_DYNAMIC_CFG",
    "DEFAULT_LEVERAGED_ETF_BLOCKLIST",
    "WatchlistCandidate",
    "DynamicWatchlist",
    "FilterConfig",
    "build_candidate_from_bars",
    "build_dynamic_watchlist",
    "classify_relative_volume",
    "compute_volume_rank_score",
    "save_dynamic_watchlist",
    "load_dynamic_watchlist",
]
