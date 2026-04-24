"""Tests for :mod:`bot.review_queue` and the ``smc-review-queue`` CLI.

All tests assert the research/dry-run safety invariants alongside the
expected behaviour: ``execution_allowed`` is ``False``, ``research_only``
is ``True``, and ``broker.place_order`` is never reachable from the
review-queue code path.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from bot import review_queue as rq_mod
from bot.review_queue import (
    DEFAULT_THRESHOLDS,
    ReviewQueue,
    SummaryNotFoundError,
    build_review_queue,
    classify_review_category,
    format_markdown,
    format_telegram_digest,
    human_review_reason,
    load_latest_summary,
    review_priority_score,
    save_review_queue,
)


# ---------------------------------------------------------------------------
# Row fixtures
# ---------------------------------------------------------------------------
def _base_row(**kw) -> dict:
    row: dict = {
        "symbol": "XXX",
        "bucket": "WATCH_NOW",
        "smc_quality_score": 80,
        "approved_for_dry_run": False,
        "execution_allowed": False,
        "market_regime": "neutral",
        "sweep": True,
        "choch": True,
        "fvg": True,
        "order_block": True,
        "entry_price": 100.0,
        "structural_stop": 97.0,
        "target_1": 110.0,
        "risk_reward_to_target_1": 3.0,
        "stop_distance_pct": 3.0,
        "extension_pct_vs_latest_close": 0.5,
        "rejection_reasons": [],
        "chart_path": "/tmp/x.png",
        "candle_source": "csv",
    }
    row.update(kw)
    return row


def _summary(rows: list[dict], **envelope) -> dict:
    payload: dict = {
        "date": "2026-04-24",
        "timeframe": "daily",
        "symbols_scanned": len(rows),
        "market_regime": "neutral",
        "regime_confidence": "medium",
        "regime_missing_fields": ["VIX", "VIX3M"],
        "research_scans_allowed": True,
        "new_positions_allowed": False,
        "regime_source": "2026-04-24.json",
        "buckets": {"WATCH_NOW": rows},
        "bucket_counts": {"WATCH_NOW": len(rows)},
        "top_by_score": rows,
        "closest_to_entry": rows,
        "execution_allowed": False,
        "research_only": True,
    }
    payload.update(envelope)
    return payload


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def test_ready_for_manual_chart_review_when_all_gates_pass() -> None:
    row = _base_row()
    assert classify_review_category(row, DEFAULT_THRESHOLDS) == (
        "READY_FOR_MANUAL_CHART_REVIEW"
    )


def test_pullback_watch_when_only_extension_exceeds() -> None:
    row = _base_row(
        bucket="TOO_EXTENDED",
        extension_pct_vs_latest_close=6.62,
        rejection_reasons=["price_extended_from_entry_pct 6.62 > 3.00 (no chasing)"],
    )
    assert classify_review_category(row, DEFAULT_THRESHOLDS) == "PULLBACK_WATCH"


def test_invalid_risk_reject_when_stop_too_wide() -> None:
    row = _base_row(
        bucket="INVALID_RISK",
        stop_distance_pct=6.31,
        extension_pct_vs_latest_close=3.1,
        rejection_reasons=["stop_distance_pct 6.31 > max 5.00"],
    )
    assert classify_review_category(row, DEFAULT_THRESHOLDS) == "INVALID_RISK_REJECT"


def test_structure_watch_when_only_sweep() -> None:
    row = _base_row(
        bucket="STRUCTURE_INCOMPLETE",
        choch=False,
        fvg=False,
        order_block=False,
        entry_price=None,
        structural_stop=None,
        target_1=None,
        risk_reward_to_target_1=None,
        stop_distance_pct=None,
        extension_pct_vs_latest_close=None,
        rejection_reasons=["no_choch_after_sweep"],
    )
    assert classify_review_category(row, DEFAULT_THRESHOLDS) == "STRUCTURE_WATCH"


def test_blocked_by_regime_or_news() -> None:
    row = _base_row(
        bucket="BLOCKED",
        rejection_reasons=["market_regime=risk_off blocks new setups"],
    )
    assert classify_review_category(row, DEFAULT_THRESHOLDS) == (
        "BLOCKED_BY_REGIME_OR_NEWS"
    )


def test_ignore_for_now_when_nothing_interesting() -> None:
    row = _base_row(
        bucket="STRUCTURE_INCOMPLETE",
        sweep=False, choch=False, fvg=False, order_block=False,
        entry_price=None, structural_stop=None, target_1=None,
        risk_reward_to_target_1=None, stop_distance_pct=None,
        extension_pct_vs_latest_close=None,
        rejection_reasons=["insufficient_candles"],
        chart_path="",
    )
    assert classify_review_category(row, DEFAULT_THRESHOLDS) == "IGNORE_FOR_NOW"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def test_priority_score_clamps_between_0_and_100() -> None:
    good = _base_row()
    bad = _base_row(
        sweep=False, choch=False, fvg=False, order_block=False,
        target_1=None, risk_reward_to_target_1=None,
        stop_distance_pct=99.0, extension_pct_vs_latest_close=99.0,
        chart_path="",
        rejection_reasons=["market_regime=unknown blocks new setups"],
    )
    s_good = review_priority_score(good, DEFAULT_THRESHOLDS)
    s_bad = review_priority_score(bad, DEFAULT_THRESHOLDS)
    assert 0 <= s_good <= 100
    assert 0 <= s_bad <= 100
    assert s_good > s_bad


def test_priority_score_rewards_near_entry_setup() -> None:
    near = _base_row(extension_pct_vs_latest_close=1.0)
    extended = _base_row(
        extension_pct_vs_latest_close=7.0,
        rejection_reasons=["price_extended_from_entry_pct 7.00 > 3.00"],
    )
    assert (
        review_priority_score(near, DEFAULT_THRESHOLDS)
        > review_priority_score(extended, DEFAULT_THRESHOLDS)
    )


# ---------------------------------------------------------------------------
# Human reason
# ---------------------------------------------------------------------------
def test_human_reason_ready_mentions_manual_review_only_not_trade_signal() -> None:
    row = _base_row()
    reason = human_review_reason(
        row, "READY_FOR_MANUAL_CHART_REVIEW", DEFAULT_THRESHOLDS
    )
    assert "manual chart review" in reason.lower()
    # Safety phrasing: never call a candidate a "trade signal".
    assert "trade signal" not in reason.lower()


def test_human_reason_pullback_names_entry_zone() -> None:
    row = _base_row(
        extension_pct_vs_latest_close=6.62, entry_price=256.46,
    )
    reason = human_review_reason(row, "PULLBACK_WATCH", DEFAULT_THRESHOLDS)
    assert "256.46" in reason
    assert "do not chase" in reason.lower()


# ---------------------------------------------------------------------------
# Queue build + envelope
# ---------------------------------------------------------------------------
def test_queue_envelope_has_required_top_level_fields() -> None:
    row = _base_row()
    queue = build_review_queue(_summary([row]), source_path="path/to/summary.json")
    d = queue.to_dict()
    for k in (
        "date", "source_summary", "market_regime", "regime_confidence",
        "regime_missing_fields", "new_positions_allowed", "research_scans_allowed",
        "execution_allowed", "research_only", "counts", "items",
    ):
        assert k in d, f"missing key {k}"
    # Safety invariants
    assert d["execution_allowed"] is False
    assert d["research_only"] is True


def test_queue_tradeable_candidates_returns_ready_rows() -> None:
    ready = _base_row(symbol="AAA")
    extended = _base_row(
        symbol="BBB",
        bucket="TOO_EXTENDED",
        extension_pct_vs_latest_close=6.62,
        rejection_reasons=["price_extended_from_entry_pct 6.62 > 3.00"],
    )
    queue = build_review_queue(_summary([ready, extended]))
    trade = queue.tradeable_candidates()
    assert [t.symbol for t in trade] == ["AAA"]


def test_queue_build_matches_prompt_counts_from_real_summary() -> None:
    """Mirror the Prompt-9 example counts: 1 pullback, 6 invalid risk,
    13 structure watch, 0 blocked (and 0 ready)."""

    def _row(symbol: str, bucket: str, **kw) -> dict:
        return _base_row(symbol=symbol, bucket=bucket, **kw)

    aapl = _row(
        "AAPL", "TOO_EXTENDED",
        extension_pct_vs_latest_close=6.62,
        rejection_reasons=["price_extended_from_entry_pct 6.62 > 3.00"],
    )
    invalid_rows = [
        _row(
            f"INV{i}", "INVALID_RISK",
            stop_distance_pct=10.0,
            rejection_reasons=["stop_distance_pct 10.00 > max 5.00"],
        )
        for i in range(6)
    ]
    structure_rows = [
        _row(
            f"STR{i}", "STRUCTURE_INCOMPLETE",
            choch=False, fvg=False, order_block=False,
            entry_price=None, structural_stop=None, target_1=None,
            risk_reward_to_target_1=None, stop_distance_pct=None,
            extension_pct_vs_latest_close=None,
            rejection_reasons=["no_choch_after_sweep"],
        )
        for i in range(13)
    ]
    summary = _summary(
        [aapl, *invalid_rows, *structure_rows],
        buckets={
            "TOO_EXTENDED": [aapl],
            "INVALID_RISK": invalid_rows,
            "STRUCTURE_INCOMPLETE": structure_rows,
            "BLOCKED": [],
            "WATCH_NOW": [],
            "NEAR_ENTRY": [],
        },
    )
    queue = build_review_queue(summary, max_items=50)
    counts = queue.counts()
    assert counts["READY_FOR_MANUAL_CHART_REVIEW"] == 0
    assert counts["PULLBACK_WATCH"] == 1
    assert counts["INVALID_RISK_REJECT"] == 6
    assert counts["STRUCTURE_WATCH"] == 13
    assert counts["BLOCKED_BY_REGIME_OR_NEWS"] == 0


# ---------------------------------------------------------------------------
# Markdown / digest
# ---------------------------------------------------------------------------
def test_markdown_output_appends_to_memory_file(tmp_project: Path) -> None:
    row = _base_row()
    queue = build_review_queue(_summary([row]))
    from bot.config import load_config
    cfg = load_config(project_root=tmp_project)
    from bot.review_queue import append_markdown
    path = append_markdown(cfg, queue, top=5)
    assert path.exists()
    txt = path.read_text(encoding="utf-8")
    assert "SMC Review Queue" in txt
    assert "research review queue only" in txt
    assert "No orders are placed" in txt


def test_markdown_shortens_chart_path() -> None:
    row = _base_row(chart_path="/abs/long/path/AAA-daily-smc.png")
    queue = build_review_queue(_summary([row]))
    md = format_markdown(queue, top=5)
    # We don't include the chart column in the top-items table but the
    # shortened name is used in the per-item sections. The safety
    # point here is that the long absolute prefix never leaks.
    assert "/abs/long/path/" not in md


def test_digest_never_says_trade_signal() -> None:
    row = _base_row()
    queue = build_review_queue(_summary([row]))
    for mode in ("HTML", None):
        text = format_telegram_digest(queue, parse_mode=mode, top=5)
        assert "trade signal" not in text.lower()


def test_digest_says_no_candidates_when_empty() -> None:
    # One extended row → no tradeable candidates.
    row = _base_row(
        bucket="TOO_EXTENDED",
        extension_pct_vs_latest_close=6.62,
        rejection_reasons=["price_extended_from_entry_pct 6.62 > 3.00"],
    )
    queue = build_review_queue(_summary([row]))
    text = format_telegram_digest(queue, parse_mode=None, top=5)
    assert "No ICT/SMC tradeable candidates found" in text
    assert "No orders placed" in text


def test_digest_lists_tradeable_candidates_when_present() -> None:
    row = _base_row(symbol="AAA")
    queue = build_review_queue(_summary([row]))
    text = format_telegram_digest(queue, parse_mode=None, top=5)
    assert "AAA" in text
    assert "candidate for manual review" in text


# ---------------------------------------------------------------------------
# Persistence + loading
# ---------------------------------------------------------------------------
def test_save_review_queue_writes_json_envelope(tmp_project: Path) -> None:
    from bot.config import load_config
    cfg = load_config(project_root=tmp_project)
    queue = build_review_queue(_summary([_base_row()]))
    path = save_review_queue(cfg, queue)
    assert path.exists()
    payload = json.loads(path.read_text())
    assert payload["execution_allowed"] is False
    assert payload["research_only"] is True
    assert "items" in payload


def test_missing_summary_file_raises_summary_not_found(tmp_project: Path) -> None:
    from bot.config import load_config
    cfg = load_config(project_root=tmp_project)
    with pytest.raises(SummaryNotFoundError):
        load_latest_summary(cfg, date="2026-04-24")


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------
def _patch_project_root(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bot import cli as cli_module
    from bot import config as config_module

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_project)
    monkeypatch.setattr(
        cli_module, "load_config",
        lambda **kw: config_module.load_config(project_root=tmp_project, **kw),
    )


def test_cli_reports_missing_summary_cleanly(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_project_root(tmp_project, monkeypatch)
    from typer.testing import CliRunner
    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["smc-review-queue"])
    assert result.exit_code == 6
    assert "No SMC scan summary found" in result.output


def test_cli_builds_queue_and_never_places_orders(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_project_root(tmp_project, monkeypatch)

    # Write a fake summary.
    summary_dir = tmp_project / "data" / "smc_setups"
    summary_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        _base_row(symbol="AAA"),
        _base_row(
            symbol="BBB", bucket="TOO_EXTENDED",
            extension_pct_vs_latest_close=6.62,
            rejection_reasons=["price_extended_from_entry_pct 6.62 > 3.00"],
        ),
    ]
    summary_path = summary_dir / "2026-04-24-watchlist-summary.json"
    summary_path.write_text(
        json.dumps(_summary(
            rows,
            buckets={
                "WATCH_NOW": [rows[0]],
                "TOO_EXTENDED": [rows[1]],
                "NEAR_ENTRY": [],
                "STRUCTURE_INCOMPLETE": [],
                "INVALID_RISK": [],
                "BLOCKED": [],
            },
        )),
        encoding="utf-8",
    )

    from bot import broker as broker_module

    def _boom(*_a, **_kw):  # pragma: no cover - guardrail
        raise AssertionError("place_order must not be invoked")

    monkeypatch.setattr(broker_module.Broker, "place_order", _boom)

    from typer.testing import CliRunner
    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["smc-review-queue", "--markdown", "--top", "5"]
    )
    assert result.exit_code == 0, result.output
    # JSON saved
    files = list((tmp_project / "data" / "review_queue").glob("*.json"))
    assert files, result.output
    payload = json.loads(files[0].read_text())
    assert payload["execution_allowed"] is False
    assert payload["research_only"] is True
    counts = payload["counts"]
    # AAA = ready, BBB = pullback.
    assert counts["READY_FOR_MANUAL_CHART_REVIEW"] == 1
    assert counts["PULLBACK_WATCH"] == 1
    # Markdown appended.
    md = (tmp_project / "memory" / "SMC-REVIEW-QUEUE.md").read_text()
    assert "SMC Review Queue" in md


def test_cli_telegram_falls_back_without_credentials(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_project_root(tmp_project, monkeypatch)
    summary_dir = tmp_project / "data" / "smc_setups"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / "2026-04-24-watchlist-summary.json").write_text(
        json.dumps(_summary([_base_row()])), encoding="utf-8",
    )

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    from typer.testing import CliRunner
    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["smc-review-queue", "--telegram"])
    assert result.exit_code == 0, result.output
    fallback = tmp_project / "memory" / "DAILY-SUMMARY.md"
    assert fallback.exists()
    content = fallback.read_text()
    assert "SMC Review Queue" in content
    # Privacy: no account number in fallback.
    assert "DU" not in content


# ---------------------------------------------------------------------------
# Safety invariant
# ---------------------------------------------------------------------------
def test_review_queue_module_has_no_broker_imports() -> None:
    mod = importlib.import_module("bot.review_queue")
    src = Path(mod.__file__).read_text()
    assert "from .broker" not in src
    assert "import bot.broker" not in src
    assert ".place_order(" not in src


def test_execution_allowed_is_hard_coded_false() -> None:
    row = _base_row()
    q = build_review_queue(_summary([row]))
    d = q.to_dict()
    assert d["execution_allowed"] is False
    for item in d["items"]:
        assert item["execution_allowed"] is False
        assert item["research_only"] is True


def test_research_only_is_true() -> None:
    q = build_review_queue(_summary([_base_row()]))
    assert q.research_only is True
    assert q.to_dict()["research_only"] is True
