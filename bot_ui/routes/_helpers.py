"""Shared route helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request

from ..services.command_queue import CommandResult
from ..services.state_store import StateStore


def _project_root_short(p: Path) -> str:
    home = Path.home()
    try:
        return "~/" + str(p.relative_to(home))
    except ValueError:
        return str(p)


def base_context(
    request: Request,
    *,
    active: str,
    flash: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Common context every page template needs."""
    state: StateStore = request.app.state.state_store
    safety = state.safety_view()
    return {
        "request": request,
        "active": active,
        "backend": request.app.state.backend,
        "safety": safety,
        "ui_host": request.app.state.ui_host,
        "ui_port": request.app.state.ui_port,
        "project_root_short": _project_root_short(request.app.state.project_root),
        "flash": flash,
    }


def flash_from_result(result: CommandResult | None) -> dict[str, str] | None:
    if result is None:
        return None
    if not result.accepted:
        return {"kind": "fail", "message": f"Command rejected: {result.rejected_reason}"}
    if result.exit_code == 0:
        return {
            "kind": "ok",
            "message": (
                f"Ran {result.request.display()} OK in "
                f"{result.duration_seconds or 0:.1f}s."
            ),
        }
    return {
        "kind": "fail",
        "message": (
            f"Command {result.request.display()} exited "
            f"{result.exit_code} (see Recent commands)."
        ),
    }


def parse_bool_param(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
