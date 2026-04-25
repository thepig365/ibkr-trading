"""Tests for ``bot.strategies.engine`` (MultiStrategyEngine)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.strategies.base import (
    StrategyContext,
    StrategyMetadata,
    StrategyScanResult,
    StrategySignal,
    _utc_now_iso,
)
from bot.strategies.config import (
    StrategyDefaults,
    StrategyEntryConfig,
    StrategyRuntimeConfig,
)
from bot.strategies.engine import (
    MultiStrategyEngine,
    MultiStrategyRunSummary,
    render_summary_zh,
    write_run_summary,
    write_single_scan,
)
from bot.strategies.registry import StrategyRegistry


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _meta(key: str, *, status: str = "ready") -> StrategyMetadata:
    return StrategyMetadata(
        key=key,
        name=key.upper(),
        version="0.0.1",
        description_zh=f"测试 {key}",
        timeframes=("daily",),
        horizon="research",
        status=status,
        enabled_by_default=False,
    )


class _OkStrategy:
    """Returns ``status="ok"`` with N synthetic signals."""

    def __init__(self, key: str = "ok", *, n_signals: int = 2) -> None:
        self.metadata = _meta(key)
        self._n = n_signals
        self.calls: list[StrategyContext] = []

    def scan(self, ctx: StrategyContext) -> StrategyScanResult:
        self.calls.append(ctx)
        now = _utc_now_iso()
        sigs = [
            StrategySignal(
                strategy_key=self.metadata.key,
                symbol=f"S{i}",
                direction="unknown",
                confidence="medium",
                horizon="research",
                timeframe="daily",
                score=float(i),
            )
            for i in range(self._n)
        ]
        return StrategyScanResult(
            strategy_key=self.metadata.key,
            started_utc=now,
            finished_utc=now,
            status="ok",
            symbol_count=len(ctx.symbols),
            signals=sigs,
            summary={"n": self._n},
        )


class _NotImplementedStrategy:
    def __init__(self, key: str = "stub") -> None:
        self.metadata = _meta(key, status="not_implemented")

    def scan(self, ctx: StrategyContext) -> StrategyScanResult:
        now = _utc_now_iso()
        return StrategyScanResult(
            strategy_key=self.metadata.key,
            started_utc=now,
            finished_utc=now,
            status="not_implemented",
            symbol_count=len(ctx.symbols),
        )


class _RaisingStrategy:
    def __init__(self, key: str = "boom") -> None:
        self.metadata = _meta(key, status="experimental")

    def scan(self, ctx: StrategyContext) -> StrategyScanResult:
        raise RuntimeError("simulated adapter crash")


def _runtime_with(*pairs: tuple[str, bool]) -> StrategyRuntimeConfig:
    return StrategyRuntimeConfig(
        defaults=StrategyDefaults(),
        strategies={
            k: StrategyEntryConfig(key=k, enabled=enabled) for k, enabled in pairs
        },
    )


def _ctx(symbols: tuple[str, ...] = ("AAPL", "NVDA")) -> StrategyContext:
    return StrategyContext(symbols=symbols)


# ---------------------------------------------------------------------------
# resolve_keys_to_run semantics
# ---------------------------------------------------------------------------


def test_resolve_keys_runs_only_enabled_by_default() -> None:
    reg = StrategyRegistry()
    reg.register(_OkStrategy("a"))
    reg.register(_NotImplementedStrategy("b"))
    runtime = _runtime_with(("a", True), ("b", False))
    eng = MultiStrategyEngine(registry=reg, runtime_config=runtime)
    run, skipped = eng.resolve_keys_to_run()
    assert run == ["a"]
    assert skipped == ["b"]


def test_resolve_keys_only_subset_intersects_registry() -> None:
    reg = StrategyRegistry()
    reg.register(_OkStrategy("a"))
    eng = MultiStrategyEngine(registry=reg)
    run, skipped = eng.resolve_keys_to_run(only=["a", "ghost"])
    assert run == ["a"]
    assert skipped == ["ghost"]


def test_resolve_keys_include_disabled_runs_everything() -> None:
    reg = StrategyRegistry()
    reg.register(_OkStrategy("a"))
    reg.register(_NotImplementedStrategy("b"))
    runtime = _runtime_with(("a", False), ("b", False))
    eng = MultiStrategyEngine(registry=reg, runtime_config=runtime)
    run, skipped = eng.resolve_keys_to_run(include_disabled=True)
    assert set(run) == {"a", "b"}
    assert skipped == []


# ---------------------------------------------------------------------------
# run() behavior
# ---------------------------------------------------------------------------


def test_run_aggregates_results_and_signal_count() -> None:
    reg = StrategyRegistry()
    reg.register(_OkStrategy("a", n_signals=2))
    reg.register(_OkStrategy("b", n_signals=3))
    runtime = _runtime_with(("a", True), ("b", True))
    eng = MultiStrategyEngine(registry=reg, runtime_config=runtime)
    summary = eng.run(_ctx())
    assert isinstance(summary, MultiStrategyRunSummary)
    assert summary.enabled_keys == ["a", "b"]
    assert summary.total_signals == 5
    assert summary.execution_allowed is False
    assert summary.paper_only is True


def test_run_skips_disabled_strategies() -> None:
    reg = StrategyRegistry()
    a = _OkStrategy("a")
    b = _OkStrategy("b")
    reg.register(a)
    reg.register(b)
    runtime = _runtime_with(("a", True), ("b", False))
    eng = MultiStrategyEngine(registry=reg, runtime_config=runtime)
    summary = eng.run(_ctx())
    assert summary.enabled_keys == ["a"]
    assert summary.skipped_keys == ["b"]
    assert len(b.metadata.key) == 1  # sanity
    assert len(a.calls) == 1
    assert len(summary.results) == 1


def test_run_returns_not_implemented_for_stub_when_explicitly_invoked() -> None:
    reg = StrategyRegistry()
    reg.register(_NotImplementedStrategy("stub"))
    eng = MultiStrategyEngine(registry=reg)
    summary = eng.run(_ctx(), only=["stub"])
    assert len(summary.results) == 1
    assert summary.results[0].status == "not_implemented"
    assert summary.total_signals == 0


def test_run_catches_strategy_exception_and_records_error() -> None:
    reg = StrategyRegistry()
    reg.register(_RaisingStrategy("boom"))
    reg.register(_OkStrategy("ok"))
    eng = MultiStrategyEngine(registry=reg)
    summary = eng.run(_ctx(), only=["boom", "ok"])
    statuses = {r.strategy_key: r.status for r in summary.results}
    assert statuses["boom"] == "error"
    assert statuses["ok"] == "ok"
    err_result = next(r for r in summary.results if r.strategy_key == "boom")
    assert err_result.error and "simulated adapter crash" in err_result.error


def test_run_rejects_paper_only_false_context() -> None:
    eng = MultiStrategyEngine(registry=StrategyRegistry())
    with pytest.raises(ValueError):
        eng.run(StrategyContext(symbols=("X",), paper_only=False))


def test_run_rejects_paper_execution_allowed_true_context() -> None:
    eng = MultiStrategyEngine(registry=StrategyRegistry())
    with pytest.raises(ValueError):
        eng.run(StrategyContext(symbols=("X",), paper_execution_allowed=True))


def test_run_passes_per_strategy_params_via_extras() -> None:
    reg = StrategyRegistry()
    a = _OkStrategy("a")
    reg.register(a)
    runtime = StrategyRuntimeConfig(
        defaults=StrategyDefaults(),
        strategies={
            "a": StrategyEntryConfig(key="a", enabled=True, params={"x": 7}),
        },
    )
    eng = MultiStrategyEngine(registry=reg, runtime_config=runtime)
    eng.run(_ctx())
    assert a.calls and a.calls[0].extras.get("x") == 7
    # paper invariants flow through the merged ctx too
    assert a.calls[0].paper_only is True
    assert a.calls[0].paper_execution_allowed is False


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def test_write_run_summary_writes_dated_file(tmp_path: Path) -> None:
    reg = StrategyRegistry()
    reg.register(_OkStrategy("a"))
    runtime = _runtime_with(("a", True))
    eng = MultiStrategyEngine(registry=reg, runtime_config=runtime)
    summary = eng.run(_ctx())
    out = write_run_summary(summary, output_dir=tmp_path)
    assert out.exists()
    assert out.name.endswith("-multi-strategy-scan.json")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["execution_allowed"] is False
    assert payload["paper_only"] is True
    assert payload["total_signals"] == 2


def test_write_single_scan_writes_per_strategy_file(tmp_path: Path) -> None:
    reg = StrategyRegistry()
    reg.register(_OkStrategy("alpha"))
    eng = MultiStrategyEngine(registry=reg)
    summary = eng.run(_ctx(), only=["alpha"])
    out = write_single_scan(summary.results[0], output_dir=tmp_path)
    assert out.exists()
    assert "alpha-scan.json" in out.name
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["strategy_key"] == "alpha"
    assert body["execution_allowed"] is False


def test_render_summary_zh_includes_required_fields() -> None:
    reg = StrategyRegistry()
    reg.register(_OkStrategy("a"))
    eng = MultiStrategyEngine(registry=reg)
    summary = eng.run(_ctx(), only=["a"])
    text = render_summary_zh(summary)
    assert "多策略扫描完成" in text
    assert "paper_only" in text
    assert "execution_allowed" in text
    assert "a:" in text
