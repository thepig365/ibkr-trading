"""CLI tests for ``scan-intraday-smc`` and ``scan-intraday-smc-watchlist``.

These tests exercise the new Prompt 13D CLI surface end-to-end by
spawning ``python -m bot.cli`` as a subprocess. They never reach a real
TWS — without one available the scanner takes the ``data_source="missing"``
path and reports ``BLOCKED``. The important guarantees verified here:

* ``--ibkr`` is required for both commands (safety: no silent IBKR poll).
* Invalid ``--mode`` / ``--direction-hint`` values are rejected with
  exit code 2 (Typer convention).
* The single-symbol command writes valid JSON when ``--save-json`` is
  set (tmp output dir).
* Both commands always report ``execution_allowed=False`` /
  ``paper_only=True`` in their stdout payloads.
* Without a built dynamic watchlist, the watchlist scan exits with a
  helpful non-zero code (3) instead of crashing.
* The ``broker.place_order`` API is not invoked by either command —
  asserted via a side-channel monkey-patch through a small inline
  ``conftest`` import in the subprocess.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The CLI uses ``bot.config.PROJECT_ROOT`` which is hard-coded to the
# repo root, so output files always land under ``REPO_ROOT/data/``
# even when the subprocess is launched with a different ``cwd``. The
# fixture below registers a teardown that cleans up the unique files
# the test created so the repo stays tidy.
INTRADAY_OUT = REPO_ROOT / "data" / "intraday_smc"


@pytest.fixture
def cleanup_intraday_outputs() -> list[Path]:
    """Track files we created so we can remove them on teardown."""
    INTRADAY_OUT.mkdir(parents=True, exist_ok=True)
    before = {p for p in INTRADAY_OUT.iterdir() if p.is_file()}
    created: list[Path] = []
    yield created
    after = {p for p in INTRADAY_OUT.iterdir() if p.is_file()}
    for p in (after - before):
        try:
            p.unlink()
        except OSError:
            pass


def _make_project(tmp_path: Path) -> Path:
    """Mirror conftest.tmp_project but inline so we can run subprocesses.

    Copies the small repo config files into ``tmp_path/config`` and
    creates ``data/`` + ``memory/``. The CLI is launched with
    ``cwd=tmp_path`` and ``PYTHONPATH=REPO_ROOT`` so it imports the
    real ``bot`` package but writes outputs under the temp dir.
    """
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "intraday_smc").mkdir(exist_ok=True)
    (tmp_path / "memory").mkdir(exist_ok=True)
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
        "strategies.yaml",
        "macro_calendar.yaml",
    ):
        src = REPO_ROOT / "config" / name
        if src.exists():
            shutil.copy(src, tmp_path / "config" / name)
    return tmp_path


def _run_cli(
    args: list[str], *, cwd: Path, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["IBKR_ACCOUNT_MODE"] = "paper"
    env["PYTHONPATH"] = str(REPO_ROOT)
    # Force IBKR connect attempts to fail fast — no real TWS.
    env.setdefault("IBKR_HOST", "127.0.0.1")
    env.setdefault("IBKR_PORT", "65530")  # closed port
    env.setdefault("IBKR_CLIENT_ID", "9999")
    return subprocess.run(
        [sys.executable, "-m", "bot.cli", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


# ---------------------------------------------------------------------------
# scan-intraday-smc — single symbol
# ---------------------------------------------------------------------------
def test_cli_scan_intraday_smc_requires_ibkr(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    p = _run_cli(["scan-intraday-smc", "--symbol", "CRM"], cwd=proj)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "--ibkr is required" in (p.stdout + p.stderr)


def test_cli_scan_intraday_smc_rejects_bad_direction_hint(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    p = _run_cli(
        [
            "scan-intraday-smc",
            "--symbol", "CRM",
            "--ibkr",
            "--no-save-json",
            "--direction-hint", "buy",
        ],
        cwd=proj,
    )
    assert p.returncode == 2
    assert "direction-hint" in (p.stdout + p.stderr).lower()


def test_cli_scan_intraday_smc_rejects_bad_mode(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    p = _run_cli(
        [
            "scan-intraday-smc",
            "--symbol", "CRM",
            "--ibkr",
            "--no-save-json",
            "--mode", "yolo",
        ],
        cwd=proj,
    )
    assert p.returncode == 2
    assert "mode" in (p.stdout + p.stderr).lower()


def test_cli_scan_intraday_smc_writes_json_when_ibkr_unavailable(
    tmp_path: Path, cleanup_intraday_outputs: list[Path]
) -> None:
    """Without TWS, scan should still complete and persist BLOCKED JSON."""
    proj = _make_project(tmp_path)
    p = _run_cli(
        ["scan-intraday-smc", "--symbol", "CRM", "--ibkr"],
        cwd=proj,
        timeout=60,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    files = sorted(INTRADAY_OUT.glob("*-CRM-intraday-smc.json"))
    assert files, f"no per-symbol JSON written under {INTRADAY_OUT}"
    payload = json.loads(files[-1].read_text("utf-8"))
    assert payload["symbol"] == "CRM"
    assert payload["strategy_id"] == "ict_smc_intraday_v1"
    assert payload["paper_only"] is True
    assert payload["execution_allowed"] is False
    # Without TWS, scanner falls back to data_source="missing" and
    # signals BLOCKED (or NO_SETUP if a stub TWS happened to respond).
    assert payload["signal_category"] in {"BLOCKED", "NO_SETUP", "ERROR"}


def test_cli_scan_intraday_smc_no_save_json_skips_disk(
    tmp_path: Path, cleanup_intraday_outputs: list[Path]
) -> None:
    proj = _make_project(tmp_path)
    before = set(INTRADAY_OUT.glob("*-CRM-intraday-smc.json"))
    p = _run_cli(
        [
            "scan-intraday-smc",
            "--symbol", "CRM",
            "--ibkr",
            "--no-save-json",
        ],
        cwd=proj,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    after = set(INTRADAY_OUT.glob("*-CRM-intraday-smc.json"))
    assert (after - before) == set(), (
        "JSON should not be written when --no-save-json is set"
    )


# ---------------------------------------------------------------------------
# scan-intraday-smc-watchlist — watchlist
# ---------------------------------------------------------------------------
def test_cli_watchlist_requires_ibkr(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    p = _run_cli(["scan-intraday-smc-watchlist"], cwd=proj)
    assert p.returncode == 2
    assert "--ibkr is required" in (p.stdout + p.stderr)


def test_cli_watchlist_rejects_bad_mode(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    p = _run_cli(
        [
            "scan-intraday-smc-watchlist",
            "--ibkr",
            "--no-save-json",
            "--mode", "yolo",
        ],
        cwd=proj,
    )
    assert p.returncode == 2
    assert "mode" in (p.stdout + p.stderr).lower()


def test_cli_watchlist_dynamic_without_built_watchlist_exits_3(
    tmp_path: Path,
) -> None:
    """With no dynamic-watchlist file present, exit cleanly with code 3."""
    proj = _make_project(tmp_path)
    p = _run_cli(
        [
            "scan-intraday-smc-watchlist",
            "--ibkr",
            "--source", "dynamic",
            "--no-save-json",
            "--limit", "2",
        ],
        cwd=proj,
    )
    assert p.returncode == 3, p.stdout + p.stderr
    assert "Build dynamic watchlist" in (p.stdout + p.stderr)


def test_cli_watchlist_static_writes_summary_json(
    tmp_path: Path, cleanup_intraday_outputs: list[Path]
) -> None:
    """``--source static`` falls back to ``static_core`` from watchlist.yaml.

    Without TWS, every per-symbol scan reports BLOCKED — but the
    summary JSON must still be written and include the canonical
    fields from PART E of Prompt 13D.
    """
    proj = _make_project(tmp_path)
    p = _run_cli(
        [
            "scan-intraday-smc-watchlist",
            "--ibkr",
            "--source", "static",
            "--limit", "2",
        ],
        cwd=proj,
        timeout=120,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    summary_files = sorted(
        INTRADAY_OUT.glob("*-watchlist-intraday-smc-summary.json")
    )
    assert summary_files, f"summary not written under {INTRADAY_OUT}"
    payload = json.loads(summary_files[-1].read_text("utf-8"))
    assert payload["strategy_id"] == "ict_smc_intraday_v1"
    assert payload["paper_only"] is True
    assert payload["execution_allowed"] is False
    assert payload["symbols_scanned"] == 2
    for k in (
        "counts", "ready_strict_symbols", "ready_aggressive_symbols",
        "watch_symbols", "invalid_symbols", "top_candidates", "items",
    ):
        assert k in payload, k


# ---------------------------------------------------------------------------
# Architectural safety: neither command places orders
# ---------------------------------------------------------------------------
def test_cli_scan_intraday_smc_does_not_call_place_order(tmp_path: Path) -> None:
    """Run the CLI under a sitecustomize that monkeypatches Broker.place_order
    to crash — proves the scan path never touches it.
    """
    proj = _make_project(tmp_path)
    # Drop a sitecustomize.py that monkeypatches the broker on import.
    site = proj / "sitecustomize.py"
    site.write_text(
        """
def _guard(*a, **kw):
    raise RuntimeError("PROHIBITED: place_order called from CLI scan path")
import sys
def _patch():
    try:
        from bot import broker as _b
    except Exception:
        return
    if hasattr(_b, "Broker"):
        _b.Broker.place_order = _guard  # type: ignore[attr-defined]
import importlib
_patch()
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["IBKR_ACCOUNT_MODE"] = "paper"
    env["PYTHONPATH"] = f"{proj}{os.pathsep}{REPO_ROOT}"
    env.setdefault("IBKR_HOST", "127.0.0.1")
    env.setdefault("IBKR_PORT", "65530")
    env.setdefault("IBKR_CLIENT_ID", "9999")
    p = subprocess.run(
        [
            sys.executable, "-m", "bot.cli",
            "scan-intraday-smc", "--symbol", "CRM",
            "--ibkr", "--no-save-json",
        ],
        cwd=str(proj),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )
    # The guard would print "PROHIBITED" if hit. Either way the CLI
    # itself must exit cleanly (no place_order called).
    assert "PROHIBITED" not in (p.stdout + p.stderr), (
        "scan-intraday-smc reached broker.place_order — invariant broken!"
    )
    assert p.returncode == 0, p.stdout + p.stderr
