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

import re

# Subcommand of ``python -m bot.cli`` -> human-readable description.
ALLOWED_COMMANDS: dict[str, str] = {
    "paper-reconcile": "Read-only paper reconciliation (broker vs local).",
    "refresh-paper-account-state": (
        "Force a fresh account + positions snapshot from the broker (paper)."
    ),
    "build-watchlist": "Rebuild the dynamic watchlist for today.",
    "scan-mtf-smc-watchlist": "Run MTF SMC/ICT scan over the current watchlist.",
    "mtf-near-alignment-alert": "Surface near-alignment candidates from latest scan.",
    "research-report": "Generate the v2 Research Intelligence report (paper-only).",
    "research-status": "Show freshness / health of the latest research report.",
    "macro-calendar": "Show the manual macro economic calendar (config/macro_calendar.yaml).",
    "ibkr-news-status": "Probe IBKR news provider entitlements (read-only connect).",
    "ibkr-news-fetch": "Fetch IBKR news for symbols and cache it (read-only connect).",
    "strategy-list": "List every registered strategy with metadata + enable status.",
    "strategy-info": "Show full metadata + runtime config for one strategy.",
    "strategy-status": "Report freshness of per-strategy scan files.",
    "strategy-scan": "Run a single strategy scan via the multi-strategy engine (research-only).",
    "multi-strategy-scan": "Run every enabled strategy via the engine (research-only).",
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


# ---------------------------------------------------------------------------
# Per-command argument validators
# ---------------------------------------------------------------------------
# Strict comma-separated UPPER-case tickers, max 5 chars each, no spaces.
_TICKER_LIST_RE = re.compile(r"^[A-Z]{1,5}(?:,[A-Z]{1,5})*$")

_IBKR_NEWS_FETCH_LIMIT_MIN = 1
_IBKR_NEWS_FETCH_LIMIT_MAX = 200
_IBKR_NEWS_FETCH_ALLOWED_FLAGS: frozenset[str] = frozenset({"--symbols", "--limit"})


def validate_ibkr_news_fetch_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Tight validator for ``ibkr-news-fetch``.

    Required: ``--symbols AAPL,TSLA,...`` (comma-separated UPPER tickers).
    Optional: ``--limit N`` where 1 <= N <= 200.
    Anything else (extra positional args, unknown flags, junk) is rejected.
    """
    flags: dict[str, str] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token not in _IBKR_NEWS_FETCH_ALLOWED_FLAGS:
            return False, (
                f"ibkr-news-fetch: unexpected token {token!r}; only "
                f"{sorted(_IBKR_NEWS_FETCH_ALLOWED_FLAGS)} are allowed."
            )
        if i + 1 >= len(args):
            return False, f"ibkr-news-fetch: flag {token!r} requires a value."
        flags[token] = args[i + 1]
        i += 2

    if "--symbols" not in flags:
        return False, "ibkr-news-fetch: --symbols is required."

    symbols_raw = flags["--symbols"]
    if not _TICKER_LIST_RE.match(symbols_raw):
        return False, (
            "ibkr-news-fetch: --symbols must match "
            "^[A-Z]{1,5}(,[A-Z]{1,5})*$ (e.g. AAPL,TSLA,NVDA)."
        )

    if "--limit" in flags:
        try:
            n = int(flags["--limit"])
        except ValueError:
            return False, "ibkr-news-fetch: --limit must be an integer."
        if not (_IBKR_NEWS_FETCH_LIMIT_MIN <= n <= _IBKR_NEWS_FETCH_LIMIT_MAX):
            return False, (
                f"ibkr-news-fetch: --limit must be in "
                f"[{_IBKR_NEWS_FETCH_LIMIT_MIN}, {_IBKR_NEWS_FETCH_LIMIT_MAX}]."
            )

    return True, ""


# Strict YYYY-MM-DD pattern for macro-calendar --date.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MACRO_CALENDAR_ALLOWED_FLAGS: frozenset[str] = frozenset({"--today", "--date"})


def validate_macro_calendar_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Allow only ``--today`` and ``--date YYYY-MM-DD``.

    Both are optional; together they're mutually exclusive in practice
    (the CLI handles precedence).
    """
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--today":
            i += 1
            continue
        if token == "--date":
            if i + 1 >= len(args):
                return False, "macro-calendar: --date requires YYYY-MM-DD."
            value = args[i + 1]
            if not _DATE_RE.match(value):
                return False, "macro-calendar: --date must be YYYY-MM-DD."
            i += 2
            continue
        if token not in _MACRO_CALENDAR_ALLOWED_FLAGS:
            return False, (
                f"macro-calendar: unexpected token {token!r}; only "
                f"{sorted(_MACRO_CALENDAR_ALLOWED_FLAGS)} are allowed."
            )
        i += 1
    return True, ""


# Optional flags accepted on research-report.
_RESEARCH_REPORT_ALLOWED_FLAGS: frozenset[str] = frozenset(
    {"--telegram", "--full", "--ibkr", "--no-ibkr"}
)


def validate_research_report_args(args: tuple[str, ...]) -> tuple[bool, str]:
    for token in args:
        if token not in _RESEARCH_REPORT_ALLOWED_FLAGS:
            return False, (
                f"research-report: unexpected token {token!r}; only "
                f"{sorted(_RESEARCH_REPORT_ALLOWED_FLAGS)} are allowed."
            )
    return True, ""


# ---------------------------------------------------------------------------
# Strategy registry validators (Prompt 13C)
# ---------------------------------------------------------------------------
# Strategy keys must match the registry's own pattern; this guard
# exists so a future UI bug cannot smuggle arbitrary tokens through
# ``--strategy``. The pattern intentionally mirrors
# ``bot.cli._STRATEGY_KEY_RE`` and the documented format in
# ``bot/strategies/base.py``.
_STRATEGY_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")

_STRATEGY_LIST_ALLOWED_FLAGS: frozenset[str] = frozenset({"--json"})
_STRATEGY_STATUS_ALLOWED_FLAGS: frozenset[str] = frozenset({"--json"})
_STRATEGY_SCAN_ALLOWED_FLAGS: frozenset[str] = frozenset({"--strategy", "--json"})
_MULTI_STRATEGY_SCAN_ALLOWED_FLAGS: frozenset[str] = frozenset(
    {"--include-disabled", "--json"}
)


def validate_strategy_list_args(args: tuple[str, ...]) -> tuple[bool, str]:
    for token in args:
        if token not in _STRATEGY_LIST_ALLOWED_FLAGS:
            return False, (
                f"strategy-list: unexpected token {token!r}; only "
                f"{sorted(_STRATEGY_LIST_ALLOWED_FLAGS)} are allowed."
            )
    return True, ""


def validate_strategy_status_args(args: tuple[str, ...]) -> tuple[bool, str]:
    for token in args:
        if token not in _STRATEGY_STATUS_ALLOWED_FLAGS:
            return False, (
                f"strategy-status: unexpected token {token!r}; only "
                f"{sorted(_STRATEGY_STATUS_ALLOWED_FLAGS)} are allowed."
            )
    return True, ""


def validate_strategy_info_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Exactly one positional strategy key, matching the strict pattern."""
    if len(args) != 1:
        return False, "strategy-info: exactly one strategy key is required."
    key = args[0]
    if not _STRATEGY_KEY_RE.match(key):
        return False, (
            f"strategy-info: invalid key {key!r}; must match {_STRATEGY_KEY_RE.pattern}."
        )
    return True, ""


def validate_strategy_scan_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Require ``--strategy <key>``; allow optional ``--json``."""
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--json":
            flags["--json"] = True
            i += 1
            continue
        if token == "--strategy":
            if i + 1 >= len(args):
                return False, "strategy-scan: --strategy requires a value."
            flags["--strategy"] = args[i + 1]
            i += 2
            continue
        if token not in _STRATEGY_SCAN_ALLOWED_FLAGS:
            return False, (
                f"strategy-scan: unexpected token {token!r}; only "
                f"{sorted(_STRATEGY_SCAN_ALLOWED_FLAGS)} are allowed."
            )
        i += 1
    if "--strategy" not in flags:
        return False, "strategy-scan: --strategy is required."
    key = str(flags["--strategy"])
    if not _STRATEGY_KEY_RE.match(key):
        return False, (
            f"strategy-scan: invalid --strategy {key!r}; must match {_STRATEGY_KEY_RE.pattern}."
        )
    return True, ""


def validate_multi_strategy_scan_args(args: tuple[str, ...]) -> tuple[bool, str]:
    for token in args:
        if token not in _MULTI_STRATEGY_SCAN_ALLOWED_FLAGS:
            return False, (
                f"multi-strategy-scan: unexpected token {token!r}; only "
                f"{sorted(_MULTI_STRATEGY_SCAN_ALLOWED_FLAGS)} are allowed."
            )
    return True, ""


def validate_args_for(command: str, args: tuple[str, ...]) -> tuple[bool, str]:
    """Dispatch to the per-command validator. Default: accept (no extra rules)."""
    if command == "ibkr-news-fetch":
        return validate_ibkr_news_fetch_args(args)
    if command == "macro-calendar":
        return validate_macro_calendar_args(args)
    if command == "research-report":
        return validate_research_report_args(args)
    if command == "strategy-list":
        return validate_strategy_list_args(args)
    if command == "strategy-info":
        return validate_strategy_info_args(args)
    if command == "strategy-status":
        return validate_strategy_status_args(args)
    if command == "strategy-scan":
        return validate_strategy_scan_args(args)
    if command == "multi-strategy-scan":
        return validate_multi_strategy_scan_args(args)
    return True, ""
