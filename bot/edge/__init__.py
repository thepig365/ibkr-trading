"""Ticker edge profiling for ict_smc_intraday_v1 (Prompt 13L-alt).

Contract (see also ``bot.execution.ict_paper_invariants`` / commit 2c7493d):

* Edge **never** creates paper eligibility. Execution requires a valid ICT
  scan row: ``DAY_TRADE_READY_*`` plus ``five_min_setup_found``,
  ``one_min_trigger_found``, and HTF flags (see
  :func:`bot.execution.ict_paper_invariants.validate_ict_chain_flags_for_paper`).
* This package may **only** rank, annotate, and apply mode / risk
  multipliers *after* the ICT chain passes in ``intraday_paper_execution``.
* Unknown profile: small STRICT paper risk only with valid ICT chain;
  aggressive generally requires a profile and ``strict_and_aggressive``-style
  permission plus ICT chain.
"""

from __future__ import annotations

from .eligibility import EdgePaperDecision, evaluate_edge_for_paper
from .paths import EDGE_PROFILE_DIR
from .reports import (
    latest_edge_profiles_path,
    load_edge_profiles_merged,
    load_profile_for_symbol,
    save_edge_profiles_artifacts,
)
from .ticker_edge import TickerEdgeProfile, build_ticker_edge_profile, profile_from_backtest_run
from .ranking import rank_profiles

__all__ = [
    "EDGE_PROFILE_DIR",
    "EdgePaperDecision",
    "TickerEdgeProfile",
    "build_ticker_edge_profile",
    "evaluate_edge_for_paper",
    "latest_edge_profiles_path",
    "load_edge_profiles_merged",
    "load_profile_for_symbol",
    "profile_from_backtest_run",
    "rank_profiles",
    "save_edge_profiles_artifacts",
]
