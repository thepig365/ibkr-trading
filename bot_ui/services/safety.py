"""Allowlist constants for the UI command queue.

This module is the SINGLE source of truth for what the local Strategy
Lab UI may run. Anything not in :data:`ALLOWED_COMMANDS` is rejected
hard by :class:`bot_ui.services.command_queue.LocalCommandRunner`.

Rules:

* Only read-only inspection commands and PAPER-only research / scan /
  reconcile commands are listed here.
* No order placement command is on this list. The bracket placement
  paths (``auto-paper-mtf``, ``run-auto-paper-mtf-loop``) are
  intentionally **not** here so the UI can never trigger a write
  through the broker, even by mistake.
* Adding a new command requires:
    1. Adding it to :data:`ALLOWED_COMMANDS`.
    2. Documenting it in ``docs/deployment-architecture.md``.
    3. Updating :mod:`tests.test_ui_command_runner`.
"""

from __future__ import annotations

# Subcommand of ``python -m bot.cli`` -> human-readable description.
ALLOWED_COMMANDS: dict[str, str] = {
    "paper-reconcile": "Read-only paper reconciliation (broker vs local).",
    "refresh-paper-account-state": (
        "Force a fresh account + positions snapshot from the broker (paper)."
    ),
    "build-watchlist": "Rebuild the dynamic watchlist for today.",
    "scan-mtf-smc-watchlist": "Run MTF SMC/ICT scan over the current watchlist.",
    "mtf-near-alignment-alert": "Surface near-alignment candidates from latest scan.",
    "research-report": "Generate a research report (Perplexity if configured).",
    "research-status": "Show research module status / last run.",
    "macro-calendar": "Show the macro economic calendar relevant to today.",
}

# Commands that are explicitly forbidden, even if a future code change
# accidentally adds them via ``ALLOWED_COMMANDS``. Belt + suspenders so
# one careless edit cannot turn the UI into an order-placement surface.
FORBIDDEN_COMMAND_TOKENS: frozenset[str] = frozenset(
    {
        "place_order",
        "place-order",
        "bracket",
        "live",
        "auto-paper-mtf",
        "run-auto-paper-mtf-loop",
        "telegram-listen",
        "run-scheduler",
    }
)

# Argument tokens that are categorically rejected. We never want the UI
# to grow the ability to pass shell metacharacters or to opt into live.
FORBIDDEN_ARG_TOKENS: frozenset[str] = frozenset(
    {
        ";",
        "&&",
        "|",
        "&",
        ">",
        "<",
        "`",
        "$(",
        "--live",
        "--enable-live-trading",
    }
)


def is_forbidden(command: str) -> bool:
    """Return True if the command name itself is on the deny-list."""
    if not command:
        return True
    lowered = command.strip().lower()
    return any(tok in lowered for tok in FORBIDDEN_COMMAND_TOKENS)


def is_allowed(command: str) -> bool:
    """Return True only if the command is explicitly allowlisted and not forbidden."""
    if is_forbidden(command):
        return False
    return command in ALLOWED_COMMANDS
