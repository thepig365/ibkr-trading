"""ICT 1-minute trading strategy.

Implements the Inner Circle Trader 1-minute model described in the spec:

* New York timezone awareness (DST handled by ``pytz``).
* Kill Zone / Silver Bullet / Macro time windows; mid-day dead zone block.
* Daily Bias (premium / discount) computed from a 20-day swing range.
* 3-bar Fair Value Gap detection on completed 1-minute bars.
* 1st Presented FVG tagging within the 9:30-10:00 AM opening window.
* 90-minute FVG expiry.
* Retest entry trigger (price re-enters the FVG and closes past its midpoint).
* Hard gates: R:R >= 2.0, stop width <= 1.5%.
* Scoring table -> auto-execute (>=60), alert-only (40-59), drop (<40).
* Breakeven move at +1R via :meth:`on_position_update`.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Deque, Optional

import pytz

from backend.config import AppConfig
from backend.strategy.base import BaseStrategy
from backend.strategy.models import Bar, Direction, Signal

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")


# Time-window tuples (start, end) in NY local time.
NY_OPEN_KZ = (time(8, 30), time(11, 0))
NY_SILVER_BULLET = (time(10, 0), time(11, 0))
NY_PM_SILVER_BULLET = (time(14, 0), time(15, 0))
LONDON_SILVER_BULLET = (time(3, 0), time(4, 0))
DEAD_ZONE = (time(11, 30), time(13, 0))
EOD_FORCE_CLOSE = time(15, 45)
OPENING_WINDOW = (time(9, 30), time(10, 0))

MACRO_TIMES = [
    time(9, 10),
    time(9, 50),
    time(10, 10),
    time(10, 50),
    time(11, 10),
    time(13, 10),
]

FVG_EXPIRY_MINUTES = 90


@dataclass
class FairValueGap:
    """A detected 3-bar Fair Value Gap on the 1m timeframe."""

    symbol: str
    direction: Direction
    top: float
    bottom: float
    created_at: datetime
    is_first_presented: bool = False
    consumed: bool = False

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def size(self) -> float:
        return self.top - self.bottom


@dataclass
class DailyBiasResult:
    """Daily bias output for a symbol."""

    direction: Direction
    equilibrium: float
    swing_high: float
    swing_low: float
    target_price: float
    confidence: float = 1.0
    reason: str = ""


@dataclass
class _SymbolState:
    """Per-symbol per-day rolling state for the ICT strategy."""

    bars: Deque[Bar] = field(default_factory=lambda: deque(maxlen=200))
    fvgs: list[FairValueGap] = field(default_factory=list)
    bias: Optional[DailyBiasResult] = None
    first_presented_assigned: bool = False
    last_session_date: Optional[datetime] = None
    trades_today: int = 0


class ICTStrategy(BaseStrategy):
    """Inner Circle Trader 1-minute strategy."""

    name = "ICT_1m"

    def __init__(self, config: AppConfig) -> None:
        super().__init__(config)
        self.ict_cfg = config.ict
        self.risk_cfg = config.risk
        self._states: dict[str, _SymbolState] = {}

    # ---------- BaseStrategy interface ---------- #

    def get_required_timeframes(self) -> list[str]:
        return ["1m", "15m", "1d"]

    def is_active_time(self, now_ny: datetime) -> bool:
        """ICT trades only during Kill Zones, never in the dead zone."""

        local = now_ny.astimezone(NY)
        if self._in_window(local.time(), DEAD_ZONE):
            return False
        return (
            self._in_window(local.time(), NY_OPEN_KZ)
            or self._in_window(local.time(), NY_PM_SILVER_BULLET)
            or self._in_window(local.time(), LONDON_SILVER_BULLET)
        )

    def on_bar(self, bar: Bar, context: dict[str, Any]) -> Optional[Signal]:
        state = self._get_state(bar.symbol)
        self._roll_session(state, bar)
        state.bars.append(bar)

        # Set or refresh the daily bias once per session if not preloaded.
        if state.bias is None:
            preloaded = context.get("daily_bias", {}).get(bar.symbol)
            if preloaded is not None:
                state.bias = preloaded

        self._detect_new_fvg(state, bar)
        self._expire_fvgs(state, bar.timestamp)

        if not self.is_active_time(bar.timestamp):
            return None
        if context.get("news_blackout", {}).get(bar.symbol):
            return None
        if state.trades_today >= self.risk_cfg.max_trades_per_day:
            return None
        if state.bias is None:
            return None

        signal = self._look_for_entry(state, bar)
        if signal is None:
            return None

        score = self._score_signal(signal, state, bar)
        signal.score = score
        signal.confidence = max(0.0, min(1.0, score / 100.0))
        if state.bias is not None:
            signal.confidence *= state.bias.confidence

        if score >= self.ict_cfg.auto_threshold:
            signal.auto_execute = True
        elif score >= self.ict_cfg.alert_threshold:
            signal.auto_execute = False
        else:
            return None

        if not self._risk_gate(signal):
            return None

        return signal

    def on_position_update(
        self, position: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """Move stop to breakeven once the position reaches +1R unrealised."""

        if position.get("moved_to_breakeven"):
            return None

        entry = float(position["entry_price"])
        risk = float(position["risk_per_share"])
        direction = Direction(position["direction"])
        last_price = float(position["last_price"])
        if risk <= 0:
            return None

        if direction is Direction.LONG:
            current_r = (last_price - entry) / risk
            new_stop = entry + 0.01
        else:
            current_r = (entry - last_price) / risk
            new_stop = entry - 0.01

        if current_r >= self.ict_cfg.trailing_activation_r:
            return {
                "action": "move_stop",
                "new_stop": round(new_stop, 4),
                "moved_to_breakeven": True,
            }
        return None

    # ---------- Daily bias ---------- #

    def compute_daily_bias(
        self, symbol: str, daily_bars: list[Bar], current_price: float
    ) -> Optional[DailyBiasResult]:
        """Compute the daily bias from the last 20 daily bars."""

        if len(daily_bars) < 5:
            return None

        recent = daily_bars[-20:]
        swing_high = max(bar.high for bar in recent)
        swing_low = min(bar.low for bar in recent)
        equilibrium = (swing_high + swing_low) / 2.0

        if current_price < equilibrium:
            direction = Direction.LONG
            target = swing_high
            reason = "discount-zone"
        elif current_price > equilibrium:
            direction = Direction.SHORT
            target = swing_low
            reason = "premium-zone"
        else:
            return None

        return DailyBiasResult(
            direction=direction,
            equilibrium=equilibrium,
            swing_high=swing_high,
            swing_low=swing_low,
            target_price=target,
            confidence=1.0,
            reason=reason,
        )

    def set_daily_bias(self, symbol: str, bias: Optional[DailyBiasResult]) -> None:
        """Inject a precomputed daily bias from the engine pre-market hook."""

        state = self._get_state(symbol)
        state.bias = bias

    # ---------- FVG detection ---------- #

    def _detect_new_fvg(self, state: _SymbolState, new_bar: Bar) -> None:
        # state.bars[-1] is `new_bar` (already appended). A 3-bar FVG needs the
        # bar two slots earlier (K1) plus the new bar (K3).
        if len(state.bars) < 3:
            return
        k1 = state.bars[-3]
        k3 = state.bars[-1]
        symbol = new_bar.symbol
        bias = state.bias

        if bias is None:
            return

        if bias.direction is Direction.LONG and k3.low > k1.high:
            gap = k3.low - k1.high
            if gap >= self.ict_cfg.min_fvg_size:
                fvg = FairValueGap(
                    symbol=symbol,
                    direction=Direction.LONG,
                    top=k3.low,
                    bottom=k1.high,
                    created_at=k3.timestamp,
                )
                self._tag_first_presented(state, fvg)
                state.fvgs.append(fvg)
                logger.debug(
                    "Bullish FVG %s: %.2f-%.2f at %s", symbol, fvg.bottom, fvg.top,
                    fvg.created_at.isoformat(),
                )
        elif bias.direction is Direction.SHORT and k3.high < k1.low:
            gap = k1.low - k3.high
            if gap >= self.ict_cfg.min_fvg_size:
                fvg = FairValueGap(
                    symbol=symbol,
                    direction=Direction.SHORT,
                    top=k1.low,
                    bottom=k3.high,
                    created_at=k3.timestamp,
                )
                self._tag_first_presented(state, fvg)
                state.fvgs.append(fvg)
                logger.debug(
                    "Bearish FVG %s: %.2f-%.2f at %s", symbol, fvg.bottom, fvg.top,
                    fvg.created_at.isoformat(),
                )

    def _tag_first_presented(self, state: _SymbolState, fvg: FairValueGap) -> None:
        if state.first_presented_assigned:
            return
        ny_time = fvg.created_at.astimezone(NY).time()
        if self._in_window(ny_time, OPENING_WINDOW):
            fvg.is_first_presented = True
            state.first_presented_assigned = True

    def _expire_fvgs(self, state: _SymbolState, now: datetime) -> None:
        cutoff = now - timedelta(minutes=FVG_EXPIRY_MINUTES)
        state.fvgs = [
            fvg
            for fvg in state.fvgs
            if not fvg.consumed and fvg.created_at >= cutoff
        ]

    # ---------- Entry trigger ---------- #

    def _look_for_entry(
        self, state: _SymbolState, bar: Bar
    ) -> Optional[Signal]:
        bias = state.bias
        if bias is None:
            return None

        candidate_fvgs = [
            fvg for fvg in state.fvgs if fvg.direction is bias.direction
        ]
        for fvg in candidate_fvgs:
            if fvg.consumed:
                continue
            if fvg.direction is Direction.LONG:
                if bar.low <= fvg.top and bar.close >= fvg.midpoint:
                    return self._build_long_signal(bar, fvg, bias)
            else:
                if bar.high >= fvg.bottom and bar.close <= fvg.midpoint:
                    return self._build_short_signal(bar, fvg, bias)
        return None

    def _build_long_signal(
        self, bar: Bar, fvg: FairValueGap, bias: DailyBiasResult
    ) -> Optional[Signal]:
        entry = bar.close
        stop = round(fvg.bottom * 0.999, 4)
        risk = entry - stop
        if risk <= 0:
            return None
        min_target = entry + risk * self.risk_cfg.min_rr_ratio
        take = max(bias.target_price, min_target)
        return Signal(
            strategy_name=self.name,
            symbol=bar.symbol,
            direction=Direction.LONG,
            entry_price=entry,
            stop_loss=stop,
            take_profit=take,
            confidence=1.0,
            reason=f"Bullish FVG retest | bias={bias.reason}",
            timeframe=bar.timeframe,
            timestamp=bar.timestamp,
            fvg_top=fvg.top,
            fvg_bottom=fvg.bottom,
        )

    def _build_short_signal(
        self, bar: Bar, fvg: FairValueGap, bias: DailyBiasResult
    ) -> Optional[Signal]:
        entry = bar.close
        stop = round(fvg.top * 1.001, 4)
        risk = stop - entry
        if risk <= 0:
            return None
        min_target = entry - risk * self.risk_cfg.min_rr_ratio
        take = min(bias.target_price, min_target)
        return Signal(
            strategy_name=self.name,
            symbol=bar.symbol,
            direction=Direction.SHORT,
            entry_price=entry,
            stop_loss=stop,
            take_profit=take,
            confidence=1.0,
            reason=f"Bearish FVG retest | bias={bias.reason}",
            timeframe=bar.timeframe,
            timestamp=bar.timestamp,
            fvg_top=fvg.top,
            fvg_bottom=fvg.bottom,
        )

    # ---------- Risk gate + scoring ---------- #

    def _risk_gate(self, signal: Signal) -> bool:
        if signal.entry_price <= 0:
            return False
        risk = abs(signal.entry_price - signal.stop_loss)
        if risk <= 0:
            return False
        reward = abs(signal.take_profit - signal.entry_price)
        rr = reward / risk
        if rr < self.risk_cfg.min_rr_ratio:
            logger.info(
                "%s rejected: RR=%.2f < %.2f", signal.symbol, rr, self.risk_cfg.min_rr_ratio
            )
            return False
        sl_width = risk / signal.entry_price
        if sl_width > self.risk_cfg.max_sl_width_pct:
            logger.info(
                "%s rejected: SL width=%.4f > %.4f", signal.symbol, sl_width,
                self.risk_cfg.max_sl_width_pct,
            )
            return False
        return True

    def _score_signal(
        self, signal: Signal, state: _SymbolState, bar: Bar
    ) -> float:
        local = bar.timestamp.astimezone(NY).time()
        score = 0.0

        if self._in_window(local, NY_SILVER_BULLET):
            score += 30
        elif self._in_window(local, NY_PM_SILVER_BULLET):
            score += 20
        elif self._in_window(local, NY_OPEN_KZ):
            score += 15

        # 1st Presented FVG bonus
        for fvg in state.fvgs:
            same_dir = fvg.direction is signal.direction
            same_levels = (
                signal.fvg_top is not None
                and signal.fvg_bottom is not None
                and abs(fvg.top - signal.fvg_top) < 1e-6
                and abs(fvg.bottom - signal.fvg_bottom) < 1e-6
            )
            if same_dir and same_levels and fvg.is_first_presented:
                score += 20
                break

        if any(self._near_macro(local, macro) for macro in MACRO_TIMES):
            score += 15

        if signal.fvg_top is not None and signal.fvg_bottom is not None:
            size = signal.fvg_top - signal.fvg_bottom
            if size > 3 * self.ict_cfg.min_fvg_size:
                score += 10

        return score

    # ---------- Helpers ---------- #

    def _get_state(self, symbol: str) -> _SymbolState:
        state = self._states.get(symbol)
        if state is None:
            state = _SymbolState()
            self._states[symbol] = state
        return state

    def _roll_session(self, state: _SymbolState, bar: Bar) -> None:
        ny_date = bar.timestamp.astimezone(NY).date()
        if state.last_session_date == ny_date:
            return
        state.last_session_date = ny_date
        state.fvgs.clear()
        state.first_presented_assigned = False
        state.trades_today = 0
        # `state.bias` is owned by the engine's premarket hook and
        # `set_daily_bias()`; it survives a session roll.

    def record_trade_for_day(self, symbol: str) -> None:
        """Increment per-symbol per-day trade counter (called by the engine)."""

        self._get_state(symbol).trades_today += 1

    @staticmethod
    def _in_window(now: time, window: tuple[time, time]) -> bool:
        start, end = window
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end

    @staticmethod
    def _near_macro(now: time, macro: time, window_minutes: int = 10) -> bool:
        today = datetime(2000, 1, 1)
        a = datetime.combine(today, now)
        b = datetime.combine(today, macro)
        return abs((a - b).total_seconds()) <= window_minutes * 60
