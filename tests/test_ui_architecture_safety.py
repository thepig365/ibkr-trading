"""Architecture safety tests for the local Strategy Lab UI.

These tests are intentionally a bit "paranoid" — they enforce the
properties that make the Vercel split (UI tier vs Worker tier) safe:

1. Importing :mod:`bot_ui.app` must NOT pull in any module that talks
   to IBKR (``bot.broker``, ``bot.ibkr_client``, ``ib_async``).
2. Creating the FastAPI app must NOT open a TWS connection — it must
   not cause any of those modules to load either.
3. The default bind host is 127.0.0.1 (loopback), never 0.0.0.0.
4. The deployment-architecture and vercel-worker-architecture docs
   exist and explicitly call out the "no live trading" rule.
5. The UI source code does not import :mod:`bot.broker` or
   :mod:`bot.ibkr_client` anywhere (grep-style guard).
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOT_UI_DIR = PROJECT_ROOT / "bot_ui"
DOCS_DIR = PROJECT_ROOT / "docs"


def _run_python(code: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_bot_ui_imports_do_not_pull_in_broker_or_ibkr_client() -> None:
    """A clean Python process importing bot_ui.app must NOT load IBKR."""
    code = (
        "import sys, json\n"
        "import bot_ui.app  # noqa: F401\n"
        "loaded = sorted(m for m in sys.modules if m == 'bot.broker' or "
        "                m == 'bot.ibkr_client' or m.startswith('ib_async') or "
        "                m.startswith('ib_insync'))\n"
        "print(json.dumps(loaded))\n"
    )
    rc, out, err = _run_python(code)
    assert rc == 0, err
    loaded = sorted(eval(out.strip()))  # noqa: S307 - trusted JSON list
    assert loaded == [], (
        f"UI import pulled in IBKR-related modules: {loaded}. The UI must stay "
        "decoupled from broker code."
    )


def test_create_app_does_not_load_broker_or_ibkr_client() -> None:
    """create_app() must also stay clean (no startup TWS connection)."""
    code = (
        "import sys, json\n"
        "from bot_ui.app import create_app\n"
        "app = create_app()\n"
        "loaded = sorted(m for m in sys.modules if m == 'bot.broker' or "
        "                m == 'bot.ibkr_client' or m.startswith('ib_async') or "
        "                m.startswith('ib_insync'))\n"
        "print(json.dumps(loaded))\n"
    )
    rc, out, err = _run_python(code)
    assert rc == 0, err
    loaded = sorted(eval(out.strip()))  # noqa: S307 - trusted JSON list
    assert loaded == [], (
        f"create_app() pulled in IBKR-related modules: {loaded}."
    )


def test_default_host_is_loopback() -> None:
    from bot_ui.app import DEFAULT_HOST, DEFAULT_PORT

    assert DEFAULT_HOST == "127.0.0.1"
    assert DEFAULT_PORT == 8765


def test_server_arg_parser_defaults_to_loopback() -> None:
    from bot_ui.server import _build_arg_parser

    args = _build_arg_parser().parse_args([])
    assert args.host in {"127.0.0.1", "localhost"}, args.host


def test_no_ui_module_imports_broker_or_ibkr_client() -> None:
    """Static check: UI source code never imports the IBKR-touching modules."""
    bad_imports = re.compile(
        r"^\s*(?:from|import)\s+(?:bot\.broker|bot\.ibkr_client|ib_async|ib_insync)\b",
        re.MULTILINE,
    )
    offenders: list[str] = []
    for path in BOT_UI_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if bad_imports.search(text):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == [], (
        f"These UI files import broker/IBKR code: {offenders}. The UI tier "
        "must remain isolated; broker calls happen in the Worker tier."
    )


def test_deployment_architecture_doc_exists_and_says_paper_only() -> None:
    p = DOCS_DIR / "deployment-architecture.md"
    assert p.exists(), "docs/deployment-architecture.md is required by Prompt 13A"
    text = p.read_text(encoding="utf-8").lower()
    assert "paper" in text
    assert "vercel" in text or "worker" in text


def test_vercel_worker_architecture_doc_exists_and_explains_split() -> None:
    p = DOCS_DIR / "vercel-worker-architecture.md"
    assert p.exists(), "docs/vercel-worker-architecture.md is required by Prompt 13A"
    text = p.read_text(encoding="utf-8").lower()
    assert "vercel" in text
    assert "worker" in text
    assert "paper" in text


def test_env_example_documents_strategy_lab_settings_and_paper_mode() -> None:
    p = PROJECT_ROOT / ".env.example"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    for needed in [
        "STRATEGY_LAB_HOST",
        "STRATEGY_LAB_PORT",
        "STRATEGY_LAB_BACKEND",
        "IBKR_ACCOUNT_MODE=paper",
    ]:
        assert needed in text, f".env.example missing {needed!r}"


def test_runtime_dirs_are_gitignored() -> None:
    p = PROJECT_ROOT / ".gitignore"
    text = p.read_text(encoding="utf-8")
    for needed in [
        "data/runtime/",
        "data/auto_paper_loop/",
        "config/settings.local.yaml",
        ".env",
    ]:
        assert needed in text, f".gitignore missing {needed!r}"
