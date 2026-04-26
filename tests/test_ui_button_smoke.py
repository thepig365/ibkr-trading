"""UI route smoke: all main pages 200, command buttons are allowlisted."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.safety import ALLOWED_COMMANDS, is_forbidden, is_allowed
from bot_ui.services.state_store import LocalFileStateStore

REPO = Path(__file__).resolve().parent.parent

MAIN_GET_PATHS: tuple[str, ...] = (
    "/dashboard",
    "/research",
    "/watchlist",
    "/signals?strategy=ict_smc_intraday_v1",
    "/backtest",
    "/edge",
    "/paper",
    "/journal",
    "/reports",
    "/logs",
    "/settings",
    "/strategies",
)

# In templates: {% with command='foo' ... %}
_CMD_WITH_RE = re.compile(
    r"""command\s*=\s*['\"]([a-z0-9][a-z0-9_.-]+)['\"]""", re.IGNORECASE
)
# Raw forms: <input type="hidden" name="command" value="backtest-..." />
_CMD_HIDDEN_RE = re.compile(
    r"""name=\"command\"\s+value=\"([a-z0-9][a-z0-9_.-]+)\"\s*/>""", re.IGNORECASE
)


def _client(root: Path) -> TestClient:
    state = LocalFileStateStore(root)
    queue = LocalCommandRunner(
        project_root=root,
        python_executable=sys.executable,
        timeout_seconds=120,
        audit_file=root / "data" / "ui_cmd_audit.jsonl",
    )
    return TestClient(
        create_app(project_root=root, state_store=state, command_queue=queue)
    )


@pytest.mark.parametrize("path", MAIN_GET_PATHS)
def test_all_main_routes_200_on_empty_project(tmp_path: Path, path: str) -> None:
    (tmp_path / "data").mkdir()
    c = _client(tmp_path)
    r = c.get(path)
    assert r.status_code == 200, f"{path}: {r.text[:400]}"
    assert "Strategy Lab" in r.text


def test_strategies_in_api_safe_return_prefixes() -> None:
    from bot_ui.routes import api as api_mod

    assert "/strategies" in api_mod._ALLOWED_PATH_PREFIXES


def test_all_command_forms_use_allowlisted_commands() -> None:
    """Static scan: every template-embedded command name is on the allowlist."""
    tmpl = REPO / "bot_ui" / "templates"
    for path in sorted(tmpl.rglob("*.html")):
        if path.name.startswith("_") and path.name != "_command_form.html":
            continue
        text = path.read_text(encoding="utf-8")
        if "api_run_command" not in text and "/api/commands/run" not in text:
            continue
        for m in _CMD_WITH_RE.finditer(text):
            cmd = m.group(1)
            if cmd in {"return", "url_for", "to"}:  # false positives
                continue
            assert cmd in ALLOWED_COMMANDS, f"{path.name}: unknown command {cmd!r}"
            assert is_allowed(cmd), f"{path.name}: command not allowed {cmd!r}"
        for m in _CMD_HIDDEN_RE.finditer(text):
            cmd = m.group(1)
            assert cmd in ALLOWED_COMMANDS, f"{path.name}: hidden unknown {cmd!r}"


def test_run_auto_loop_not_allowlisted() -> None:
    assert "run-auto-paper-intraday-loop" not in ALLOWED_COMMANDS
    assert is_forbidden("run-auto-paper-intraday-loop")
    t = (REPO / "bot_ui" / "templates").rglob("*.html")
    for p in t:
        assert "run-auto-paper-intraday-loop" not in p.read_text(
            encoding="utf-8"
        ), p


def test_first_paper_only_on_paper_page() -> None:
    for p in (REPO / "bot_ui" / "templates").rglob("*.html"):
        t = p.read_text(encoding="utf-8")
        if "first-paper-pass" in t and p.name != "paper.html":
            raise AssertionError(f"first-paper should only be on paper: {p}")


def test_paper_trading_forms_include_first_paper_button() -> None:
    t = (REPO / "bot_ui" / "templates" / "paper.html").read_text(encoding="utf-8")
    assert "first-paper-pass" in t
