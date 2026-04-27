"""launchd helper scripts and plist (no install in tests)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"


def test_runner_script_uses_supervisor_not_raw_orders() -> None:
    sh = (SCRIPTS / "run_full_auto_paper_supervisor.sh").read_text(encoding="utf-8")
    assert "run-full-auto-paper-supervisor" in sh
    assert "place-order" not in sh.lower()
    assert "--live" not in sh


def test_plist_points_to_runner_and_repo_placeholder() -> None:
    pl = (SCRIPTS / "com.strategy-lab.full-auto-paper.plist").read_text(encoding="utf-8")
    assert "run_full_auto_paper_supervisor.sh" in pl
    assert "__REPO_ROOT__" in pl
    assert "StartInterval" in pl
    assert "launchd_full_auto.out.log" in pl


def test_install_script_exists() -> None:
    ins = SCRIPTS / "install_full_auto_paper_launchd.sh"
    assert ins.is_file()
    t = ins.read_text(encoding="utf-8")
    assert "LaunchAgents" in t
    assert "com.strategy-lab.full-auto-paper.plist" in t


def test_uninstall_does_not_rm_data() -> None:
    t = (SCRIPTS / "uninstall_full_auto_paper_launchd.sh").read_text(encoding="utf-8")
    assert "rm -rf" not in t
    assert "data/" not in t
    assert "rm -f" in t and "LaunchAgents" in t


@pytest.mark.parametrize(
    "name",
    [
        "run_full_auto_paper_supervisor.sh",
        "install_full_auto_paper_launchd.sh",
        "uninstall_full_auto_paper_launchd.sh",
        "status_full_auto_paper_launchd.sh",
    ],
)
def test_scripts_are_executable(name: str) -> None:
    p = SCRIPTS / name
    assert p.is_file()
    assert p.stat().st_mode & 0o111, f"{name} should be executable"
