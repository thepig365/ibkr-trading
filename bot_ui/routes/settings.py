"""Settings page (placeholder + safety + allowlist viewer)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..services.safety import (
    ALLOWED_COMMANDS,
    FORBIDDEN_ARG_TOKENS,
    FORBIDDEN_COMMAND_TOKENS,
)
from ._helpers import base_context

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse, name="settings_page")
def settings_page(request: Request) -> HTMLResponse:
    ctx = base_context(request, active="settings")
    ctx.update(
        {
            "allowed_commands": ALLOWED_COMMANDS,
            "forbidden_command_tokens": sorted(FORBIDDEN_COMMAND_TOKENS),
            "forbidden_arg_tokens": sorted(FORBIDDEN_ARG_TOKENS),
            "runtime": request.app.state.state_store.runtime_flags(),
        }
    )
    return request.app.state.templates.TemplateResponse(request, "settings.html", ctx)
