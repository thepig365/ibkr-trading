"""Tests for ICT/SMC intraday paper bracket execution (Prompt 13F).

These tests guard the hard invariants every reviewer should care about:

* paper account only — never live
* every order is a LIMIT bracket (parent LIMIT + child STOP + child LIMIT)
* missing stop, missing target, or invalid bracket geometry hard-block
* duplicate same-symbol position / open order hard-block
* kill switch and runtime-OFF hard-block
* the audit log + state JSON are always written
* the Chinese Telegram digest is well-formed
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from bot.broker import Broker, LiveTradingBlocked, TradingDisabled
from bot.config import load_config
from bot.execution.intraday_paper_execution import (
    INTRADAY_AUTO_PAPER_ENABLED_RELPATH,
    INTRADAY_LOOP_STATE_RELPATH,
    KILL_SWITCH_RELPATH,
    PAPER_ORDERS_DIR,
    IntradayPaperIntent,
    IntradayPaperPassResult,
    IntradayPaperSubmissionResult,
    READY_AGGRESSIVE,
    READY_STRICT,
    build_intraday_paper_intent,
    format_intraday_paper_digest_zh,
    is_intraday_paper_runtime_enabled,
    submit_intraday_paper_bracket,
    validate_intraday_paper_intent,
    verify_intraday_paper_bracket_trades,
)
from bot.journal import Journal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enable_intraday_paper(
    project: Path,
    write_yaml,
    *,
    allow_aggressive: bool = True,
    allow_strict: bool = True,
    allow_shorting: bool = False,
    fully_automatic: bool = True,
    require_recon: bool = False,
    dry_run: bool = True,
    enabled: bool = True,
) -> None:
    p = project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s.setdefault("trading", {})
    s["trading"]["enabled"] = True
    s["trading"]["allow_shorting"] = allow_shorting
    ip = s["trading"].setdefault("intraday_paper", {})
    ip["enabled"] = enabled
    ip["fully_automatic"] = fully_automatic
    ip["allow_strict_entries"] = allow_strict
    ip["allow_aggressive_entries"] = allow_aggressive
    ip["require_reconciliation_pass"] = require_recon
    ip["dry_run"] = dry_run
    s.setdefault("account", {})
    s["account"]["mode"] = "paper"
    s["account"]["block_live_trading"] = True
    write_yaml(p, s)


def _strict_long_scan(symbol: str = "AAPL") -> dict:
    return {
        "symbol": symbol,
        "signal_category": READY_STRICT,
        "direction": "long",
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
        "explanation_zh": "测试",
    }


def _aggressive_short_scan(symbol: str = "TSLA") -> dict:
    return {
        "symbol": symbol,
        "signal_category": READY_AGGRESSIVE,
        "direction": "short",
        "entry": 200.0,
        "stop": 202.0,
        "target": 196.0,
    }


def _broker_state_paper_clean() -> dict[str, Any]:
    return {
        "account_mode": "paper",
        "block_live_trading": True,
        "kill_switch_active": False,
        "runtime_intraday_on": True,
        "reconciliation_passed": True,
        "net_liquidation": 100_000.0,
        "positions": [],
        "open_orders": [],
        "open_positions_count": 0,
    }


# ---------------------------------------------------------------------------
# Runtime flag helpers
# ---------------------------------------------------------------------------


def test_runtime_flag_helpers_read_canonical_path(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    on, off = is_intraday_paper_runtime_enabled(cfg)
    assert on is False and off is False  # missing file => fallback
    p = tmp_project / INTRADAY_AUTO_PAPER_ENABLED_RELPATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("1\n", encoding="utf-8")
    on, off = is_intraday_paper_runtime_enabled(cfg)
    assert on is True and off is False
    p.write_text("0\n", encoding="utf-8")
    on, off = is_intraday_paper_runtime_enabled(cfg)
    assert on is False and off is True


def test_runtime_flag_paths_are_canonical_constants() -> None:
    assert KILL_SWITCH_RELPATH == "data/KILL_SWITCH"
    assert (
        INTRADAY_AUTO_PAPER_ENABLED_RELPATH
        == "data/runtime/intraday_auto_paper_enabled"
    )
    assert (
        INTRADAY_LOOP_STATE_RELPATH
        == "data/runtime/intraday_auto_paper_loop_state.json"
    )
    assert PAPER_ORDERS_DIR == "data/paper_orders"


# ---------------------------------------------------------------------------
# build_intraday_paper_intent
# ---------------------------------------------------------------------------


def test_build_intent_long_strict_ok(tmp_project: Path, write_yaml) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    intent, reasons = build_intraday_paper_intent(
        _strict_long_scan(), {"net_liquidation": 100_000.0, "mode": "paper"}, cfg,
    )
    assert reasons == []
    assert intent is not None
    assert intent.direction == "long"
    assert intent.signal_category == READY_STRICT
    assert intent.entry_price == 100.0
    assert intent.stop_price == 99.0
    assert intent.target_price == 102.0
    assert intent.planned_rr == pytest.approx(2.0, rel=1e-3)
    assert intent.quantity >= 1
    assert intent.order_type == "LIMIT_BRACKET"
    assert intent.paper_only is True
    assert intent.live_trading_allowed is False


def test_build_intent_short_requires_correct_geometry(
    tmp_project: Path, write_yaml,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml, allow_shorting=True)
    cfg = load_config(project_root=tmp_project)
    item = _aggressive_short_scan()
    intent, reasons = build_intraday_paper_intent(
        item, {"net_liquidation": 100_000.0}, cfg,
    )
    assert reasons == []
    assert intent is not None
    assert intent.direction == "short"
    # invert: stop above entry, target below
    bad = dict(item)
    bad["stop"] = 195.0  # stop below entry now → invalid for short
    intent2, reasons2 = build_intraday_paper_intent(
        bad, {"net_liquidation": 100_000.0}, cfg,
    )
    assert intent2 is None
    assert any("short bracket invalid" in r for r in reasons2)


def test_build_intent_rejects_unknown_signal_category(
    tmp_project: Path, write_yaml,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    item = _strict_long_scan()
    item["signal_category"] = "ALMOST_READY"
    intent, reasons = build_intraday_paper_intent(
        item, {"net_liquidation": 100_000.0}, cfg,
    )
    assert intent is None
    assert any("not paper-eligible" in r for r in reasons)


def test_build_intent_rejects_aggressive_when_disabled(
    tmp_project: Path, write_yaml,
) -> None:
    _enable_intraday_paper(
        tmp_project, write_yaml, allow_aggressive=False, allow_shorting=True,
    )
    cfg = load_config(project_root=tmp_project)
    item = _aggressive_short_scan()
    intent, reasons = build_intraday_paper_intent(
        item, {"net_liquidation": 100_000.0}, cfg,
    )
    assert intent is None
    assert any("allow_aggressive_entries=false" in r for r in reasons)


def test_build_intent_min_rr_blocks_low_rr(tmp_project: Path, write_yaml) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    item = _strict_long_scan()
    item["target"] = 100.5  # R/R = 0.5
    intent, reasons = build_intraday_paper_intent(
        item, {"net_liquidation": 100_000.0}, cfg,
    )
    assert intent is None
    assert any("below min_rr" in r for r in reasons)


def test_build_intent_requires_account_equity(tmp_project: Path, write_yaml) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    intent, reasons = build_intraday_paper_intent(_strict_long_scan(), {}, cfg)
    assert intent is None
    assert any("equity" in r.lower() for r in reasons)


# ---------------------------------------------------------------------------
# validate_intraday_paper_intent
# ---------------------------------------------------------------------------


def _intent_long(symbol: str = "AAPL", qty: int = 10) -> IntradayPaperIntent:
    return IntradayPaperIntent(
        strategy_id="ict_smc_intraday_v1",
        symbol=symbol,
        direction="long",
        signal_category=READY_STRICT,
        entry_price=100.0,
        stop_price=99.0,
        target_price=102.0,
        planned_rr=2.0,
        quantity=qty,
        risk_amount=qty * 1.0,
        risk_per_trade_pct=0.10,
    )


def _intent_short(symbol: str = "TSLA", qty: int = 5) -> IntradayPaperIntent:
    return IntradayPaperIntent(
        strategy_id="ict_smc_intraday_v1",
        symbol=symbol,
        direction="short",
        signal_category=READY_AGGRESSIVE,
        entry_price=200.0,
        stop_price=202.0,
        target_price=196.0,
        planned_rr=2.0,
        quantity=qty,
        risk_amount=qty * 2.0,
        risk_per_trade_pct=0.10,
    )


def test_validate_blocks_live_account(tmp_project: Path, write_yaml) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    state = _broker_state_paper_clean()
    state["account_mode"] = "live"
    state["block_live_trading"] = False
    ok, reasons = validate_intraday_paper_intent(_intent_long(), state, cfg)
    assert ok is False
    joined = " ".join(reasons).lower()
    assert "not paper" in joined
    assert "block_live_trading" in joined


def test_validate_blocks_kill_switch(tmp_project: Path, write_yaml) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    state = _broker_state_paper_clean()
    state["kill_switch_active"] = True
    ok, reasons = validate_intraday_paper_intent(_intent_long(), state, cfg)
    assert ok is False
    assert any("kill switch" in r.lower() for r in reasons)


def test_validate_blocks_runtime_off(tmp_project: Path, write_yaml) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    state = _broker_state_paper_clean()
    state["runtime_intraday_on"] = False
    ok, reasons = validate_intraday_paper_intent(_intent_long(), state, cfg)
    assert ok is False
    assert any("runtime flag is OFF" in r for r in reasons)


def test_validate_blocks_duplicate_position(tmp_project: Path, write_yaml) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    state = _broker_state_paper_clean()
    state["positions"] = [{"symbol": "AAPL", "position": 50}]
    ok, reasons = validate_intraday_paper_intent(_intent_long(), state, cfg)
    assert ok is False
    assert any("duplicate paper entry" in r and "AAPL" in r for r in reasons)


def test_validate_blocks_duplicate_open_order(tmp_project: Path, write_yaml) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    state = _broker_state_paper_clean()

    @dataclass
    class _O:
        symbol: str = "AAPL"

    state["open_orders"] = [_O()]
    ok, reasons = validate_intraday_paper_intent(_intent_long(), state, cfg)
    assert ok is False
    assert any("open order exists" in r for r in reasons)


def test_validate_blocks_reconciliation_fail_when_required(
    tmp_project: Path, write_yaml,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml, require_recon=True)
    cfg = load_config(project_root=tmp_project)
    state = _broker_state_paper_clean()
    state["reconciliation_passed"] = False
    ok, reasons = validate_intraday_paper_intent(_intent_long(), state, cfg)
    assert ok is False
    assert any("reconciliation failed" in r for r in reasons)


def test_validate_blocks_short_when_allow_shorting_off(
    tmp_project: Path, write_yaml,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml, allow_shorting=False)
    cfg = load_config(project_root=tmp_project)
    state = _broker_state_paper_clean()
    ok, reasons = validate_intraday_paper_intent(_intent_short(), state, cfg)
    assert ok is False
    assert any("allow_shorting=false" in r for r in reasons)


def test_validate_blocks_invalid_long_geometry(tmp_project: Path, write_yaml) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    bad = IntradayPaperIntent(
        strategy_id="x",
        symbol="AAPL",
        direction="long",
        signal_category=READY_STRICT,
        entry_price=100.0,
        stop_price=101.0,  # invalid: stop above entry
        target_price=102.0,
        planned_rr=1.5,
        quantity=10,
        risk_amount=10.0,
        risk_per_trade_pct=0.1,
    )
    ok, reasons = validate_intraday_paper_intent(
        bad, _broker_state_paper_clean(), cfg,
    )
    assert ok is False
    assert any("long bracket: require stop < entry < target" in r for r in reasons)


def test_validate_blocks_short_geometry_violation(
    tmp_project: Path, write_yaml,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml, allow_shorting=True)
    cfg = load_config(project_root=tmp_project)
    bad = IntradayPaperIntent(
        strategy_id="x",
        symbol="TSLA",
        direction="short",
        signal_category=READY_AGGRESSIVE,
        entry_price=200.0,
        stop_price=199.0,  # invalid: stop below entry for short
        target_price=196.0,
        planned_rr=1.5,
        quantity=5,
        risk_amount=5.0,
        risk_per_trade_pct=0.1,
    )
    ok, reasons = validate_intraday_paper_intent(
        bad, _broker_state_paper_clean(), cfg,
    )
    assert ok is False
    assert any("short bracket: require target < entry < stop" in r for r in reasons)


def test_validate_blocks_zero_quantity(tmp_project: Path, write_yaml) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    ok, reasons = validate_intraday_paper_intent(
        _intent_long(qty=0), _broker_state_paper_clean(), cfg,
    )
    assert ok is False
    assert any("quantity rounds to 0" in r for r in reasons)


def test_validate_blocks_max_concurrent_positions(
    tmp_project: Path, write_yaml,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s["trading"]["intraday_paper"]["max_concurrent_positions"] = 2
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    state = _broker_state_paper_clean()
    state["open_positions_count"] = 5
    ok, reasons = validate_intraday_paper_intent(_intent_long(), state, cfg)
    assert ok is False
    assert any("max_concurrent_positions reached" in r for r in reasons)


def test_validate_passes_when_clean(tmp_project: Path, write_yaml) -> None:
    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    ok, reasons = validate_intraday_paper_intent(
        _intent_long(), _broker_state_paper_clean(), cfg,
    )
    assert ok is True, reasons
    assert reasons == []


# ---------------------------------------------------------------------------
# submit_intraday_paper_bracket — broker is mocked / dry-run
# ---------------------------------------------------------------------------


def _make_broker_with_fake_ib(cfg) -> tuple[Broker, MagicMock]:
    """Mirror the pattern in tests/test_mtf_paper.py."""
    fake_ib = MagicMock()
    contract = MagicMock()
    fake_ib.qualifyContracts = MagicMock(return_value=[contract])
    fo, so, to = MagicMock(), MagicMock(), MagicMock()
    for o, oid in ((fo, 1), (so, 2), (to, 3)):
        o.orderId = oid
    fake_ib.bracketOrder = MagicMock(return_value=(fo, so, to))
    fake_ib.placeOrder = MagicMock()

    def _mk_trade(oid: int, status: str = "Submitted", log: list | None = None) -> MagicMock:
        tr = MagicMock()
        tr.order.orderId = oid
        tr.orderStatus.status = status
        tr.log = log or []
        return tr

    fake_ib.trades = MagicMock(
        return_value=[
            _mk_trade(1, "Submitted"),
            _mk_trade(2, "Submitted"),
            _mk_trade(3, "Submitted"),
        ]
    )
    fake_ib.sleep = MagicMock()
    client = MagicMock()
    client._ib = fake_ib
    client.is_connected = True
    client.fetch_stock_min_tick = MagicMock(
        return_value={
            "min_tick": Decimal("0.01"),
            "min_tick_source": "contract_details",
            "min_tick_fetch_error": None,
        }
    )
    broker = Broker(cfg, client=client, journal=None)
    return broker, fake_ib


def test_submit_returns_dry_run_skip_when_dry_run_true(
    tmp_project: Path, write_yaml,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml, dry_run=True)
    cfg = load_config(project_root=tmp_project)
    broker, fake_ib = _make_broker_with_fake_ib(cfg)
    sub = submit_intraday_paper_bracket(
        _intent_long(), _broker_state_paper_clean(), cfg, broker=broker,
    )
    assert sub.submitted is False
    assert sub.submitted_to_broker is False
    assert sub.skipped_reasons == ["dry-run"]
    fake_ib.placeOrder.assert_not_called()
    fake_ib.bracketOrder.assert_not_called()


def test_submit_places_bracket_when_dry_run_false(
    tmp_project: Path, write_yaml,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml, dry_run=False)
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s["trading"]["require_manual_confirmation"] = True
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    broker, fake_ib = _make_broker_with_fake_ib(cfg)
    sub = submit_intraday_paper_bracket(
        _intent_long(), _broker_state_paper_clean(), cfg, broker=broker,
    )
    assert sub.submitted is True
    assert sub.submitted_to_broker is True
    assert sub.bracket_integrity == "complete"
    assert sub.order_ids == [1, 2, 3]
    assert fake_ib.bracketOrder.call_count == 1
    args, _ = fake_ib.bracketOrder.call_args
    side, qty, entry, target, stop = args
    assert side == "BUY"
    # Quantity is recomputed from equity + risk% after tick normalization.
    assert qty == 100.0
    assert entry == 100.0 and target == 102.0 and stop == 99.0
    assert fake_ib.placeOrder.call_count == 3


def test_submit_skipped_returns_validation_reasons_without_calling_broker(
    tmp_project: Path, write_yaml,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml, dry_run=False)
    cfg = load_config(project_root=tmp_project)
    broker, fake_ib = _make_broker_with_fake_ib(cfg)
    state = _broker_state_paper_clean()
    state["kill_switch_active"] = True
    sub = submit_intraday_paper_bracket(
        _intent_long(), state, cfg, broker=broker,
    )
    assert sub.submitted is False
    assert any("kill switch" in r.lower() for r in sub.skipped_reasons)
    fake_ib.placeOrder.assert_not_called()


def test_broker_blocks_intraday_paper_when_config_disabled(
    tmp_project: Path, write_yaml,
) -> None:
    """Even if the execution module's validators were bypassed, the broker
    layer must still refuse when ``intraday_paper.enabled=false``."""
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s.setdefault("trading", {})["enabled"] = True
    s["trading"].setdefault("intraday_paper", {})["enabled"] = False
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    broker, _ = _make_broker_with_fake_ib(cfg)
    intent = _intent_long().to_trade_intent()
    with pytest.raises(TradingDisabled):
        broker.place_order(intent, intraday_paper_bracket=True)


def test_broker_blocks_intraday_paper_when_account_live(
    tmp_project: Path, write_yaml,
) -> None:
    """If account.mode=live, even an enabled intraday_paper config can't
    save us — the broker hard-blocks."""
    _enable_intraday_paper(tmp_project, write_yaml, dry_run=False)
    p = tmp_project / "config" / "settings.yaml"
    s = yaml.safe_load(p.read_text())
    s["account"]["mode"] = "live"
    s["account"]["block_live_trading"] = True
    write_yaml(p, s)
    cfg = load_config(project_root=tmp_project)
    broker, _ = _make_broker_with_fake_ib(cfg)
    intent = _intent_long().to_trade_intent()
    with pytest.raises(LiveTradingBlocked):
        broker.place_order(intent, intraday_paper_bracket=True)


def test_broker_intraday_paper_rejects_short_when_disallowed(
    tmp_project: Path, write_yaml,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml, dry_run=False, allow_shorting=False)
    cfg = load_config(project_root=tmp_project)
    broker, _ = _make_broker_with_fake_ib(cfg)
    intent = _intent_short().to_trade_intent()
    with pytest.raises(TradingDisabled):
        broker.place_order(intent, intraday_paper_bracket=True)


def test_broker_intraday_paper_rejects_missing_stop_or_target(
    tmp_project: Path, write_yaml,
) -> None:
    _enable_intraday_paper(tmp_project, write_yaml, dry_run=False)
    cfg = load_config(project_root=tmp_project)
    broker, _ = _make_broker_with_fake_ib(cfg)
    base = _intent_long().to_trade_intent()
    no_stop = base.__class__(
        symbol=base.symbol,
        sec_type=base.sec_type,
        side=base.side,
        quantity=base.quantity,
        estimated_price=base.estimated_price,
        entry_limit_price=base.entry_limit_price,
        take_profit_price=base.take_profit_price,
        stop_loss_price=None,
    )
    with pytest.raises(TradingDisabled):
        broker.place_order(no_stop, intraday_paper_bracket=True)


# ---------------------------------------------------------------------------
# Audit log + state file: written by run_intraday_paper_pass / submit
# ---------------------------------------------------------------------------


def test_audit_log_path_constructed_under_paper_orders_dir(
    tmp_project: Path, write_yaml,
) -> None:
    """Direct check that the audit row is appended JSONL under the canonical
    directory and that no live-trading flag is ever recorded."""
    from bot.execution.intraday_paper_execution import _record_submission_audit

    _enable_intraday_paper(tmp_project, write_yaml)
    cfg = load_config(project_root=tmp_project)
    intent = _intent_long()
    sub = IntradayPaperSubmissionResult(
        symbol=intent.symbol,
        submitted=True,
        submitted_to_broker=True,
        bracket_integrity="complete",
        intent=intent,
        order_ids=[101, 102, 103],
        tick_meta={
            "min_tick": "0.01",
            "min_tick_source": "contract_details",
            "original_entry": 100.0,
        },
    )
    p = _record_submission_audit(cfg, sub)
    assert p
    assert (tmp_project / PAPER_ORDERS_DIR).is_dir()
    row = json.loads(Path(p).read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["paper_only"] is True
    assert row["live_trading_allowed"] is False
    assert row["symbol"] == "AAPL"
    assert row["order_ids"] == [101, 102, 103]
    assert row["submitted"] is True
    assert row.get("min_tick") == "0.01"
    assert row.get("bracket_integrity") == "complete"


# ---------------------------------------------------------------------------
# run_intraday_paper_pass: skip paths (no broker connect needed)
# ---------------------------------------------------------------------------


def test_pass_skips_when_kill_switch_active(tmp_project: Path, write_yaml) -> None:
    from bot.execution.intraday_paper_execution import run_intraday_paper_pass

    _enable_intraday_paper(tmp_project, write_yaml)
    (tmp_project / KILL_SWITCH_RELPATH).write_text("on\n", encoding="utf-8")
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    result = run_intraday_paper_pass(cfg, journal, telegram=False)
    assert isinstance(result, IntradayPaperPassResult)
    assert result.kill_switch is True
    assert any("kill switch" in r.lower() for r in result.skipped_reasons)
    # State file must be written even on skip.
    state_p = tmp_project / INTRADAY_LOOP_STATE_RELPATH
    assert state_p.exists()
    body = json.loads(state_p.read_text(encoding="utf-8"))
    assert body["paper_only"] is True
    assert body["kill_switch"] is True


def test_pass_skips_when_runtime_off_and_not_fully_automatic(
    tmp_project: Path, write_yaml,
) -> None:
    from bot.execution.intraday_paper_execution import run_intraday_paper_pass

    _enable_intraday_paper(tmp_project, write_yaml, fully_automatic=False)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    result = run_intraday_paper_pass(cfg, journal, telegram=False)
    assert any(
        "runtime intraday flag" in r.lower() or "runtime flag" in r.lower()
        for r in result.skipped_reasons
    )


def test_pass_skips_when_intraday_paper_disabled_in_config(
    tmp_project: Path, write_yaml,
) -> None:
    from bot.execution.intraday_paper_execution import run_intraday_paper_pass

    _enable_intraday_paper(
        tmp_project, write_yaml, enabled=False, fully_automatic=False,
    )
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    result = run_intraday_paper_pass(cfg, journal, telegram=False)
    assert any("intraday_paper.enabled=false" in r for r in result.skipped_reasons)


# ---------------------------------------------------------------------------
# Telegram digest (Chinese)
# ---------------------------------------------------------------------------


def test_format_intraday_paper_digest_zh_contains_chinese_keywords() -> None:
    intent = _intent_long()
    sub = IntradayPaperSubmissionResult(
        symbol="AAPL",
        submitted=True,
        intent=intent,
        order_ids=[1, 2, 3],
    )
    result = IntradayPaperPassResult(
        timestamp_utc="2026-04-25T13:00:00Z",
        paper_only=True,
        runtime_intraday_on=True,
        kill_switch=False,
        reconciliation_status="passed",
        config_enabled=True,
        fully_automatic=True,
        symbols_scanned=["AAPL"],
        strict_ready_count=1,
        aggressive_ready_count=0,
        submissions=[sub],
        last_status="ok",
        last_reason="submitted=1/1",
    )
    text = format_intraday_paper_digest_zh(result)
    for needle in [
        "ICT/SMC",
        "纸面",
        "扫描数",
        "已提交",
        "对账",
        "仅纸面账户",
        "AAPL",
    ]:
        assert needle in text, f"digest missing {needle!r}"
    assert "实盘" not in text or "不会触发实盘" in text


def test_verify_intraday_error_110_marks_incomplete() -> None:
    fake_ib = MagicMock()

    def _mk(oid: int, status: str, log: list | None = None) -> MagicMock:
        tr = MagicMock()
        tr.order.orderId = oid
        tr.orderStatus.status = status
        tr.log = log or []
        return tr

    le = MagicMock()
    le.errorCode = 110
    le.message = "min tick"
    fake_ib.trades = MagicMock(
        return_value=[
            _mk(1, "Submitted"),
            _mk(2, "Submitted"),
            _mk(3, "Cancelled", [le]),
        ]
    )
    fake_ib.sleep = MagicMock()
    r = verify_intraday_paper_bracket_trades(fake_ib, [1, 2, 3], timeout=0.5)
    assert r["bracket_integrity"] == "incomplete"
    assert r["bracket_protected"] is False
    assert 110 in r["broker_error_codes"]


def test_verify_all_legs_submitted_marks_complete() -> None:
    fake_ib = MagicMock()

    def _mk(oid: int, status: str = "Submitted") -> MagicMock:
        tr = MagicMock()
        tr.order.orderId = oid
        tr.orderStatus.status = status
        tr.log = []
        return tr

    fake_ib.trades = MagicMock(
        return_value=[_mk(1), _mk(2), _mk(3)]
    )
    fake_ib.sleep = MagicMock()
    r = verify_intraday_paper_bracket_trades(fake_ib, [1, 2, 3], timeout=0.4)
    assert r["bracket_integrity"] == "complete"
    assert r["bracket_protected"] is True
