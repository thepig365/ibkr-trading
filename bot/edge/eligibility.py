"""Paper trading eligibility from ticker edge profiles (Prompt 13L-alt)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import AppConfig
from ..strategies.ict_smc_intraday.model import (
    SIGNAL_DAY_TRADE_READY_AGGRESSIVE,
    SIGNAL_DAY_TRADE_READY_STRICT,
)
from .reports import load_profile_for_symbol
from .ticker_edge import (
    REC_DISABLED,
    REC_STRICT_AND_AGGRESSIVE,
    REC_STRICT_ONLY,
    REC_WATCH_ONLY,
    TickerEdgeProfile,
)

UNKNOWN_POLICY_STRICT_SMALL = "allow_strict_small_risk"
UNKNOWN_POLICY_WATCH = "watch_only"
UNKNOWN_POLICY_BLOCK = "block_all"


@dataclass
class EdgePaperDecision:
    """Result of edge gate for one intraday paper candidate."""

    allow_submit: bool
    effective_risk_pct: float
    allow_strict: bool
    allow_aggressive: bool
    skip_reasons: list[str] = field(default_factory=list)
    profile: TickerEdgeProfile | None = None
    edge_audit: dict[str, Any] = field(default_factory=dict)

    @property
    def max_risk_multiplier_effective(self) -> float:
        m = self.edge_audit.get("max_risk_multiplier_effective")
        if isinstance(m, (int, float)):
            return float(m)
        return 1.0


def _policy(cfg: AppConfig) -> str:
    ip = cfg.settings.trading.intraday_paper
    return str(getattr(ip, "unknown_edge_policy", UNKNOWN_POLICY_STRICT_SMALL))


def _unknown_mult(cfg: AppConfig) -> float:
    ip = cfg.settings.trading.intraday_paper
    return float(getattr(ip, "unknown_edge_risk_multiplier", 0.25))


def _allow_aggr_no_profile(cfg: AppConfig) -> bool:
    ip = cfg.settings.trading.intraday_paper
    return bool(getattr(ip, "allow_aggressive_without_edge_profile", False))


def evaluate_edge_for_paper(
    cfg: AppConfig,
    symbol: str,
    signal_category: str,
    *,
    base_risk_pct: float,
    strategy_id: str = "ict_smc_intraday_v1",
) -> EdgePaperDecision:
    """Gate **risk and mode** using the latest edge profile.

    This never substitutes for the ICT/SMC scan: callers must only invoke
    this for symbols that already have ``DAY_TRADE_READY_*`` and pass
    :func:`bot.execution.ict_paper_invariants.validate_ict_chain_flags_for_paper`
    (5m / 1m / HTF flags). News, watchlist, or edge score alone must never
    open a paper order.
    """
    sym = (symbol or "").strip().upper()
    ip = cfg.settings.trading.intraday_paper
    enabled = bool(getattr(ip, "edge_profile_enabled", True))
    sk: list[str] = []
    audit: dict[str, Any] = {
        "edge_profile_enabled": enabled,
        "symbol": sym,
    }

    if not enabled:
        audit["edge_risk_multiplier_applied"] = False
        audit["max_risk_multiplier_effective"] = 1.0
        return EdgePaperDecision(
            allow_submit=True,
            effective_risk_pct=base_risk_pct,
            allow_strict=bool(ip.allow_strict_entries),
            allow_aggressive=bool(ip.allow_aggressive_entries),
            edge_audit=audit,
        )

    prof = load_profile_for_symbol(
        cfg.project_root, sym, strategy_id=strategy_id
    )
    audit["profile_found"] = prof is not None
    is_strict = signal_category == SIGNAL_DAY_TRADE_READY_STRICT
    is_aggr = signal_category == SIGNAL_DAY_TRADE_READY_AGGRESSIVE

    if prof is None:
        audit["edge_profile_missing"] = True
        pol = _policy(cfg)
        um = _unknown_mult(cfg)
        if pol == UNKNOWN_POLICY_BLOCK:
            sk.append("edge_profile_missing")
            return EdgePaperDecision(
                allow_submit=False,
                effective_risk_pct=0.0,
                allow_strict=False,
                allow_aggressive=False,
                skip_reasons=sk,
                edge_audit=audit,
            )
        if pol == UNKNOWN_POLICY_WATCH:
            sk.append("edge_recommended_mode_watch_only")
            return EdgePaperDecision(
                allow_submit=False,
                effective_risk_pct=0.0,
                allow_strict=False,
                allow_aggressive=False,
                skip_reasons=sk,
                edge_audit=audit,
            )
        # allow_strict_small_risk (default)
        eff = base_risk_pct * um
        audit["edge_risk_multiplier_applied"] = True
        audit["max_risk_multiplier_effective"] = um
        if is_aggr:
            if not _allow_aggr_no_profile(cfg) or not ip.allow_aggressive_entries:
                return EdgePaperDecision(
                    allow_submit=False,
                    effective_risk_pct=0.0,
                    allow_strict=False,
                    allow_aggressive=False,
                    skip_reasons=["edge_profile_missing"],
                    edge_audit=audit,
                )
            return EdgePaperDecision(
                allow_submit=True,
                effective_risk_pct=eff,
                allow_strict=False,
                allow_aggressive=True,
                edge_audit=audit,
            )
        if is_strict and ip.allow_strict_entries:
            return EdgePaperDecision(
                allow_submit=True,
                effective_risk_pct=eff,
                allow_strict=True,
                allow_aggressive=False,
                edge_audit=audit,
            )
        return EdgePaperDecision(
            allow_submit=False,
            effective_risk_pct=0.0,
            allow_strict=False,
            allow_aggressive=False,
            skip_reasons=["not_eligible_signal"],
            edge_audit=audit,
        )

    # Have profile
    audit.update(
        {
            "edge_score": prof.edge_score,
            "confidence_level": prof.confidence_level,
            "recommended_mode": prof.recommended_mode,
            "profile_max_risk_multiplier": prof.max_risk_multiplier,
        }
    )
    mrm = float(prof.max_risk_multiplier)
    mode = prof.recommended_mode
    base_eff = base_risk_pct * mrm
    audit["edge_risk_multiplier_applied"] = mrm < 1.0 - 1e-9
    audit["max_risk_multiplier_effective"] = mrm

    if mode == REC_DISABLED:
        sk.append("edge_recommended_mode_disabled")
        return EdgePaperDecision(
            allow_submit=False,
            effective_risk_pct=0.0,
            allow_strict=False,
            allow_aggressive=False,
            skip_reasons=sk,
            profile=prof,
            edge_audit=audit,
        )
    if mode == REC_WATCH_ONLY:
        sk.append("edge_recommended_mode_watch_only")
        return EdgePaperDecision(
            allow_submit=False,
            effective_risk_pct=0.0,
            allow_strict=True,
            allow_aggressive=False,
            skip_reasons=sk,
            profile=prof,
            edge_audit=audit,
        )
    if mode == REC_STRICT_ONLY:
        if is_aggr:
            sk.append("edge_strict_only_blocks_aggressive")
        allow_s = bool(is_strict and ip.allow_strict_entries)
        allow_a = False
        return EdgePaperDecision(
            allow_submit=allow_s and is_strict and not sk,
            effective_risk_pct=base_eff if allow_s and is_strict else base_risk_pct,
            allow_strict=allow_s,
            allow_aggressive=allow_a,
            skip_reasons=sk,
            profile=prof,
            edge_audit=audit,
        )
    # strict_and_aggressive
    if is_aggr and not ip.allow_aggressive_entries:
        sk.append("config allow_aggressive_entries=false")
    if is_strict and not ip.allow_strict_entries:
        sk.append("config allow_strict_entries=false")
    allow_s = is_strict and ip.allow_strict_entries
    allow_a = is_aggr and ip.allow_aggressive_entries
    ok = (allow_s or allow_a) and not sk
    return EdgePaperDecision(
        allow_submit=ok,
        effective_risk_pct=base_eff if ok else base_risk_pct,
        allow_strict=allow_s,
        allow_aggressive=allow_a,
        skip_reasons=sk,
        profile=prof,
        edge_audit=audit,
    )
