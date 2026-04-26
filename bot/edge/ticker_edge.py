"""Per-ticker edge profiles for ict_smc_intraday_v1 (Prompt 13L-alt).

Transparent scoring only — no ML. All inputs come from the intraday
backtest engine (cached candles; no live trading).
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..backtests.intraday_engine import BACKTEST_STRATEGY_KEY, Trade
from ..backtests.metrics import SIGNAL_AGGRESSIVE, SIGNAL_STRICT

if TYPE_CHECKING:
    from ..backtests.intraday_engine import BacktestRun

# ---------------------------------------------------------------------------
# Recommended / confidence tags (string contracts for JSON + UI)
# ---------------------------------------------------------------------------
REC_DISABLED = "disabled"
REC_WATCH_ONLY = "watch_only"
REC_STRICT_ONLY = "strict_only"
REC_STRICT_AND_AGGRESSIVE = "strict_and_aggressive"

CONF_INSUFFICIENT = "insufficient_data"
CONF_WEAK = "weak"
CONF_MODERATE = "moderate"
CONF_STRONG = "strong"
CONF_NEGATIVE = "negative"

DEFAULT_MIN_TRADES_MODERATE = 30
DEFAULT_MIN_TRADES_STRONG = 60
PROFIT_FACTOR_STRONG = 1.5
AVERAGE_R_POSITIVE_THRESHOLD = 0.1
DEFAULT_MAX_DRAWDOWN_R_LIMIT = 25.0  # "acceptable" if abs less than this


@dataclass
class TickerEdgeProfile:
    symbol: str
    strategy_id: str
    sample_start: str
    sample_end: str
    total_signals: int
    filled_trades: int
    fill_rate: float
    win_rate: float | None
    average_r: float | None
    median_r: float | None
    total_r: float
    max_drawdown_r: float
    profit_factor: float | None
    strict_count: int
    strict_win_rate: float | None
    strict_average_r: float | None
    aggressive_count: int
    aggressive_win_rate: float | None
    aggressive_average_r: float | None
    long_count: int
    long_win_rate: float | None
    long_average_r: float | None
    short_count: int
    short_win_rate: float | None
    short_average_r: float | None
    best_hours: list[str] = field(default_factory=list)
    weak_hours: list[str] = field(default_factory=list)
    best_direction: str = "both"  # long | short | both
    reliability_score: float = 0.0
    edge_score: float = 0.0
    confidence_level: str = CONF_INSUFFICIENT
    recommended_mode: str = REC_WATCH_ONLY
    max_risk_multiplier: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items()}


def _mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _win_rate_from_trades(ts: list[Trade]) -> float | None:
    if not ts:
        return None
    w = sum(1 for t in ts if t.outcome == "win")
    return w / len(ts)


def _trades_from_summary_dict(
    backtest_summary: dict[str, Any], symbol: str
) -> list[Trade]:
    out: list[Trade] = []
    for d in backtest_summary.get("trades") or []:
        if not isinstance(d, dict):
            continue
        if str(d.get("symbol") or "").upper() != symbol.upper():
            continue
        out.append(
            Trade(
                trade_id=str(d.get("trade_id") or ""),
                symbol=symbol.upper(),
                date=str(d.get("date") or ""),
                strategy_id=str(d.get("strategy_id") or BACKTEST_STRATEGY_KEY),
                direction=str(d.get("direction") or "long"),
                signal_category=str(d.get("signal_category") or ""),
                setup_type=str(d.get("setup_type") or ""),
                trigger_type=str(d.get("trigger_type") or ""),
                entry_time=str(d.get("entry_time") or ""),
                entry_price=float(d.get("entry_price") or 0.0),
                stop_price=float(d.get("stop_price") or 0.0),
                target_price=float(d.get("target_price") or 0.0),
                exit_time=str(d.get("exit_time") or ""),
                exit_price=d.get("exit_price"),
                outcome=str(d.get("outcome") or "not_filled"),
                pnl_r=d.get("pnl_r"),
                gross_pnl=d.get("gross_pnl"),
                planned_rr=d.get("planned_rr"),
                mfe_r=d.get("mfe_r"),
                mae_r=d.get("mae_r"),
                bars_held=d.get("bars_held"),
                notes=[],
            )
        )
    return out


def _trades_from_csv(path: Path, symbol: str) -> list[Trade]:
    p = Path(path)
    if not p.is_file():
        return []
    out: list[Trade] = []
    try:
        with p.open("r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                if str(row.get("symbol") or "").upper() != symbol.upper():
                    continue
                out.append(
                    Trade(
                        trade_id=str(row.get("trade_id") or ""),
                        symbol=symbol.upper(),
                        date=str(row.get("date") or ""),
                        strategy_id=str(row.get("strategy_id") or BACKTEST_STRATEGY_KEY),
                        direction=str(row.get("direction") or "long"),
                        signal_category=str(row.get("signal_category") or ""),
                        setup_type=str(row.get("setup_type") or ""),
                        trigger_type=str(row.get("trigger_type") or ""),
                        entry_time=str(row.get("entry_time") or ""),
                        entry_price=float(row.get("entry_price") or 0.0),
                        stop_price=float(row.get("stop_price") or 0.0),
                        target_price=float(row.get("target_price") or 0.0),
                        exit_time=str(row.get("exit_time") or ""),
                        exit_price=float(row["exit_price"])
                        if row.get("exit_price")
                        else None,
                        outcome=str(row.get("outcome") or "not_filled"),
                        pnl_r=float(row["pnl_r"])
                        if row.get("pnl_r") not in (None, "")
                        else None,
                        gross_pnl=float(row["gross_pnl"])
                        if row.get("gross_pnl") not in (None, "")
                        else None,
                        planned_rr=float(row["planned_rr"])
                        if row.get("planned_rr") not in (None, "")
                        else None,
                        mfe_r=float(row["mfe_r"])
                        if row.get("mfe_r") not in (None, "")
                        else None,
                        mae_r=float(row["mae_r"])
                        if row.get("mae_r") not in (None, "")
                        else None,
                        bars_held=int(row["bars_held"])
                        if row.get("bars_held") not in (None, "")
                        else None,
                        notes=[],
                    )
                )
    except OSError:
        return []
    return out


def _best_weak_hours(
    by_hour: dict[str, dict[str, float]] | None,
) -> tuple[list[str], list[str]]:
    if not by_hour:
        return [], []
    scored: list[tuple[str, float]] = []
    for hk, b in by_hour.items():
        ar = b.get("average_r")
        if ar is None or (isinstance(ar, float) and math.isnan(ar)):
            tr = b.get("total_r")
            s = float(tr) if tr is not None else 0.0
        else:
            s = float(ar)
        scored.append((hk, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    best = [h for h, _ in scored[:3] if h]
    weak = [h for h, _ in sorted(scored, key=lambda x: x[1])[:3] if h]
    return best, weak


def _classify_and_score(
    *,
    n_filled: int,
    n_signals: int,
    avg_r: float | None,
    pf: float | None,
    mdd: float,
    min_mod: int,
    min_str: int,
    max_dd_limit: float,
) -> tuple[float, str, str, float, str]:
    """Return edge_score, confidence, recommended_mode, max_risk_mult, notes."""
    notes_parts: list[str] = []
    if n_signals <= 0:
        return (
            0.0,
            CONF_INSUFFICIENT,
            REC_WATCH_ONLY,
            0.0,
            "no signals in backtest window",
        )

    fill_rate = n_filled / max(1, n_signals)
    if n_filled < 1:
        notes_parts.append("no filled trades")
        return (
            5.0,
            CONF_INSUFFICIENT,
            REC_WATCH_ONLY,
            0.0,
            "; ".join(notes_parts) or "no fills",
        )

    # transparent weighted score
    s_pf = 0.0
    if pf is not None and not math.isinf(pf) and pf > 0:
        s_pf = min(30.0, 10.0 * math.log1p(pf))  # 0..~30
    s_ar = 0.0
    if avg_r is not None:
        s_ar = max(0.0, min(25.0, 20.0 * max(0.0, float(avg_r) + 0.5)))
    s_tr = 0.0
    s_tr = min(20.0, 2.0 * math.sqrt(n_filled))
    s_dd = max(0.0, 15.0 - min(15.0, abs(float(mdd)) * 0.4))
    s_fill = 10.0 * min(1.0, fill_rate)
    small_pen = 0.0
    if n_filled < 20:
        small_pen = 20.0 * (1.0 - n_filled / 20.0)
    if fill_rate < 0.1:
        small_pen += 8.0
    if avg_r is not None and avg_r < 0:
        small_pen += 10.0 + 15.0 * min(1.0, -float(avg_r))
    if pf is not None and pf < 1.0 and not math.isinf(pf):
        small_pen += 8.0
    if abs(float(mdd)) > max_dd_limit:
        small_pen += 8.0

    raw = s_pf + s_ar + s_tr + s_dd + s_fill
    edge_score = max(0.0, min(100.0, raw - small_pen))
    rel = min(100.0, 30.0 * math.log1p(n_filled))

    conf = CONF_WEAK
    rec = REC_WATCH_ONLY
    mrm = 0.25

    neg = (avg_r is not None and avg_r < 0) and (pf is None or (pf is not None and pf < 1.0 and not math.isinf(pf)))
    if neg and n_filled >= 15:
        return (
            min(edge_score, 20.0),
            CONF_NEGATIVE,
            REC_DISABLED,
            0.0,
            "negative expectation (avg R and profit factor); disabled for paper",
        )

    if n_filled < 20 or n_filled < min_mod:
        conf = CONF_INSUFFICIENT
        rec = REC_WATCH_ONLY
        mrm = 0.25 if n_filled >= 5 else 0.0
        notes_parts.append(
            f"sample {n_filled} fills; need {min_mod}+ for moderate confidence"
        )
    else:
        ok_pf = pf is not None and (math.isinf(pf) or pf >= PROFIT_FACTOR_STRONG)
        ok_ar = avg_r is not None and float(avg_r) > AVERAGE_R_POSITIVE_THRESHOLD
        ok_dd = abs(float(mdd)) <= max_dd_limit
        if n_filled >= min_str and ok_pf and ok_ar and ok_dd:
            conf = CONF_STRONG
            rec = REC_STRICT_AND_AGGRESSIVE
            mrm = 1.0
        elif n_filled >= min_mod and (
            (avg_r is not None and float(avg_r) > 0) or (pf is not None and pf >= 1.05)
        ):
            conf = CONF_MODERATE
            rec = REC_STRICT_ONLY
            mrm = 0.5
        else:
            conf = CONF_WEAK
            rec = REC_WATCH_ONLY
            mrm = 0.25

    return (edge_score, conf, rec, mrm, "; ".join(notes_parts) or "scored from backtest")


def best_direction_for_trades(
    longs: list[Trade], shorts: list[Trade]
) -> str:
    lr = _mean([float(t.pnl_r) for t in longs if t.pnl_r is not None]) or 0.0
    sr = _mean([float(t.pnl_r) for t in shorts if t.pnl_r is not None]) or 0.0
    if not longs and not shorts:
        return "both"
    if not longs:
        return "short"
    if not shorts:
        return "long"
    if abs(lr - sr) < 0.05:
        return "both"
    return "long" if lr >= sr else "short"


def build_ticker_edge_profile(
    symbol: str,
    backtest_summary: dict[str, Any] | None,
    trades_csv: Path | str | None = None,
    *,
    min_trades_moderate: int = DEFAULT_MIN_TRADES_MODERATE,
    min_trades_strong: int = DEFAULT_MIN_TRADES_STRONG,
    max_drawdown_r_limit: float = DEFAULT_MAX_DRAWDOWN_R_LIMIT,
) -> TickerEdgeProfile:
    """Build a :class:`TickerEdgeProfile` from a saved backtest JSON + optional CSV.

    If *backtest_summary* is None or empty, returns an insufficient_data profile.
    """
    sym = (symbol or "").strip().upper()
    sid = str(
        (backtest_summary or {}).get("strategy_id") or BACKTEST_STRATEGY_KEY
    )
    if not backtest_summary or not sym:
        return TickerEdgeProfile(
            symbol=sym or "UNKNOWN",
            strategy_id=sid,
            sample_start="",
            sample_end="",
            total_signals=0,
            filled_trades=0,
            fill_rate=0.0,
            win_rate=None,
            average_r=None,
            median_r=None,
            total_r=0.0,
            max_drawdown_r=0.0,
            profit_factor=None,
            strict_count=0,
            strict_win_rate=None,
            strict_average_r=None,
            aggressive_count=0,
            aggressive_win_rate=None,
            aggressive_average_r=None,
            long_count=0,
            long_win_rate=None,
            long_average_r=None,
            short_count=0,
            short_win_rate=None,
            short_average_r=None,
            best_hours=[],
            weak_hours=[],
            best_direction="both",
            reliability_score=0.0,
            edge_score=0.0,
            confidence_level=CONF_INSUFFICIENT,
            recommended_mode=REC_WATCH_ONLY,
            max_risk_multiplier=0.0,
            notes="missing backtest summary; run backtest-intraday-smc first",
        )

    cfg = backtest_summary.get("config") or {}
    start = str(cfg.get("start") or "")
    end = str(cfg.get("end") or "")
    metrics = backtest_summary.get("metrics") or {}
    all_tr = _trades_from_summary_dict(backtest_summary, sym)
    if not all_tr and trades_csv:
        all_tr = _trades_from_csv(Path(trades_csv), sym)
    if not all_tr and trades_csv:
        pcsv = Path(trades_csv)
        if pcsv.is_file():
            all_tr = _trades_from_csv(pcsv, sym)  # already tried

    filled = [t for t in all_tr if t.outcome in {"win", "loss", "eod_exit"}]
    strict = [t for t in filled if t.signal_category == SIGNAL_STRICT]
    aggressive = [t for t in filled if t.signal_category == SIGNAL_AGGRESSIVE]
    longs = [t for t in filled if t.direction == "long"]
    shorts = [t for t in filled if t.direction == "short"]

    total_signals = int(
        metrics.get("total_signals")
        if metrics.get("total_signals") is not None
        else len(all_tr)
    )
    n_filled = len(filled)
    not_f = int(metrics.get("total_not_filled") or max(0, total_signals - n_filled))
    fill_rate = n_filled / max(1, total_signals) if total_signals else 0.0
    mdd = float(metrics.get("max_drawdown_r") or 0.0)
    if not metrics and filled:
        from ..backtests.metrics import compute_metrics  # noqa: PLC0415

        m2 = compute_metrics(all_tr, total_signals=total_signals)
        md = m2.to_dict()
        metrics = md
        mdd = float(md.get("max_drawdown_r") or 0.0)

    avg_r = metrics.get("average_r")
    med_r = metrics.get("median_r")
    if isinstance(avg_r, (int, float)) and not isinstance(avg_r, bool):
        avg_r = float(avg_r)
    else:
        avg_r = _mean(
            [float(t.pnl_r) for t in filled if t.pnl_r is not None]
        )
    if isinstance(med_r, (int, float)) and not isinstance(med_r, bool):
        med_r = float(med_r)
    else:
        med_r = None
    tr = float(metrics.get("total_r") or 0.0)
    if not tr and filled:
        tr = sum(float(t.pnl_r) for t in filled if t.pnl_r is not None)
    pf = metrics.get("profit_factor")
    if isinstance(pf, (int, float)) and not isinstance(pf, bool):
        pf = float(pf)
    else:
        pf = None

    win_rate = metrics.get("win_rate")
    if win_rate is None:
        win_rate = _win_rate_from_trades(filled)

    by_hour = metrics.get("by_hour") if isinstance(metrics.get("by_hour"), dict) else None
    best_h, weak_h = _best_weak_hours(by_hour)

    edge_score, conf, rec, mrm, note_s = _classify_and_score(
        n_filled=n_filled,
        n_signals=max(total_signals, 1),
        avg_r=avg_r,
        pf=pf,
        mdd=mdd,
        min_mod=min_trades_moderate,
        min_str=min_trades_strong,
        max_dd_limit=max_drawdown_r_limit,
    )
    rel = min(100.0, 25.0 * math.log1p(max(1, n_filled)))

    return TickerEdgeProfile(
        symbol=sym,
        strategy_id=sid,
        sample_start=start,
        sample_end=end,
        total_signals=total_signals,
        filled_trades=n_filled,
        fill_rate=float(fill_rate),
        win_rate=win_rate,
        average_r=avg_r,
        median_r=med_r,
        total_r=tr,
        max_drawdown_r=mdd,
        profit_factor=pf,
        strict_count=len(strict),
        strict_win_rate=_win_rate_from_trades(strict),
        strict_average_r=_mean(
            [float(t.pnl_r) for t in strict if t.pnl_r is not None]
        ),
        aggressive_count=len(aggressive),
        aggressive_win_rate=_win_rate_from_trades(aggressive),
        aggressive_average_r=_mean(
            [float(t.pnl_r) for t in aggressive if t.pnl_r is not None]
        ),
        long_count=len(longs),
        long_win_rate=_win_rate_from_trades(longs),
        long_average_r=_mean(
            [float(t.pnl_r) for t in longs if t.pnl_r is not None]
        ),
        short_count=len(shorts),
        short_win_rate=_win_rate_from_trades(shorts),
        short_average_r=_mean(
            [float(t.pnl_r) for t in shorts if t.pnl_r is not None]
        ),
        best_hours=best_h,
        weak_hours=weak_h,
        best_direction=best_direction_for_trades(longs, shorts),
        reliability_score=rel,
        edge_score=edge_score,
        confidence_level=conf,
        recommended_mode=rec,
        max_risk_multiplier=mrm,
        notes=note_s,
    )


def profile_from_backtest_run(
    symbol: str,
    run: "BacktestRun",
    strategy_id: str = BACKTEST_STRATEGY_KEY,
    **kwargs: Any,
) -> TickerEdgeProfile:
    """Build a profile directly from a :class:`BacktestRun` in memory."""
    d = run.to_dict()
    d["strategy_id"] = strategy_id
    p = build_ticker_edge_profile(
        symbol, d, trades_csv=None, **kwargs
    )
    return p


def edge_profile_insufficient(
    symbol: str,
    strategy_id: str,
    sample_start: str,
    sample_end: str,
    notes: str,
) -> TickerEdgeProfile:
    """No-cache / no-run profile (still storable in JSON for ranking)."""
    su = (symbol or "").upper().strip() or "UNKNOWN"
    return TickerEdgeProfile(
        symbol=su,
        strategy_id=strategy_id,
        sample_start=sample_start,
        sample_end=sample_end,
        total_signals=0,
        filled_trades=0,
        fill_rate=0.0,
        win_rate=None,
        average_r=None,
        median_r=None,
        total_r=0.0,
        max_drawdown_r=0.0,
        profit_factor=None,
        strict_count=0,
        strict_win_rate=None,
        strict_average_r=None,
        aggressive_count=0,
        aggressive_win_rate=None,
        aggressive_average_r=None,
        long_count=0,
        long_win_rate=None,
        long_average_r=None,
        short_count=0,
        short_win_rate=None,
        short_average_r=None,
        best_hours=[],
        weak_hours=[],
        best_direction="both",
        reliability_score=0.0,
        edge_score=0.0,
        confidence_level=CONF_INSUFFICIENT,
        recommended_mode=REC_WATCH_ONLY,
        max_risk_multiplier=0.0,
        notes=notes,
    )
