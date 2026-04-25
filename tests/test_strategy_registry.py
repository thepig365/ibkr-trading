"""Tests for ``bot.strategies.registry`` and ``bot.strategies.config``."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from bot.strategies.base import (
    Strategy,
    StrategyContext,
    StrategyMetadata,
    StrategyScanResult,
    _utc_now_iso,
)
from bot.strategies.config import (
    StrategyDefaults,
    StrategyEntryConfig,
    StrategyRuntimeConfig,
    load_strategies_config,
)
from bot.strategies.registry import (
    StrategyRegistry,
    default_registry,
    register_builtin_strategies,
    reset_default_registry_for_tests,
)


# ---------------------------------------------------------------------------
# Module hygiene — registry must not pull in IBKR / broker at import
# ---------------------------------------------------------------------------


def test_registry_module_does_not_import_broker_at_module_load() -> None:
    """Spawn a fresh interpreter and import the registry only.

    Asserts that ``bot.broker``, ``bot.ibkr_client`` and ``ib_async``
    do NOT appear in ``sys.modules`` afterwards. This is what lets the
    FastAPI render path safely call ``default_registry()`` without
    accidentally opening a TWS socket.
    """
    code = (
        "import sys\n"
        "import bot.strategies.registry as r\n"
        "_ = r.default_registry().list_metadata()\n"
        "banned = sorted([m for m in sys.modules "
        "if m == 'bot.broker' or m == 'bot.ibkr_client' or m == 'ib_async']);\n"
        "print('|'.join(banned))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    out = (proc.stdout or "").strip()
    assert out == "", f"Registry import pulled in banned modules: {out}"


def test_no_adapter_module_imports_broker_or_ibkr_at_module_load() -> None:
    """Static AST scan: no adapter file may import bot.broker / ib_async at top level."""
    pkg_dir = Path("bot/strategies/adapters")
    for path in sorted(pkg_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("bot.broker"), (
                        f"{path.name} imports bot.broker at module load"
                    )
                    assert alias.name != "ib_async", (
                        f"{path.name} imports ib_async at module load"
                    )
                    assert not alias.name.startswith("bot.ibkr_client"), (
                        f"{path.name} imports bot.ibkr_client at module load"
                    )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not mod.startswith("bot.broker"), (
                    f"{path.name} from-imports bot.broker at module load"
                )
                assert mod != "ib_async", (
                    f"{path.name} from-imports ib_async at module load"
                )
                assert not mod.startswith("bot.ibkr_client"), (
                    f"{path.name} from-imports bot.ibkr_client at module load"
                )


# ---------------------------------------------------------------------------
# StrategyRegistry semantics
# ---------------------------------------------------------------------------


def _dummy(key: str = "dummy") -> Strategy:
    class _D:
        metadata = StrategyMetadata(
            key=key,
            name="Dummy",
            version="0.0.1",
            description_zh="测试",
            timeframes=(),
            horizon="research",
            status="experimental",
        )

        def scan(self, ctx: StrategyContext) -> StrategyScanResult:
            now = _utc_now_iso()
            return StrategyScanResult(
                strategy_key=self.metadata.key,
                started_utc=now,
                finished_utc=now,
                status="ok",
                symbol_count=0,
            )

    return _D()


def test_register_and_get_round_trip() -> None:
    reg = StrategyRegistry()
    d = _dummy()
    reg.register(d)
    assert reg.has("dummy")
    assert reg.get("dummy") is d
    assert reg.keys() == ["dummy"]
    assert reg.list_metadata()[0].key == "dummy"


def test_duplicate_registration_raises_unless_replace() -> None:
    reg = StrategyRegistry()
    reg.register(_dummy("k"))
    with pytest.raises(ValueError):
        reg.register(_dummy("k"))
    reg.register(_dummy("k"), replace=True)  # OK with replace


def test_get_unknown_key_raises_keyerror_with_known_keys() -> None:
    reg = StrategyRegistry()
    reg.register(_dummy("a"))
    reg.register(_dummy("b"))
    with pytest.raises(KeyError) as exc:
        reg.get("missing")
    msg = str(exc.value)
    assert "missing" in msg and "['a', 'b']" in msg


def test_register_rejects_non_strategy_objects() -> None:
    reg = StrategyRegistry()
    with pytest.raises(TypeError):
        reg.register(object())


def test_register_rejects_empty_key() -> None:
    """An empty metadata.key must be rejected by ``register``."""
    class _Bare:
        metadata = StrategyMetadata(
            key="",  # explicitly empty
            name="x",
            version="0.0.1",
            description_zh="x",
            timeframes=(),
            horizon="research",
        )

        def scan(self, ctx: StrategyContext) -> StrategyScanResult:  # pragma: no cover
            now = _utc_now_iso()
            return StrategyScanResult(
                strategy_key=self.metadata.key,
                started_utc=now,
                finished_utc=now,
                status="ok",
                symbol_count=0,
            )

    reg = StrategyRegistry()
    with pytest.raises(ValueError):
        reg.register(_Bare())


# ---------------------------------------------------------------------------
# default_registry — built-in registrations match the contract
# ---------------------------------------------------------------------------


def test_default_registry_contains_all_planned_keys() -> None:
    reset_default_registry_for_tests()
    reg = default_registry()
    keys = reg.keys()
    assert "mtf_smc" in keys
    assert "ict_smc_intraday_v1" in keys
    assert "chanlun_intraday_v1" in keys
    assert "orb_baseline" in keys


def test_default_registry_marks_stubs_not_implemented() -> None:
    reset_default_registry_for_tests()
    reg = default_registry()
    for k in ("ict_smc_intraday_v1", "chanlun_intraday_v1", "orb_baseline"):
        assert reg.get(k).metadata.status == "not_implemented"
    assert reg.get("mtf_smc").metadata.status == "ready"


def test_register_builtin_strategies_is_idempotent() -> None:
    reg = StrategyRegistry()
    register_builtin_strategies(reg)
    n = len(reg.keys())
    register_builtin_strategies(reg)
    assert len(reg.keys()) == n


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def test_load_strategies_config_missing_file_returns_empty() -> None:
    rc = load_strategies_config(Path("/nonexistent/strategies.yaml"))
    assert isinstance(rc, StrategyRuntimeConfig)
    assert rc.strategies == {}
    assert rc.defaults.paper_only is True
    assert rc.defaults.paper_execution_allowed is False
    assert rc.notes  # carries a "not found" note


def test_load_strategies_config_real_file_parses_enabled_keys() -> None:
    rc = load_strategies_config(Path("config/strategies.yaml"))
    # The committed config enables mtf_smc only.
    assert "mtf_smc" in rc.strategies
    assert rc.strategies["mtf_smc"].enabled is True
    # Stubs are present but disabled.
    for k in ("ict_smc_intraday_v1", "chanlun_intraday_v1", "orb_baseline"):
        assert k in rc.strategies
        assert rc.strategies[k].enabled is False
    # Invariants are coerced.
    for entry in rc.strategies.values():
        assert entry.paper_execution_allowed is False
    assert rc.defaults.paper_only is True
    assert rc.defaults.paper_execution_allowed is False


def test_load_strategies_config_malformed_yaml_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("strategies: [this is: not, valid: yaml\n", encoding="utf-8")
    rc = load_strategies_config(p)
    # malformed -> empty + a note; never raises
    assert rc.strategies == {}
    assert rc.notes


def test_runtime_config_get_returns_disabled_default_for_unknown_key() -> None:
    rc = StrategyRuntimeConfig()
    entry = rc.get("nope")
    assert entry.key == "nope"
    assert entry.enabled is False
    assert entry.paper_execution_allowed is False


def test_strategy_entry_config_to_dict_and_runtime_to_dict() -> None:
    e = StrategyEntryConfig(key="x", enabled=True, params={"a": 1})
    d = e.to_dict()
    assert d == {"key": "x", "enabled": True, "paper_execution_allowed": False, "params": {"a": 1}}
    rc = StrategyRuntimeConfig(
        source_path="x.yaml",
        defaults=StrategyDefaults(),
        strategies={"x": e},
    )
    rd = rc.to_dict()
    assert rd["source_path"] == "x.yaml"
    assert rd["strategies"]["x"]["enabled"] is True
    assert rd["defaults"]["paper_only"] is True
