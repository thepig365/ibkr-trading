"""UI-facing strategy catalog (labels, gating) — no broker imports."""

from .catalog import (
    DEFAULT_STRATEGY_ID,
    StrategyUICatalog,
    StrategyUIEntry,
    load_strategy_ui_catalog,
)
from .selection import (
    StrategySelectionState,
    load_strategy_selection,
    save_strategy_selection,
    selection_from_mapping,
    selection_path,
    validate_paper_strategy,
    validate_per_area,
)

__all__ = [
    "DEFAULT_STRATEGY_ID",
    "StrategySelectionState",
    "StrategyUICatalog",
    "StrategyUIEntry",
    "load_strategy_ui_catalog",
    "load_strategy_selection",
    "save_strategy_selection",
    "selection_path",
    "selection_from_mapping",
    "validate_paper_strategy",
    "validate_per_area",
]
