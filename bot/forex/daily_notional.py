"""Melbourne-day notional bookkeeping for Forex auto paper."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

STATE_RELPATH = "data/runtime/forex_notional_melbourne_daily.json"


def _melbourne_date_str(now_utc: datetime, tz_name: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        z = ZoneInfo(tz_name or "Australia/Melbourne")
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()
    return now_utc.astimezone(z).date().isoformat()


def daily_state_path(root: Path) -> Path:
    return Path(root).resolve() / STATE_RELPATH


def load_notional_day(root: Path, *, timezone_name: str) -> dict[str, Any]:
    """Return persisted state for today's Melbourne calendar day."""

    p = daily_state_path(root)
    mel_day = _melbourne_date_str(datetime.now(timezone.utc), timezone_name)
    if not p.is_file():
        return {
            "date": mel_day,
            "total_usd": 0.0,
            "by_pair": {},
            "trade_count_pairs": {},
        }
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        raw = {}
    if isinstance(raw, dict) and raw.get("date") != mel_day:
        raw = {}
    base = dict(raw) if isinstance(raw, dict) else {}
    base.setdefault("date", mel_day)
    base.setdefault("total_usd", 0.0)
    base.setdefault("by_pair", {})
    base.setdefault("trade_count_pairs", {})
    base["by_pair"] = {str(k): float(v) for k, v in base["by_pair"].items()}
    base["total_usd"] = float(base.get("total_usd") or 0)
    tc = {}
    raw_tc = base.get("trade_count_pairs") or {}
    if isinstance(raw_tc, dict):
        tc = {str(k): int(v) for k, v in raw_tc.items()}
    base["trade_count_pairs"] = tc
    if base["date"] != mel_day:
        base = {
            "date": mel_day,
            "total_usd": 0.0,
            "by_pair": {},
            "trade_count_pairs": {},
        }
    return base


def save_notional_day(root: Path, payload: dict[str, Any]) -> None:
    p = daily_state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def can_add_notional(
    root: Path,
    *,
    pair_slug: str,
    usd_estimate: float,
    max_daily_usd: float,
    max_pair_usd: float,
    timezone_name: str,
) -> tuple[bool, str]:
    st = load_notional_day(root, timezone_name=timezone_name)
    tot = float(st.get("total_usd") or 0)
    by_pair = st.get("by_pair") if isinstance(st.get("by_pair"), dict) else {}
    spent_pair = float(by_pair.get(pair_slug.upper(), 0))

    new_tot = tot + usd_estimate
    new_pair = spent_pair + usd_estimate

    if new_tot > float(max_daily_usd) + 1e-6:
        return False, "max_daily_notional_usd"
    if new_pair > float(max_pair_usd) + 1e-6:
        return False, "per_pair_notional_usd_cap"
    return True, ""


def record_notional_trade(
    root: Path,
    *,
    pair_slug: str,
    usd_estimate: float,
    timezone_name: str,
) -> dict[str, Any]:
    """Add notional estimate after submitting (or intending) a forex trade."""

    st = load_notional_day(root, timezone_name=timezone_name)
    pair_u = pair_slug.upper()
    st["total_usd"] = float(st.get("total_usd") or 0) + abs(float(usd_estimate))
    bp = st.get("by_pair") if isinstance(st.get("by_pair"), dict) else {}
    bp[pair_u] = float(bp.get(pair_u, 0)) + abs(float(usd_estimate))
    st["by_pair"] = bp

    tcg = {}
    if isinstance(st.get("trade_count_pairs"), dict):
        tcg = dict(st["trade_count_pairs"])
    tcg[pair_u] = int(tcg.get(pair_u, 0)) + 1
    st["trade_count_pairs"] = tcg

    save_notional_day(root, st)
    return st


__all__ = [
    "STATE_RELPATH",
    "load_notional_day",
    "record_notional_trade",
    "can_add_notional",
    "save_notional_day",
]
