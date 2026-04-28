"""/reports exposes optional trade chart batch summary block (read-only)."""

from __future__ import annotations

import json
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


def test_reports_page_shows_trade_chart_batch_when_runtime_file_exists(
    tmp_path: Path,
) -> None:
    rtd = tmp_path / "data" / "runtime"
    rtd.mkdir(parents=True)
    (rtd / "trade_chart_batch_last.json").write_text(
        json.dumps(
            {
                "generated_count": 2,
                "missing_candles_count": 1,
                "chart_dir": str(tmp_path / "data" / "reports" / "trade_charts"),
            }
        ),
        encoding="utf-8",
    )
    r = _client(tmp_path).get("/reports")
    assert r.status_code == 200
    assert "Journal trade charts" in r.text
    assert "2" in r.text
