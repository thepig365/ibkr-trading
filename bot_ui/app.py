"""FastAPI application factory for the local Strategy Lab UI.

Boot rules (must remain true forever):

* No IBKR / TWS connection on startup.
* No order placement on startup or anywhere from this app.
* Binds to 127.0.0.1 by default — never to 0.0.0.0.
* Backend selected by ``STRATEGY_LAB_BACKEND`` env (``local`` / ``remote``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .i18n import COOKIE_NAME, SUPPORTED
from .services.command_queue import CommandQueue, get_command_queue
from .services.state_store import StateStore, get_state_store

PROJECT_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def create_app(
    *,
    project_root: Path | None = None,
    state_store: StateStore | None = None,
    command_queue: CommandQueue | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    All collaborators are injectable so tests can swap them with fakes
    without touching the filesystem or shelling out.
    """
    root = Path(project_root or PROJECT_ROOT_DEFAULT).resolve()
    backend = (os.environ.get("STRATEGY_LAB_BACKEND") or "local").strip().lower()

    state = state_store if state_store is not None else get_state_store(root)
    queue = command_queue if command_queue is not None else get_command_queue(root)

    templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

    app = FastAPI(
        title="Strategy Lab (Local)",
        description=(
            "Local FastAPI UI for the IBKR paper trading bot. "
            "Paper-only by construction; never connects to TWS at startup."
        ),
        version="0.1.0-13A",
        docs_url=None,  # Hide /docs by default — local-only UI, not an API surface
        redoc_url=None,
    )

    # Stash collaborators on app.state so route handlers can read them
    # without re-running factories.
    app.state.project_root = root
    app.state.backend = backend
    app.state.state_store = state
    app.state.command_queue = queue
    app.state.templates = templates
    app.state.ui_host = os.environ.get("STRATEGY_LAB_HOST", DEFAULT_HOST)
    app.state.ui_port = int(os.environ.get("STRATEGY_LAB_PORT", str(DEFAULT_PORT)))

    static_dir = PACKAGE_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def _lang_cookie_middleware(request: Request, call_next: Any) -> Any:
        """Set strategy_lab_lang cookie when ?lang=en|zh is present (display only)."""
        response = await call_next(request)
        lang = (request.query_params.get("lang") or "").strip().lower()
        if lang in SUPPORTED:
            response.set_cookie(
                COOKIE_NAME,
                lang,
                max_age=365 * 24 * 3600,
                httponly=False,
                samesite="lax",
                path="/",
            )
        return response

    # Lazy import routers to keep top-level imports tiny and to avoid
    # any chance of circular imports across the routes/services layers.
    from .routes import backtest as backtest_routes  # noqa: PLC0415
    from .routes import dashboard as dashboard_routes  # noqa: PLC0415
    from .routes import edge as edge_routes  # noqa: PLC0415
    from .routes import forex as forex_routes  # noqa: PLC0415
    from .routes import journal as journal_routes  # noqa: PLC0415
    from .routes import logs as logs_routes  # noqa: PLC0415
    from .routes import paper as paper_routes  # noqa: PLC0415
    from .routes import reports as reports_routes  # noqa: PLC0415
    from .routes import research as research_routes  # noqa: PLC0415
    from .routes import settings as settings_routes  # noqa: PLC0415
    from .routes import signals as signals_routes  # noqa: PLC0415
    from .routes import strategies as strategies_routes  # noqa: PLC0415
    from .routes import trades as trades_routes  # noqa: PLC0415
    from .routes import watchlist as watchlist_routes  # noqa: PLC0415
    from .routes.api import build_api_router  # noqa: PLC0415

    app.include_router(dashboard_routes.router)
    app.include_router(edge_routes.router)
    app.include_router(watchlist_routes.router)
    app.include_router(signals_routes.router)
    app.include_router(paper_routes.router)
    app.include_router(forex_routes.router)
    app.include_router(trades_routes.router)
    app.include_router(journal_routes.router)
    app.include_router(strategies_routes.router)
    app.include_router(research_routes.router)
    app.include_router(reports_routes.router)
    app.include_router(backtest_routes.router)
    app.include_router(logs_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(build_api_router())

    @app.get("/", include_in_schema=False)
    def _root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard", status_code=302)

    @app.get("/healthz", include_in_schema=False)
    def _healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "paper_only": True,
            "backend": app.state.backend,
            "host": app.state.ui_host,
            "port": app.state.ui_port,
        }

    @app.exception_handler(404)
    async def _not_found(request: Request, exc: Exception) -> HTMLResponse:  # noqa: ARG001
        from .routes._helpers import base_context as _base_ctx  # noqa: PLC0415

        ctx = _base_ctx(request, active="dashboard")
        ctx["error_path"] = request.url.path
        return templates.TemplateResponse(request, "404.html", ctx, status_code=404)

    return app


__all__ = ["create_app", "DEFAULT_HOST", "DEFAULT_PORT"]
