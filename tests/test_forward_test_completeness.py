"""Forward-test catalog checks (13FORWARD): config + strategy defaults."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent


def test_default_paper_strategy_is_ict_smc_intraday() -> None:
    raw = yaml.safe_load((REPO / "config" / "strategy_ui.yaml").read_text(encoding="utf-8"))
    assert raw.get("default_strategy") == "ict_smc_intraday_v1"
    ict = (raw.get("strategies") or {}).get("ict_smc_intraday_v1") or {}
    assert ict.get("paper_enabled") is True
    mtf = (raw.get("strategies") or {}).get("mtf_smc") or {}
    assert mtf.get("paper_enabled") is False
