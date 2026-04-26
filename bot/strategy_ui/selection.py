"""Persist per-area strategy selection in `data/runtime/selected_strategy.json` (gitignored)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .catalog import DEFAULT_STRATEGY_ID, StrategyUICatalog, load_strategy_ui_catalog

_SELECTED_REL = "data/runtime/selected_strategy.json"
_AREAS = ("active_scan_strategy", "active_backtest_strategy", "active_edge_strategy", "active_paper_strategy")


@dataclass
class StrategySelectionState:
    active_scan_strategy: str = DEFAULT_STRATEGY_ID
    active_backtest_strategy: str = DEFAULT_STRATEGY_ID
    active_edge_strategy: str = DEFAULT_STRATEGY_ID
    active_paper_strategy: str = DEFAULT_STRATEGY_ID
    last_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, str]:
        return {
            "active_scan_strategy": self.active_scan_strategy,
            "active_backtest_strategy": self.active_backtest_strategy,
            "active_edge_strategy": self.active_edge_strategy,
            "active_paper_strategy": self.active_paper_strategy,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "StrategySelectionState":
        s = cls()
        if not d:
            return s
        for a in _AREAS:
            v = d.get(a)
            if v is not None and str(v).strip():
                setattr(s, a, str(v).strip())
        return s


def selection_path(root: Path) -> Path:
    return (Path(root) / _SELECTED_REL).resolve()


def load_strategy_selection(
    project_root: Path | str,
    *,
    catalog: StrategyUICatalog | None = None,
) -> StrategySelectionState:
    """Load on-disk selection; coerces invalid ids and sets last_warnings on the state."""
    root = Path(project_root).resolve()
    cat = catalog or load_strategy_ui_catalog(root)
    path = selection_path(root)
    raw: dict[str, Any] | None = None
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            raw = None
    st = StrategySelectionState.from_dict(raw)
    w = _coerce_in_place(st, cat)
    st.last_warnings = w
    return st


def _coerce_in_place(st: StrategySelectionState, cat: StrategyUICatalog) -> list[str]:
    warn: list[str] = []
    dflt = cat.default_strategy if cat.default_strategy in cat.strategies else DEFAULT_STRATEGY_ID

    def _fix_field(attr: str, check) -> None:
        nonlocal warn
        cur = getattr(st, attr)
        if cur not in cat.strategies:
            warn.append(f"Unknown strategy {cur!r} in {attr}; reverted to {dflt!r}.")
            setattr(st, attr, dflt)
            cur = dflt
        if not check(cat.strategies[cur]):
            warn.append(f"Strategy {cur!r} not valid for {attr}; reverted to {dflt!r}.")
            setattr(st, attr, dflt)

    _fix_field("active_scan_strategy", lambda e: e.scan_enabled)
    _fix_field("active_backtest_strategy", lambda e: e.backtest_enabled)
    _fix_field("active_edge_strategy", lambda e: e.edge_profile_enabled)
    _fix_field("active_paper_strategy", lambda e: e.paper_enabled)
    return warn


def save_strategy_selection(
    project_root: Path | str,
    state: StrategySelectionState,
    *,
    catalog: StrategyUICatalog,
) -> Path:
    st = StrategySelectionState(
        active_scan_strategy=state.active_scan_strategy,
        active_backtest_strategy=state.active_backtest_strategy,
        active_edge_strategy=state.active_edge_strategy,
        active_paper_strategy=state.active_paper_strategy,
    )
    w = _coerce_in_place(st, catalog)
    st.last_warnings = w
    path = selection_path(Path(project_root))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(st.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def selection_from_mapping(
    cat: StrategyUICatalog,
    patch: Mapping[str, Any],
    *,
    current: StrategySelectionState | None = None,
) -> tuple[StrategySelectionState, list[str]]:
    """Apply partial field updates; validates against catalog (paper cannot be non-paper-enabled)."""
    base = current or StrategySelectionState()
    d = base.to_dict()
    for a in _AREAS:
        if a in patch and patch[a] is not None:
            d[a] = str(patch[a]).strip()
    st = StrategySelectionState.from_dict(d)
    w = _coerce_in_place(st, cat)
    st.last_warnings = w
    return st, w


def validate_paper_strategy(cat: StrategyUICatalog, strategy_id: str) -> tuple[bool, str]:
    e = cat.get(strategy_id)
    if not e:
        return False, f"Unknown strategy {strategy_id!r}."
    if not e.paper_enabled:
        return False, "Paper is disabled for this strategy until it is implemented and safety-tested."
    return True, ""


def validate_per_area(
    cat: StrategyUICatalog,
    area: str,
    strategy_id: str,
) -> tuple[bool, str]:
    e = cat.get(strategy_id)
    if not e:
        return False, f"Unknown strategy {strategy_id!r}."
    a = (area or "").strip().lower()
    if a == "scan" and not e.scan_enabled:
        return False, "Scan is not enabled for this strategy in the UI."
    if a in {"backtest", "bt"} and not e.backtest_enabled:
        return False, "Backtest is not available for this strategy yet."
    if a in {"edge", "edge_profile"} and not e.edge_profile_enabled:
        return False, "Edge profile is not enabled for this strategy yet."
    if a == "paper" and not e.paper_enabled:
        return False, (
            f"{e.display_name} is registered for future development, but paper trading is "
            "disabled until implementation, backtest, and safety tests are complete."
        )
    if a in {"paper", "scan", "backtest", "bt", "edge", "edge_profile"}:
        return True, ""
    return False, f"Unknown area {area!r}."


__all__ = [
    "StrategySelectionState",
    "load_strategy_selection",
    "save_strategy_selection",
    "selection_path",
    "selection_from_mapping",
    "validate_paper_strategy",
    "validate_per_area",
]
