"""Launchd template for Telegram command listener: StrategyLab path, no Documents."""

from __future__ import annotations

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def test_telegram_listener_plist_template_has_placeholders() -> None:
    t = (SCRIPTS / "com.strategy-lab.telegram-listener.plist").read_text(
        encoding="utf-8"
    )
    assert "com.strategy-lab.telegram-listener" in t
    assert "__REPO_ROOT__" in t
    assert "Claude Folders" not in t


def test_install_script_references_paths() -> None:
    t = (SCRIPTS / "install_telegram_command_listener_launchd.sh").read_text(
        encoding="utf-8"
    )
    assert "com.strategy-lab.telegram-listener" in t
    assert "telegram_command_listener_wrapper.sh" in t


def test_uninstall_references_label() -> None:
    t = (SCRIPTS / "uninstall_telegram_command_listener_launchd.sh").read_text(
        encoding="utf-8"
    )
    assert "com.strategy-lab.telegram-listener" in t


def test_wrapper_uses_mkdir_lock_dir() -> None:
    t = (SCRIPTS / "telegram_command_listener_wrapper.sh").read_text(encoding="utf-8")
    assert "lock.run" in t
    assert "mkdir" in t
    assert "STRATEGY_LAB_REPO_DIR" in t
