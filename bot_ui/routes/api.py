"""POST endpoints used by the page buttons to enqueue commands.

Every button submits a form here; the handler validates against the
allowlist via :class:`bot_ui.services.command_queue.LocalCommandRunner`,
runs the command, and redirects back to the originating page with a
flash status. There is no JSON body parsing — this is intentional;
forms only.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ..services.command_queue import CommandRequest


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api/commands", tags=["commands"])

    @router.post("/run", name="api_run_command")
    def run_command(
        request: Request,
        command: str = Form(...),
        args: str = Form(default=""),
        return_to: str = Form(default="/dashboard"),
    ) -> RedirectResponse:
        # Tokenise the args field. We deliberately split on whitespace
        # (no shell parsing) so users cannot smuggle metacharacters.
        arg_tuple = tuple(a for a in (args or "").split() if a)
        req = CommandRequest(
            command=command.strip(),
            args=arg_tuple,
            requested_by="local-ui",
        )
        request.app.state.command_queue.submit(req)
        # Always redirect back; the next page render will surface the
        # latest result row in its "Recent commands" section.
        return RedirectResponse(url=_safe_return(return_to), status_code=303)

    return router


_ALLOWED_RETURNS = {
    "/dashboard",
    "/watchlist",
    "/signals",
    "/paper",
    "/logs",
    "/settings",
}


def _safe_return(value: str) -> str:
    v = (value or "").strip()
    if v in _ALLOWED_RETURNS:
        return v
    return "/dashboard"
