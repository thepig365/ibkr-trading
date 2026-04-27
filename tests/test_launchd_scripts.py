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


def test_plist_uses_app_support_workdir_and_env_repo() -> None:
    pl = (SCRIPTS / "com.strategy-lab.full-auto-paper.plist").read_text(encoding="utf-8")
    assert "__WRAPPER_SH__" in pl
    assert "__WORK_DIR__" in pl
    assert "STRATEGY_LAB_REPO_DIR" in pl
    assert "__REPO_ROOT__" in pl
    assert "StartInterval" in pl
    assert "__OUT_LOG__" in pl and "__ERR_LOG__" in pl


def test_install_warns_on_documents_path() -> None:
    t = (SCRIPTS / "install_full_auto_paper_launchd.sh").read_text(encoding="utf-8")
    assert "Documents" in t
    assert "STRATEGY_LAB_REPO_DIR" in t
    assert "TCC" in t or "Full Disk Access" in t


def test_status_diagnoses_operation_not_permitted() -> None:
    t = (SCRIPTS / "status_full_auto_paper_launchd.sh").read_text(encoding="utf-8")
    assert "Operation not permitted" in t
    assert "StrategyLab" in t or "Documents" in t


def test_launchd_wrapper_no_secrets_uses_project_env() -> None:
    w = (SCRIPTS / "strategy_lab_launchd_wrapper.sh").read_text(encoding="utf-8")
    assert "IBKR_TRADING_PROJECT_ROOT" in w
    assert "PYTHONPATH" in w
    assert "TELEGRAM" not in w
    assert ".env" not in w
    assert "place-order" not in w.lower()
    assert "run-full-auto-paper-supervisor" in w


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
        "strategy_lab_launchd_wrapper.sh",
    ],
)
def test_scripts_are_executable(name: str) -> None:
    p = SCRIPTS / name
    assert p.is_file()
    assert p.stat().st_mode & 0o111, f"{name} should be executable"
