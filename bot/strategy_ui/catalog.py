"""Load `config/strategy_ui.yaml` for Strategy Lab (no TWS, no orders)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

DEFAULT_STRATEGY_ID = "ict_smc_intraday_v1"
_CONFIG_NAME = "config/strategy_ui.yaml"


@dataclass(frozen=True)
class StrategyUIEntry:
    strategy_id: str
    display_name: str
    short_description: str
    human_summary: str
    status: str
    research_only: bool
    scan_enabled: bool
    backtest_enabled: bool
    edge_profile_enabled: bool
    paper_enabled: bool
    supported_actions: tuple[str, ...]
    timeframes: tuple[str, ...]
    final_trigger_timeframe: str
    default_symbol_universe: str
    risk_notes: tuple[str, ...] = ()
    safety_notes: tuple[str, ...] = ()
    how_it_works: str = ""
    what_blocks: str = ""
    timeframe_chain: tuple[str, ...] = ()
    paper_requirements: tuple[str, ...] = ()
    future_note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "display_name": self.display_name,
            "status": self.status,
            "scan_enabled": self.scan_enabled,
            "backtest_enabled": self.backtest_enabled,
            "edge_profile_enabled": self.edge_profile_enabled,
            "paper_enabled": self.paper_enabled,
        }


@dataclass(frozen=True)
class StrategyUICatalog:
    source_path: str
    default_strategy: str
    strategies: dict[str, StrategyUIEntry]
    raw_notes: list[str] = field(default_factory=list)

    def get(self, key: str) -> StrategyUIEntry | None:
        return self.strategies.get(key)

    def all_ids(self) -> list[str]:
        return list(self.strategies.keys())


def _as_tuple(v: Any) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, str):
        return (v,) if v.strip() else ()
    if isinstance(v, (list, tuple)):
        return tuple(str(x).strip() for x in v if str(x).strip())
    return ()


def _entry_from_block(sid: str, d: Mapping[str, Any]) -> StrategyUIEntry:
    return StrategyUIEntry(
        strategy_id=sid,
        display_name=str(d.get("display_name") or sid),
        short_description=str(d.get("short_description") or "").strip(),
        human_summary=str(d.get("human_summary") or d.get("description") or "").strip(),
        status=str(d.get("status") or "unknown"),
        research_only=bool(d.get("research_only", False)),
        scan_enabled=bool(d.get("scan_enabled", False)),
        backtest_enabled=bool(d.get("backtest_enabled", False)),
        edge_profile_enabled=bool(d.get("edge_profile_enabled", False)),
        paper_enabled=bool(d.get("paper_enabled", False)),
        supported_actions=tuple(
            str(x) for x in (d.get("supported_actions") or []) if str(x).strip()
        )
        or tuple(),
        timeframes=_as_tuple(d.get("timeframes")),
        final_trigger_timeframe=str(d.get("final_trigger_timeframe") or "n/a"),
        default_symbol_universe=str(d.get("default_symbol_universe") or "—"),
        risk_notes=_as_tuple(d.get("risk_notes")),
        safety_notes=_as_tuple(d.get("safety_notes")),
        how_it_works=str(d.get("how_it_works") or "").strip(),
        what_blocks=str(d.get("what_blocks") or "").strip(),
        timeframe_chain=_as_tuple(d.get("timeframe_chain")),
        paper_requirements=_as_tuple(d.get("paper_requirements")),
        future_note=str(d.get("future_note") or "").strip(),
        extra={k: v for k, v in d.items() if k not in {
            "display_name", "short_description", "human_summary", "status", "research_only",
            "scan_enabled", "backtest_enabled", "edge_profile_enabled", "paper_enabled",
            "supported_actions", "timeframes", "final_trigger_timeframe", "default_symbol_universe",
            "risk_notes", "safety_notes", "how_it_works", "what_blocks", "timeframe_chain",
            "paper_requirements", "future_note",
        }},
    )


def load_strategy_ui_catalog(
    project_root: Path | str,
    *,
    path: Path | str | None = None,
) -> StrategyUICatalog:
    """Load UI catalog. If the project has no file, use the repository ``config/strategy_ui.yaml`` (next to ``bot/``) when importable from this package."""
    root = Path(project_root).resolve()
    p = Path(path) if path else (root / _CONFIG_NAME)
    if not p.is_file():
        packaged = (
            Path(__file__).resolve().parent.parent.parent / "config" / "strategy_ui.yaml"
        )
        if packaged.is_file():
            p = packaged
    notes: list[str] = []
    if not p.is_file():
        return _default_catalog(str(p), note="strategy_ui.yaml not found; using minimal defaults")
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return _default_catalog(str(p), note="PyYAML missing; using minimal defaults")
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:  # type: ignore[attr-defined]
        return _default_catalog(str(p), note=f"parse error: {exc}")
    if not isinstance(data, dict):
        return _default_catalog(str(p), note="root not a mapping")

    default = str(data.get("default_strategy") or DEFAULT_STRATEGY_ID)
    raw = data.get("strategies")
    strategies: dict[str, StrategyUIEntry] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            key = str(k).strip()
            if not key or not isinstance(v, dict):
                continue
            strategies[key] = _entry_from_block(key, v)
    if not strategies:
        notes.append("strategies: empty; using minimal defaults for ICT only")
    cat = StrategyUICatalog(
        source_path=str(p),
        default_strategy=default if default in strategies else DEFAULT_STRATEGY_ID,
        strategies=strategies,
        raw_notes=notes,
    )
    if DEFAULT_STRATEGY_ID not in cat.strategies:
        return _default_catalog(
            str(p),
            note="ict_smc_intraday_v1 missing from YAML; using hardcoded defaults",
        )
    return cat


def _default_catalog(source_path: str, note: str = "") -> StrategyUICatalog:
    raw_notes = [note] if note else []
    ict = _entry_from_block(
        DEFAULT_STRATEGY_ID,
        {
            "display_name": "ICT/SMC Intraday",
            "short_description": "Intraday ICT/SMC (fallback metadata).",
            "human_summary": "Default strategy.",
            "status": "implemented",
            "scan_enabled": True,
            "backtest_enabled": True,
            "edge_profile_enabled": True,
            "paper_enabled": True,
            "supported_actions": ["scan", "backtest", "edge_profile", "paper_readiness", "paper_execution"],
            "timeframes": ["4H", "30m", "5m", "1m"],
            "final_trigger_timeframe": "1m",
            "default_symbol_universe": "dynamic watchlist",
        },
    )
    return StrategyUICatalog(
        source_path=source_path,
        default_strategy=DEFAULT_STRATEGY_ID,
        strategies={DEFAULT_STRATEGY_ID: ict},
        raw_notes=raw_notes,
    )


__all__ = [
    "DEFAULT_STRATEGY_ID",
    "StrategyUICatalog",
    "StrategyUIEntry",
    "load_strategy_ui_catalog",
]
