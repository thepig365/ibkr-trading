"""Market-structure detection primitives (V0).

Pure, side-effect-free helpers used by the SMC Liquidity Reversal
research strategy. The functions in this module never reach out to a
broker, never place orders, and never read configuration directly.
They operate on a list of :class:`Candle` objects and return plain
dicts / dataclasses that downstream modules (or tests) can inspect.

The "engineering" definitions below intentionally replace the
ambiguous SMC / ICT vocabulary:

==================  ==========================================================
SMC term            Engineering definition used here
==================  ==========================================================
Liquidity sweep     A candle prints below a *confirmed* prior swing low
                    (within ``lookback`` bars) and closes back above it.
Change of           After a sweep, a later candle *closes above* the most
character (ChoCH)   recent confirmed pivot high that existed before the sweep.
Bullish FVG         A 3-candle imbalance where ``candle3.low > candle1.high``.
Bullish order       The last bearish (down-close) candle before the bullish
block               impulse leg that produced the ChoCH.
Structural stop     ``min(sweep_low, order_block_low) - buffer``.
==================  ==========================================================

Lookahead safety
----------------
Swing points are only "confirmed" once ``right_bars`` candles have
elapsed after them. The detectors expose ``allow_unconfirmed=True``
for backtesting / charting, but every downstream module in V0
restricts itself to confirmed swings only - no future data may leak
into a live decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------
@dataclass
class Candle:
    """A single OHLCV bar.

    ``timestamp`` is opaque to this module - any string is accepted;
    callers are expected to use ISO-8601 (``YYYY-MM-DD`` for daily
    bars, ``YYYY-MM-DDTHH:MM:SS`` for intraday).
    """

    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Candle":
        return cls(
            timestamp=str(d["timestamp"]),
            open=float(d["open"]),
            high=float(d["high"]),
            low=float(d["low"]),
            close=float(d["close"]),
            volume=float(d.get("volume", 0.0) or 0.0),
        )


SwingType = Literal["swing_high", "swing_low"]


@dataclass
class SwingPoint:
    type: SwingType
    index: int
    timestamp: str
    price: float
    left_bars: int
    right_bars: int
    confirmed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Convenience aliases for type hints.
Candles = list[Candle]


# ---------------------------------------------------------------------------
# Swing detection
# ---------------------------------------------------------------------------
def detect_swing_highs(
    candles: Candles,
    left: int = 2,
    right: int = 2,
    *,
    allow_unconfirmed: bool = False,
) -> list[SwingPoint]:
    """Return all swing-high candles in chronological order.

    A candle ``i`` is a swing high when its ``high`` is **strictly
    greater than** the highs of the ``left`` candles before it AND
    the ``right`` candles after it. By default only fully-confirmed
    swings are returned (i.e. there are at least ``right`` bars after
    the candle); set ``allow_unconfirmed=True`` to also include the
    most recent provisional swing for charting.
    """
    return _detect_swings(candles, left=left, right=right,
                          high=True, allow_unconfirmed=allow_unconfirmed)


def detect_swing_lows(
    candles: Candles,
    left: int = 2,
    right: int = 2,
    *,
    allow_unconfirmed: bool = False,
) -> list[SwingPoint]:
    """Return all swing-low candles in chronological order.

    A candle ``i`` is a swing low when its ``low`` is **strictly
    less than** the lows of the ``left`` candles before it AND the
    ``right`` candles after it.
    """
    return _detect_swings(candles, left=left, right=right,
                          high=False, allow_unconfirmed=allow_unconfirmed)


def _detect_swings(
    candles: Candles,
    *,
    left: int,
    right: int,
    high: bool,
    allow_unconfirmed: bool,
) -> list[SwingPoint]:
    if left < 1 or right < 1:
        raise ValueError("left and right must both be >= 1")
    n = len(candles)
    out: list[SwingPoint] = []
    for i in range(n):
        if i < left:
            continue
        ref = candles[i].high if high else candles[i].low
        # Left side must be strictly less (high) / greater (low).
        left_ok = all(
            (candles[i - k].high < ref) if high else (candles[i - k].low > ref)
            for k in range(1, left + 1)
        )
        if not left_ok:
            continue

        right_avail = min(right, n - 1 - i)
        right_ok = all(
            (candles[i + k].high < ref) if high else (candles[i + k].low > ref)
            for k in range(1, right_avail + 1)
        )
        if not right_ok:
            continue

        confirmed = right_avail >= right
        if not confirmed and not allow_unconfirmed:
            continue

        out.append(
            SwingPoint(
                type="swing_high" if high else "swing_low",
                index=i,
                timestamp=candles[i].timestamp,
                price=ref,
                left_bars=left,
                right_bars=right,
                confirmed=confirmed,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Liquidity sweep
# ---------------------------------------------------------------------------
def detect_liquidity_sweep(
    candles: Candles,
    lookback: int = 20,
    *,
    swings: list[SwingPoint] | None = None,
    require_close_back_above: bool = True,
) -> list[dict[str, Any]]:
    """Detect every bullish liquidity-sweep event in the series.

    For each candle ``i`` we look at the *confirmed* swing lows in the
    previous ``lookback`` bars (using indices, not bar count) and pick
    the lowest one. A sweep fires when:

        candle_i.low  <  swept_low.price
        candle_i.close >= swept_low.price   (when require_close_back_above)

    Returned in chronological order; callers typically use the most
    recent event ``[-1]``.
    """
    swings = swings or detect_swing_lows(candles)
    confirmed_lows = [s for s in swings if s.confirmed]

    out: list[dict[str, Any]] = []
    for i in range(1, len(candles)):
        c = candles[i]
        candidates = [
            s for s in confirmed_lows
            if s.index < i and (i - s.index) <= lookback
        ]
        if not candidates:
            continue
        # Significant liquidity sits at the lowest prior swing low.
        swept = min(candidates, key=lambda s: s.price)
        if c.low >= swept.price:
            continue
        if require_close_back_above and c.close < swept.price:
            continue
        out.append({
            "found": True,
            "timestamp": c.timestamp,
            "index": i,
            "swept_low_index": swept.index,
            "swept_low_price": swept.price,
            "sweep_low": c.low,
            "close": c.close,
            "closed_back_above": c.close >= swept.price,
        })
    return out


# ---------------------------------------------------------------------------
# Change of character (ChoCH)
# ---------------------------------------------------------------------------
def detect_choch_after_sweep(
    candles: Candles,
    sweep_event: dict[str, Any] | None,
    *,
    max_bars_after_sweep: int = 10,
    swings: list[SwingPoint] | None = None,
    require_close_above_pivot_high: bool = True,
) -> dict[str, Any] | None:
    """Return the bullish ChoCH that follows ``sweep_event``, if any.

    Algorithm: locate the most recent confirmed pivot high *strictly
    before* the sweep candle, then walk forward up to
    ``max_bars_after_sweep`` candles looking for the first one whose
    ``close`` exceeds that pivot. Wick-only breaks are rejected when
    ``require_close_above_pivot_high`` is True (the default).
    """
    if not sweep_event:
        return None
    swings = swings or detect_swing_highs(candles)
    confirmed_highs = [
        s for s in swings if s.confirmed and s.index < sweep_event["index"]
    ]
    if not confirmed_highs:
        return None
    pivot = max(confirmed_highs, key=lambda s: s.index)

    start = sweep_event["index"] + 1
    end = min(start + max_bars_after_sweep, len(candles))
    for j in range(start, end):
        c = candles[j]
        broke = (
            (c.close > pivot.price) if require_close_above_pivot_high
            else (c.high > pivot.price)
        )
        if broke:
            return {
                "found": True,
                "timestamp": c.timestamp,
                "index": j,
                "pivot_high_index": pivot.index,
                "pivot_high_broken": pivot.price,
                "close": c.close,
                "bars_after_sweep": j - sweep_event["index"],
            }
    return None


# ---------------------------------------------------------------------------
# Bullish fair-value gap
# ---------------------------------------------------------------------------
def detect_bullish_fvg(candles: Candles) -> list[dict[str, Any]]:
    """Find every bullish 3-candle imbalance.

    A bullish FVG exists at ``i`` when ``candles[i].low > candles[i-2].high``.
    The gap zone runs from ``candles[i-2].high`` (low of zone) to
    ``candles[i].low`` (high of zone).
    """
    out: list[dict[str, Any]] = []
    for i in range(2, len(candles)):
        c1 = candles[i - 2]
        c3 = candles[i]
        if c3.low > c1.high:
            zone_low = c1.high
            zone_high = c3.low
            ref = c1.high if c1.high > 0 else (c3.low or 1.0)
            size_pct = ((zone_high - zone_low) / ref) * 100.0 if ref > 0 else 0.0
            out.append({
                "found": True,
                "start_index": i - 2,
                "end_index": i,
                "timestamp": candles[i - 1].timestamp,
                "low": zone_low,
                "high": zone_high,
                "size_pct": round(size_pct, 4),
            })
    return out


def select_fvg_for_setup(
    candles: Candles,
    sweep_event: dict[str, Any] | None,
    choch_event: dict[str, Any] | None,
    *,
    min_size_pct: float = 0.10,
    max_distance_from_choch_bars: int = 3,
    fvgs: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Pick the FVG that belongs to the current setup, if any.

    Selection rule:
      * the FVG's middle candle (``end_index - 1``) must be between
        the sweep and ``max_distance_from_choch_bars`` bars after the
        ChoCH candle.
      * the FVG must be at least ``min_size_pct`` percent wide.
      * when several qualify, prefer the one closest to (but not after)
        the ChoCH candle - that is the imbalance left behind by the
        impulse leg.
    """
    if not (sweep_event and choch_event):
        return None
    fvgs = fvgs or detect_bullish_fvg(candles)
    qualifying: list[dict[str, Any]] = []
    for fvg in fvgs:
        mid_idx = fvg["end_index"] - 1
        if mid_idx <= sweep_event["index"]:
            continue
        if mid_idx > choch_event["index"] + max_distance_from_choch_bars:
            continue
        if fvg["size_pct"] < min_size_pct:
            continue
        qualifying.append(fvg)
    if not qualifying:
        return None
    # Prefer the FVG ending on / closest to the ChoCH candle.
    qualifying.sort(
        key=lambda f: abs(f["end_index"] - choch_event["index"])
    )
    return qualifying[0]


# ---------------------------------------------------------------------------
# Bullish order block
# ---------------------------------------------------------------------------
def detect_bullish_order_block(
    candles: Candles,
    choch_event: dict[str, Any] | None,
    *,
    max_lookback: int = 20,
) -> dict[str, Any] | None:
    """Return the last bearish (close < open) candle before the ChoCH.

    Walks backwards from ``choch.index - 1`` for at most
    ``max_lookback`` candles. Returns ``None`` when no down-close
    candle exists in that window.
    """
    if not choch_event:
        return None
    j = int(choch_event["index"])
    start = max(0, j - max_lookback)
    for k in range(j - 1, start - 1, -1):
        c = candles[k]
        if c.close < c.open:
            return {
                "found": True,
                "index": k,
                "timestamp": c.timestamp,
                "low": c.low,
                "high": c.high,
                "open": c.open,
                "close": c.close,
            }
    return None


# ---------------------------------------------------------------------------
# Structural stop
# ---------------------------------------------------------------------------
def calculate_structural_stop(
    setup: dict[str, Any],
    buffer_cents: float = 0.05,
) -> float:
    """Compute the structural stop for a sweep + order-block setup.

    ``structural_stop = min(sweep_low, order_block_low) - buffer``.
    Either component may be missing; whichever is present is used.
    Raises :class:`ValueError` when neither is present.
    """
    sweep = setup.get("sweep") or {}
    ob = setup.get("order_block") or {}
    candidates: list[float] = []
    sw_low = sweep.get("sweep_low")
    ob_low = ob.get("low")
    if isinstance(sw_low, (int, float)):
        candidates.append(float(sw_low))
    if isinstance(ob_low, (int, float)):
        candidates.append(float(ob_low))
    if not candidates:
        raise ValueError(
            "structural stop requires at least sweep.sweep_low or order_block.low"
        )
    base = min(candidates)
    return round(base - float(buffer_cents), 4)


# ---------------------------------------------------------------------------
# Helpers used by the strategy engine
# ---------------------------------------------------------------------------
def prior_swing_high_target(
    candles: Candles,
    sweep_event: dict[str, Any],
    *,
    swings_high: list[SwingPoint] | None = None,
) -> float | None:
    """Legacy helper: highest confirmed swing-high before the sweep.

    Retained for backwards compatibility and test fixtures.
    :func:`select_target_1` is the V1 target selector and must be
    preferred by any new caller.
    """
    swings_high = swings_high or detect_swing_highs(candles)
    eligible = [
        s for s in swings_high
        if s.confirmed and s.index < sweep_event["index"]
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda s: s.price).price


# ---------------------------------------------------------------------------
# Target 1 — nearest buy-side liquidity
# ---------------------------------------------------------------------------
def select_target_1(
    candles: Candles,
    sweep_event: dict[str, Any],
    choch_event: dict[str, Any] | None,
    *,
    entry_price: float,
    risk_per_share: float,
    swings_high: list[SwingPoint] | None = None,
    lookback_bars_before_sweep: int = 60,
    max_target_distance_pct: float = 25.0,
    min_risk_reward: float = 2.0,
) -> tuple[float | None, list[dict[str, Any]], str | None]:
    """Pick the nearest buy-side-liquidity target above ``entry_price``.

    The V0 implementation used the *highest* prior swing high, which
    produced unrealistically optimistic R/R on symbols like TSLA where
    a multi-month absolute high sat far above the current range.
    V1 instead ranks candidates by *proximity* to ``entry_price`` and
    picks the nearest one that:

      * is above ``entry_price``,
      * sits within ``max_target_distance_pct`` of ``entry_price``,
      * yields ``risk_reward >= min_risk_reward``.

    Candidate hierarchy:

      1. confirmed swing highs in the ``lookback_bars_before_sweep``
         window up to and including the sweep bar,
      2. the ChoCH pivot high (if above entry),
      3. the highest bar in the same lookback window.

    Returns ``(price, candidates, rejection_reason)``:

      * ``price`` is ``None`` when no target qualifies,
      * ``candidates`` is the full debug list with per-entry
        ``distance_pct_from_entry``, ``risk_reward`` and ``selected``,
      * ``rejection_reason`` is one of
        ``"no_target_above_entry"``, ``"target_1_too_far"``,
        ``"no_target_meets_min_rr"``, or ``None`` on success.
    """
    if entry_price <= 0:
        return None, [], "invalid_entry_price"

    sweep_idx = int(sweep_event["index"])
    window_start = max(0, sweep_idx - int(lookback_bars_before_sweep))

    raw_candidates: list[dict[str, Any]] = []

    # Keep an explicit ``is None`` check so callers can pass ``[]`` to
    # *opt out* of the swing-high channel entirely (useful in tests
    # that exercise the range-high fallback in isolation).
    if swings_high is None:
        swings_high = detect_swing_highs(candles)
    for s in swings_high:
        if not s.confirmed:
            continue
        if s.index < window_start or s.index > sweep_idx:
            continue
        if s.price <= entry_price:
            continue
        raw_candidates.append(
            _target_candidate(
                timestamp=s.timestamp,
                price=float(s.price),
                candidate_type="swing_high",
                entry=entry_price,
                risk=risk_per_share,
            )
        )

    if choch_event and isinstance(
        choch_event.get("pivot_high_broken"), (int, float)
    ):
        pivot_price = float(choch_event["pivot_high_broken"])
        if pivot_price > entry_price:
            pivot_idx = int(choch_event.get("pivot_high_index", -1))
            if 0 <= pivot_idx < len(candles):
                pivot_ts = candles[pivot_idx].timestamp
            else:
                pivot_ts = str(choch_event.get("timestamp", ""))
            raw_candidates.append(
                _target_candidate(
                    timestamp=pivot_ts,
                    price=pivot_price,
                    candidate_type="choch_pivot_high",
                    entry=entry_price,
                    risk=risk_per_share,
                )
            )

    if candles and sweep_idx >= window_start:
        window = candles[window_start : sweep_idx + 1]
        if window:
            top = max(window, key=lambda c: c.high)
            if top.high > entry_price:
                raw_candidates.append(
                    _target_candidate(
                        timestamp=top.timestamp,
                        price=float(top.high),
                        candidate_type="range_high",
                        entry=entry_price,
                        risk=risk_per_share,
                    )
                )

    # Deduplicate on (price, type): the range-high candidate often
    # duplicates a swing high. Keep the first occurrence per price
    # (swing highs are added first, so they win).
    deduped: list[dict[str, Any]] = []
    seen_prices: set[float] = set()
    for c in raw_candidates:
        key = round(float(c["price"]), 4)
        if key in seen_prices:
            continue
        seen_prices.add(key)
        deduped.append(c)

    # Sort from nearest to farthest above entry.
    deduped.sort(key=lambda c: float(c["price"]))

    if not deduped:
        return None, deduped, "no_target_above_entry"

    in_range = [
        c for c in deduped
        if float(c["distance_pct_from_entry"]) <= float(max_target_distance_pct)
    ]
    if not in_range:
        return None, deduped, "target_1_too_far"

    for c in in_range:
        if float(c["risk_reward"]) >= float(min_risk_reward):
            c["selected"] = True
            return float(c["price"]), deduped, None

    return None, deduped, "no_target_meets_min_rr"


def _target_candidate(
    *,
    timestamp: str,
    price: float,
    candidate_type: str,
    entry: float,
    risk: float,
) -> dict[str, Any]:
    distance_pct = (
        round(((price - entry) / entry) * 100.0, 4) if entry > 0 else 0.0
    )
    rr = (
        round((price - entry) / risk, 4)
        if risk and risk > 0 and price > entry
        else 0.0
    )
    return {
        "timestamp": str(timestamp),
        "price": round(float(price), 4),
        "type": candidate_type,
        "distance_pct_from_entry": distance_pct,
        "risk_reward": rr,
        "selected": False,
    }


def candles_from_records(records: Iterable[dict[str, Any]]) -> Candles:
    """Convert plain dict / CSV rows to typed :class:`Candle` objects."""
    return [Candle.from_dict(r) for r in records]


__all__ = [
    "Candle",
    "Candles",
    "SwingPoint",
    "SwingType",
    "detect_swing_highs",
    "detect_swing_lows",
    "detect_liquidity_sweep",
    "detect_choch_after_sweep",
    "detect_bullish_fvg",
    "select_fvg_for_setup",
    "detect_bullish_order_block",
    "calculate_structural_stop",
    "prior_swing_high_target",
    "select_target_1",
    "candles_from_records",
]
