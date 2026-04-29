"""launchd Forex helper scripts syntax."""

from __future__ import annotations

from pathlib import Path

import subprocess

REPO_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
FOREX_SHELL = [
    "run_forex_auto_paper_supervisor.sh",
    "forex_auto_paper_launchd_wrapper.sh",
    "install_forex_auto_paper_launchd.sh",
    "uninstall_forex_auto_paper_launchd.sh",
    "status_forex_auto_paper_launchd.sh",
]


def test_forex_launchd_shell_scripts_syntax_ok() -> None:
    for name in FOREX_SHELL:
        p = REPO_SCRIPTS / name
        assert p.is_file(), p
        r = subprocess.run(
            ["bash", "-n", str(p)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, f"{name}: {r.stderr}"
