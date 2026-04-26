"""ICT/SMC intraday paper execution chain invariants (Prompt 13L-alt+).

Paper submission must **not** be driven by Edge Profiler, news, watchlist
scores, relative volume, or ranking — only by a **current** intraday scan
row that satisfies the full ICT chain (higher-TF context → 5m setup → 1m
trigger) with a valid bracket.

These checks are evaluated on the **compact summary row** dict produced by
:class:`bot.strategies.ict_smc_intraday.scanner` (``five_min_setup_found``,
``one_min_trigger_found``, ``higher_timeframe_context_ok``).

Edge profiling may only adjust risk multiplier and strict/aggressive
eligibility after these invariants pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..strategies.ict_smc_intraday.model import (
    SIGNAL_DAY_TRADE_READY_AGGRESSIVE,
    SIGNAL_DAY_TRADE_READY_STRICT,
)

# Audit / skip reason strings (stable contracts for JSONL + UI)
WAITING_FOR_1M_TRIGGER = "waiting_for_1m_trigger"
STRUCTURE_CONTEXT_MISSING = "structure_context_missing"
ICT_EXECUTION_FLAGS_INCOMPLETE = "ict_execution_flags_incomplete"


def validate_ict_chain_flags_for_paper(item: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Return (ok, []) or (False, [stable reason, ...]).

    Call this for rows that the caller has already restricted to
    ``DAY_TRADE_READY_STRICT`` / ``DAY_TRADE_READY_AGGRESSIVE``.

    * ``five_min_setup_found`` / ``one_min_trigger_found`` must be
      **present** and true (defence against tampered or legacy JSON).
    * ``higher_timeframe_context_ok`` if **present and false** blocks
      (4H/30m missing in context). If the key is absent, HTF is not
      hard-blocked (legacy scan files); re-scan to populate the key.

    News, relative volume, edge score, and watchlist are **not** inputs.

    * Missing 5m structure → ``structure_context_missing``
    * Missing 1m trigger → ``waiting_for_1m_trigger``
    * HTF not satisfied → ``structure_context_missing``
    * Missing required keys → ``ict_execution_flags_incomplete``
    """
    if "five_min_setup_found" not in item or "one_min_trigger_found" not in item:
        return False, [ICT_EXECUTION_FLAGS_INCOMPLETE]

    f5 = item.get("five_min_setup_found")
    o1 = item.get("one_min_trigger_found")
    if f5 is not True:
        return False, [STRUCTURE_CONTEXT_MISSING]
    if o1 is not True:
        return False, [WAITING_FOR_1M_TRIGGER]

    htf = item.get("higher_timeframe_context_ok")
    if htf is False:
        return False, [STRUCTURE_CONTEXT_MISSING]
    return True, []


def is_day_trade_ready_category(item: Mapping[str, Any]) -> bool:
    """True if *signal_category* is STRICT or AGGRESSIVE (ICT intraday v1)."""
    cat = str(item.get("signal_category") or "").strip()
    return cat in {
        SIGNAL_DAY_TRADE_READY_STRICT,
        SIGNAL_DAY_TRADE_READY_AGGRESSIVE,
    }
