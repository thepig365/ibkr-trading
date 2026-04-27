"""Shared route helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request

from ..i18n import append_lang_to_path, get_locale, lang_switch_href, t as translate
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
    root = request.app.state.project_root
    locale = get_locale(request)

    def t(key: str, **kwargs: str | float) -> str:  # noqa: ANN401
        return translate(key, locale, **kwargs)

    def ret(path: str) -> str:
        return append_lang_to_path(path, locale)

    def lang_href(target: str) -> str:
        return lang_switch_href(request, target)

    return {
        "request": request,
        "active": active,
        "backend": request.app.state.backend,
        "safety": safety,
        "ui_host": request.app.state.ui_host,
        "ui_port": request.app.state.ui_port,
        "project_root": str(root),
        "project_root_short": _project_root_short(root),
        "flash": flash,
        "doc_manual": "docs/strategy-lab-user-manual.md",
        "doc_checklist": "docs/daily-operation-checklist.md",
        "doc_troubleshooting": "docs/troubleshooting.md",
        "locale": locale,
        "html_lang": "zh" if locale == "zh" else "en",
        "t": t,
        "ret": ret,
        "lang_href": lang_href,
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
