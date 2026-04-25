"""Paper Trading page.

Important: this page does NOT place orders. It exposes:

* a paper-reconcile button (read-only check),
* a refresh-paper-account-state button (forces fresh snapshots),
* runtime toggles (kill switch, MTF auto paper enabled flag) implemented
  as filesystem flags read by :mod:`bot.auto_paper_loop` and
  :mod:`bot.auto_paper_mtf` running in a separate process.

Canonical paths (must match the worker / Telegram /kill /resume handlers):

* Kill switch  -> ``<project_root>/data/KILL_SWITCH``
* MTF auto on  -> ``<project_root>/data/runtime/mtf_auto_paper_enabled``
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..services.state_store import (
    KILL_SWITCH_RELPATH,
    MTF_AUTO_PAPER_ENABLED_RELPATH,
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
    """Create or remove the canonical kill-switch file.

    Writes ``<project_root>/data/KILL_SWITCH`` — the same file checked by
    :func:`bot.auto_paper_mtf.is_kill_switch_active` and by Telegram
    ``/kill`` / ``/resume``. The UI never places or cancels orders here.
    """
    project_root = request.app.state.project_root
    target = (project_root / KILL_SWITCH_RELPATH).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if (enable or "").strip().lower() in {"1", "on", "true", "yes"}:
        target.write_text(
            f"{datetime.now(timezone.utc).isoformat()} via local UI /paper\n",
            encoding="utf-8",
        )
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
    """Write the canonical MTF auto-paper flag file.

    Writes ``<project_root>/data/runtime/mtf_auto_paper_enabled`` with
    ``1`` or ``0``. Consumed by
    :func:`bot.auto_paper_mtf.is_runtime_mtf_auto_enabled` /
    :func:`bot.auto_paper_mtf.is_runtime_mtf_auto_disabled_explicit` and by
    the auto-paper loop. The UI never places orders here.
    """
    project_root = request.app.state.project_root
    target = (project_root / MTF_AUTO_PAPER_ENABLED_RELPATH).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    val = (state or "").strip().lower()
    if val in {"1", "on", "true", "yes"}:
        target.write_text("1\n", encoding="utf-8")
    else:
        target.write_text("0\n", encoding="utf-8")
    return RedirectResponse(url="/paper", status_code=303)
