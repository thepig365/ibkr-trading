"""Load Strategy Lab UI catalog + selection (file I/O only; no IBKR)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request

from bot.strategy_ui import (
    StrategySelectionState,
    StrategyUICatalog,
    load_strategy_selection,
    load_strategy_ui_catalog,
)


def get_catalog_and_selection(
    project_root: Path,
) -> tuple[StrategyUICatalog, StrategySelectionState]:
    cat = load_strategy_ui_catalog(project_root)
    sel = load_strategy_selection(project_root, catalog=cat)
    return cat, sel


def strategy_context_for_request(request: Request) -> dict[str, Any]:
    root: Path = request.app.state.project_root
    cat, sel = get_catalog_and_selection(root)
    return {
        "strategy_ui_catalog": cat,
        "strategy_selection": sel,
    }


__all__ = [
    "get_catalog_and_selection",
    "strategy_context_for_request",
]
