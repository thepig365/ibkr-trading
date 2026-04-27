"""UI wiring for full auto — no IBKR connect on import paths used by pages."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.config import load_config
from bot.full_auto_paper_readiness import build_full_auto_paper_readiness
from bot_ui.services.safety import validate_args_for


def test_safety_allows_readiness_and_supervisor_dry() -> None:
    ok, err = validate_args_for("full-auto-paper-readiness", ("--json",))
    assert ok and not err
    ok2, err2 = validate_args_for(
        "run-full-auto-paper-supervisor", ("--dry-run", "--json")
    )
    assert ok2 and not err2


def test_readiness_ui_safe_no_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path as P

    root = P(__file__).resolve().parents[1]
    import shutil
    (tmp_path / "config").mkdir()
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
    ):
        shutil.copy(root / "config" / name, tmp_path / "config" / name)
    (tmp_path / "data").mkdir()
    cfg = load_config(project_root=tmp_path)
    # Ensure no TCP to TWS: ui_safe True
    r = build_full_auto_paper_readiness(
        tmp_path, cfg, None, probe_ibkr=False, ui_safe=True
    )
    assert r.get("ibkr_connected") is None
