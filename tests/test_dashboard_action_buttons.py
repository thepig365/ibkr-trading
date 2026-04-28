"""Dashboard actionable buttons — forms, links, no hidden auto-connect."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore


def _client(root: Path) -> TestClient:
    (root / "data").mkdir(exist_ok=True)
    state = LocalFileStateStore(root)
    queue = LocalCommandRunner(
        project_root=root,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=root / "ui_audit.jsonl",
    )
    return TestClient(
        create_app(project_root=root, state_store=state, command_queue=queue)
    )


def _first_hidden_value(html: str, name: str) -> str | None:
    pat = rf'name="{re.escape(name)}"\s+value="([^"]*)"'
    m = re.search(pat, html)
    return m.group(1) if m else None


def test_connect_refresh_twspost_return_to_zh(tmp_path: Path) -> None:
    html = _client(tmp_path).get("/dashboard?lang=zh").text
    assert 'name="command" value="broker-snapshot-refresh"' in html
    rt = _first_hidden_value(html, "return_to")
    assert rt is not None
    assert "lang=zh" in rt.replace("%3D", "=")


def test_complete_trade_charts_form_on_dashboard(tmp_path: Path) -> None:
    html = _client(tmp_path).get("/dashboard").text
    assert 'name="command" value="complete-trade-charts"' in html
    assert "--fetch-missing-candles" in html


def test_dashboard_href_quick_links_have_lang(tmp_path: Path) -> None:
    html = _client(tmp_path).get("/dashboard?lang=zh").text
    assert 'href="' in html and "lang=zh" in html


def test_dashboard_main_cockpit_has_no_live_trading_commands(tmp_path: Path) -> None:
    html = _client(tmp_path).get("/dashboard").text
    fold = html.find('id="cockpit-diagnostics"')
    cockpit = html[:fold] if fold >= 0 else html
    lc = cockpit.lower()
    assert "enable-live" not in lc.replace("_", "-")
    assert "place-order" not in lc.replace("_", "-")
    assert "run-first-paper-pass" not in lc.replace("_", "-")
    assert '"command" value="run-automatic-paper-engine"' not in cockpit

