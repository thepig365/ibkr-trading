"""Deterministic tests for bot.market_regime."""

from __future__ import annotations

import pytest

from bot.market_regime import (
    MarketInputs,
    classify_regime,
    regime_is_defensive,
)


def test_crisis_when_vix_at_or_above_30() -> None:
    assert classify_regime(MarketInputs(vix=30.0, spy=400, spy_200ma=380)) == "crisis"
    assert classify_regime(MarketInputs(vix=45.0, spy=400, spy_200ma=380)) == "crisis"


def test_elevated_vol_when_vix_in_20_30() -> None:
    assert (
        classify_regime(MarketInputs(vix=20.0, spy=400, spy_200ma=380))
        == "elevated_vol"
    )
    assert (
        classify_regime(MarketInputs(vix=29.99, spy=400, spy_200ma=380))
        == "elevated_vol"
    )


def test_risk_off_on_term_structure_inversion() -> None:
    # VIX / VIX3M >= 1.0 even when absolute VIX is modest.
    r = classify_regime(
        MarketInputs(vix=18.0, vix3m=17.0, spy=400, spy_200ma=390)
    )
    assert r == "risk_off"


def test_risk_off_when_spy_below_200ma() -> None:
    r = classify_regime(
        MarketInputs(vix=16.0, vix3m=19.0, spy=380, spy_200ma=400)
    )
    assert r == "risk_off"


def test_risk_on_requires_low_vix_and_uptrend() -> None:
    r = classify_regime(
        MarketInputs(vix=13.0, vix3m=18.0, spy=450, spy_200ma=420)
    )
    assert r == "risk_on"


def test_neutral_between_thresholds() -> None:
    r = classify_regime(
        MarketInputs(vix=17.0, vix3m=20.0, spy=410, spy_200ma=400)
    )
    assert r == "neutral"


def test_unknown_when_required_input_missing() -> None:
    assert classify_regime(MarketInputs()) == "unknown"
    assert classify_regime(MarketInputs(vix=15.0)) == "unknown"
    assert (
        classify_regime(MarketInputs(vix=15.0, spy=400))  # missing 200MA
        == "unknown"
    )


def test_defensive_helper_covers_spec_labels() -> None:
    assert regime_is_defensive("crisis")
    assert regime_is_defensive("risk_off")
    assert regime_is_defensive("unknown")
    assert not regime_is_defensive("risk_on")
    assert not regime_is_defensive("neutral")
    assert not regime_is_defensive("elevated_vol")


@pytest.mark.parametrize(
    "inputs,expected",
    [
        (MarketInputs(vix=10.0, vix3m=15.0, spy=500, spy_200ma=480), "risk_on"),
        (MarketInputs(vix=35.0, vix3m=25.0, spy=500, spy_200ma=480), "crisis"),
        (MarketInputs(vix=22.0, vix3m=22.0, spy=500, spy_200ma=480), "elevated_vol"),
    ],
)
def test_parametrised_matrix(inputs: MarketInputs, expected: str) -> None:
    assert classify_regime(inputs) == expected


# ---------------------------------------------------------------------------
# Trend-only fallback (Prompt 4): VIX is missing but SPY/QQQ are not.
# ---------------------------------------------------------------------------
def test_trend_only_neutral_when_spy_above_200ma() -> None:
    r = classify_regime(MarketInputs(spy=500, spy_200ma=450))
    assert r == "neutral"


def test_trend_only_risk_off_when_spy_below_200ma() -> None:
    r = classify_regime(MarketInputs(spy=400, spy_200ma=450))
    assert r == "risk_off"


def test_trend_only_risk_off_when_qqq_below_200ma() -> None:
    r = classify_regime(MarketInputs(qqq=350, qqq_200ma=400))
    assert r == "risk_off"


def test_trend_only_neutral_via_qqq_only() -> None:
    r = classify_regime(MarketInputs(qqq=410, qqq_200ma=400))
    assert r == "neutral"


def test_unknown_when_no_vix_and_no_trend_data() -> None:
    assert classify_regime(MarketInputs()) == "unknown"
    # SPY price without 200MA is not a usable trend reference.
    assert classify_regime(MarketInputs(spy=500)) == "unknown"
    assert classify_regime(MarketInputs(qqq=400)) == "unknown"


def test_qqq_below_200ma_can_force_risk_off_even_with_vix() -> None:
    # VIX is mild and SPY is above its 200MA, but QQQ is below its
    # 200MA -> risk_off (per the new "broader trend" rule in
    # classify_regime).
    r = classify_regime(
        MarketInputs(vix=15.0, vix3m=18.0, spy=500, spy_200ma=480, qqq=380, qqq_200ma=400)
    )
    assert r == "risk_off"


# ---------------------------------------------------------------------------
# Prompt 8: full evaluator (confidence + flags + market_data schema)
# ---------------------------------------------------------------------------
from bot.market_regime import build_market_data, evaluate_regime  # noqa: E402


def _md(market: MarketInputs) -> dict[str, object]:
    """Convenience shortcut used by a handful of assertions below."""
    return build_market_data(market)


def test_evaluate_regime_schema_is_complete() -> None:
    ev = evaluate_regime(MarketInputs(vix=15.0, spy=500, spy_200ma=480))
    payload = ev.to_dict()
    assert set(payload.keys()) == {
        "market_regime",
        "regime_confidence",
        "new_positions_allowed",
        "research_scans_allowed",
        "reason",
        "market_data",
    }
    md = payload["market_data"]
    assert set(md.keys()) >= {
        "spy_close",
        "spy_200ma",
        "spy_above_200ma",
        "qqq_close",
        "qqq_200ma",
        "qqq_above_200ma",
        "vix",
        "vix3m",
        "vix_vix3m_ratio",
        "missing_fields",
    }


def test_vix_missing_but_spy_qqq_above_200ma_fallback_to_neutral_medium() -> None:
    ev = evaluate_regime(
        MarketInputs(spy=500, spy_200ma=480, qqq=410, qqq_200ma=400)
    )
    assert ev.market_regime == "neutral"
    assert ev.regime_confidence == "medium"
    assert ev.research_scans_allowed is True
    # Execution-side gate must still block when VIX is missing.
    assert ev.new_positions_allowed is False
    md = ev.market_data
    assert "VIX" in md["missing_fields"]
    assert "VIX3M" in md["missing_fields"]


def test_spy_and_qqq_below_200ma_returns_risk_off() -> None:
    ev = evaluate_regime(
        MarketInputs(spy=400, spy_200ma=450, qqq=350, qqq_200ma=400)
    )
    assert ev.market_regime == "risk_off"
    assert ev.new_positions_allowed is False


def test_crisis_when_vix_over_30() -> None:
    ev = evaluate_regime(
        MarketInputs(vix=35.0, vix3m=25.0, spy=400, spy_200ma=450)
    )
    assert ev.market_regime == "crisis"
    assert ev.new_positions_allowed is False


def test_elevated_vol_when_vix_over_20() -> None:
    ev = evaluate_regime(
        MarketInputs(vix=22.0, vix3m=22.0, spy=500, spy_200ma=480)
    )
    assert ev.market_regime == "elevated_vol"


def test_vix_term_structure_inversion_forces_risk_off() -> None:
    ev = evaluate_regime(
        MarketInputs(vix=18.0, vix3m=17.0, spy=500, spy_200ma=480)
    )
    assert ev.market_regime == "risk_off"


def test_unknown_when_no_critical_data() -> None:
    ev = evaluate_regime(MarketInputs())
    assert ev.market_regime == "unknown"
    assert ev.regime_confidence == "low"
    assert ev.new_positions_allowed is False
    assert ev.research_scans_allowed is False
    assert "no trend reference data available" in ev.reason


def test_missing_fields_lists_all_gaps() -> None:
    md = _md(MarketInputs(spy=500, spy_200ma=480))
    missing = set(md["missing_fields"])
    assert {"VIX", "VIX3M", "QQQ", "QQQ 200MA"}.issubset(missing)
    assert "SPY" not in missing
    assert "SPY 200MA" not in missing


def test_high_confidence_only_with_full_data() -> None:
    ev = evaluate_regime(
        MarketInputs(
            vix=14.0, vix3m=18.0,
            spy=500, spy_200ma=480,
            qqq=410, qqq_200ma=400,
        )
    )
    assert ev.regime_confidence == "high"


def test_config_allow_medium_for_research_can_be_disabled() -> None:
    ev = evaluate_regime(MarketInputs(spy=500))  # only SPY, no 200MA → low conf
    assert ev.regime_confidence == "low"
    # low confidence blocks research scans when the config flag is off.
    ev2 = evaluate_regime(
        MarketInputs(spy=500),
        {"allow_medium_confidence_for_research": False},
    )
    assert ev2.research_scans_allowed is False


def test_evaluate_regime_never_enables_execution_globally() -> None:
    # Even a picture-perfect regime: research and new_positions may be
    # allowed but the module does not expose an 'execution_allowed'
    # field. Execution gating lives entirely in the broker layer.
    ev = evaluate_regime(
        MarketInputs(
            vix=12.0, vix3m=20.0, spy=500, spy_200ma=480,
            qqq=410, qqq_200ma=400,
        ),
        {"allow_medium_confidence_for_new_positions": True},
    )
    payload = ev.to_dict()
    assert "execution_allowed" not in payload


def test_reason_mentions_fallback_when_vix_missing_but_trend_ok() -> None:
    ev = evaluate_regime(MarketInputs(spy=500, spy_200ma=480, qqq=410, qqq_200ma=400))
    assert "VIX/VIX3M unavailable" in ev.reason


def test_build_market_data_spy_above_flag() -> None:
    md = _md(MarketInputs(spy=500, spy_200ma=480, qqq=300, qqq_200ma=400))
    assert md["spy_above_200ma"] is True
    assert md["qqq_above_200ma"] is False
