"""Smoke + behaviour tests for the new ``strategy-*`` CLI commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["IBKR_ACCOUNT_MODE"] = "paper"
    return subprocess.run(
        [sys.executable, "-m", "bot.cli", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )


# ---------------------------------------------------------------------------
# strategy-list
# ---------------------------------------------------------------------------


def test_cli_strategy_list_json_includes_all_keys() -> None:
    p = _run(["strategy-list", "--json"])
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    keys = [r["key"] for r in payload["strategies"]]
    assert {"mtf_smc", "ict_smc_intraday_v1", "chanlun_intraday_v1", "orb_baseline"} <= set(keys)
    assert payload["defaults"]["paper_only"] is True
    assert payload["defaults"]["paper_execution_allowed"] is False


def test_cli_strategy_list_table_runs_without_error() -> None:
    p = _run(["strategy-list"])
    assert p.returncode == 0, p.stderr
    assert "mtf_smc" in p.stdout


# ---------------------------------------------------------------------------
# strategy-info
# ---------------------------------------------------------------------------


def test_cli_strategy_info_known_key() -> None:
    p = _run(["strategy-info", "mtf_smc"])
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["metadata"]["key"] == "mtf_smc"
    assert payload["runtime"]["enabled"] is True
    assert payload["runtime"]["paper_execution_allowed"] is False


def test_cli_strategy_info_unknown_key_exits_nonzero() -> None:
    p = _run(["strategy-info", "nope_strategy"])
    assert p.returncode != 0
    assert "not registered" in (p.stdout + p.stderr)


def test_cli_strategy_info_invalid_pattern_exits_nonzero() -> None:
    p = _run(["strategy-info", "MTF-SMC"])
    assert p.returncode != 0
    assert "Invalid" in (p.stdout + p.stderr)


# ---------------------------------------------------------------------------
# strategy-status
# ---------------------------------------------------------------------------


def test_cli_strategy_status_json_safe_with_no_scans(tmp_path: Path) -> None:
    p = _run(["strategy-status", "--json"])
    assert p.returncode == 0, p.stderr
    body = json.loads(p.stdout)
    assert body["paper_only"] is True
    assert body["execution_allowed"] is False
    assert "per_strategy" in body
    assert "mtf_smc" in body["per_strategy"]


# ---------------------------------------------------------------------------
# strategy-scan — the stub adapters can be exercised without IBKR
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["chanlun_intraday_v1", "ict_smc_intraday_v1", "orb_baseline"],
)
def test_cli_strategy_scan_stub_writes_per_strategy_file(key: str) -> None:
    p = _run(["strategy-scan", "--strategy", key, "--json"])
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["strategy_key"] == key
    assert payload["status"] == "not_implemented"
    assert payload["execution_allowed"] is False
    # The CLI also wrote a per-strategy JSON file under data/strategies/.
    out = REPO_ROOT / "data" / "strategies"
    files = list(out.glob(f"*-{key}-scan.json"))
    assert files, f"no scan file written for {key}"


def test_cli_strategy_scan_invalid_key_exits_nonzero() -> None:
    p = _run(["strategy-scan", "--strategy", "MTF-SMC"])
    assert p.returncode != 0
    assert "Invalid" in (p.stdout + p.stderr)


# ---------------------------------------------------------------------------
# multi-strategy-scan — empty enabled set still writes a snapshot
# ---------------------------------------------------------------------------


def test_cli_multi_strategy_scan_with_only_stubs_via_include_disabled() -> None:
    """Force include_disabled but rely on the stubs returning fast.

    We cannot exercise mtf_smc without IBKR, but include_disabled makes
    every stub run too. ``mtf_smc`` will likely return ``status="error"``
    because there is no live TWS — that is acceptable: the engine catches
    the exception and we get a JSON snapshot regardless.
    """
    p = _run(["multi-strategy-scan", "--include-disabled", "--json"])
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["paper_only"] is True
    assert payload["execution_allowed"] is False
    statuses = {r["strategy_key"]: r["status"] for r in payload["results"]}
    for key in ("ict_smc_intraday_v1", "chanlun_intraday_v1", "orb_baseline"):
        assert statuses.get(key) == "not_implemented"
