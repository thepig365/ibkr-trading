"""Paper Trading page.

Important: this page does NOT place orders. It exposes:

* a paper-reconcile button (read-only check),
* a refresh-paper-account-state button (forces fresh snapshots),
* runtime toggles (kill switch, MTF auto paper enabled flag) implemented
  as filesystem flags in ``data/runtime/`` — these are read by
  :mod:`bot.auto_paper_loop` running in a separate process.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..services.state_store import (
    KILL_SWITCH_FILE,
    MTF_AUTO_PAPER_ENABLED_FILE,
)
from ._helpers import base_context

router = APIRouter()


@router.get("/paper", response_class=HTMLResponse, name="paper_page")
def paper_page(request: Request) -> HTMLResponse:
    state = request.app.state.state_store
    ctx = base_context(request, active="paper")
    ctx["account"] = state.account_summary()
    ctx["positions"] = state.positions()
    ctx["loop"] = state.loop_status()
    ctx["runtime"] = state.runtime_flags()
    ctx["recent_results"] = request.app.state.command_queue.list_recent(limit=8)
    return request.app.state.templates.TemplateResponse(request, "paper.html", ctx)


@router.post("/paper/runtime/kill-switch", name="toggle_kill_switch")
def toggle_kill_switch(
    request: Request,
    enable: str = Form(default="off"),
) -> RedirectResponse:
    """Create or remove ``data/runtime/KILL_SWITCH`` based on form input.

    This file is consumed by :mod:`bot.auto_paper_loop` as a hard stop.
    The UI never places or cancels orders here.
    """
    runtime_dir = (request.app.state.project_root / "data" / "runtime").resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    target = runtime_dir / KILL_SWITCH_FILE
    if (enable or "").strip().lower() in {"1", "on", "true", "yes"}:
        target.write_text("kill switch on\n", encoding="utf-8")
    else:
        if target.exists():
            try:
                target.unlink()
            except OSError:
                pass
    return RedirectResponse(url="/paper", status_code=303)


@router.post("/paper/runtime/mtf-auto", name="toggle_mtf_auto")
def toggle_mtf_auto(
    request: Request,
    state: str = Form(default="off"),
) -> RedirectResponse:
    """Write ``data/runtime/mtf_auto_paper_enabled`` with ``1`` or ``0``."""
    runtime_dir = (request.app.state.project_root / "data" / "runtime").resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    target = runtime_dir / MTF_AUTO_PAPER_ENABLED_FILE
    val = (state or "").strip().lower()
    if val in {"1", "on", "true", "yes"}:
        target.write_text("1\n", encoding="utf-8")
    else:
        target.write_text("0\n", encoding="utf-8")
    return RedirectResponse(url="/paper", status_code=303)
