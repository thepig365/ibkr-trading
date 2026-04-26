"""Logs page (read-only file tailing with secret masking)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from ..services.log_reader import is_inside, mask_secrets, safe_relative
from ._helpers import base_context

router = APIRouter()

DEFAULT_TAIL_BYTES = 64_000
MAX_TAIL_BYTES = 1_000_000


@router.get("/logs", response_class=HTMLResponse, name="logs_page")
def logs_page(
    request: Request,
    file: str | None = Query(default=None),
    bytes_to_show: int = Query(default=DEFAULT_TAIL_BYTES, alias="bytes", ge=1024, le=MAX_TAIL_BYTES),
) -> HTMLResponse:
    state = request.app.state.state_store
    project_root: Path = request.app.state.project_root
    files = state.list_log_files()

    selected_path: Path | None = None
    raw_text = ""
    error: str | None = None
    if file:
        # Absolute or project-relative path
        candidate = Path(file)
        if not candidate.is_absolute():
            candidate = project_root / candidate
        if not is_inside(candidate, project_root):
            error = "Refusing to read files outside the project directory."
        elif not candidate.exists() or not candidate.is_file():
            error = f"File not found: {safe_relative(candidate, project_root)}"
        else:
            selected_path = candidate
            raw_text = state.tail_file(candidate, max_bytes=bytes_to_show)
    elif files:
        selected_path = files[0]
        raw_text = state.tail_file(selected_path, max_bytes=bytes_to_show)

    masked = mask_secrets(raw_text) if raw_text else ""
    file_rows = [
        {
            "rel": safe_relative(p, project_root),
            "abs_quoted": quote(str(p)),
            "size": p.stat().st_size if p.exists() else 0,
        }
        for p in files[:200]
    ]

    paper_rep: dict[str, str | None] = {}
    if hasattr(state, "latest_paper_report_links"):
        paper_rep = state.latest_paper_report_links()  # type: ignore[assignment,union-attr]
    ctx = base_context(request, active="logs")
    ctx.update(
        {
            "files": file_rows,
            "selected_path": str(selected_path) if selected_path else None,
            "selected_rel": safe_relative(selected_path, project_root) if selected_path else None,
            "content": masked,
            "bytes_to_show": bytes_to_show,
            "error": error,
            "paper_reports": paper_rep,
        }
    )
    return request.app.state.templates.TemplateResponse(request, "logs.html", ctx)
