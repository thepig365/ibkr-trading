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
    "scan-intraday-smc": "ICT/SMC Intraday V1 — single-symbol research scan (paper-only).",
    "scan-intraday-smc-watchlist": "ICT/SMC Intraday V1 — watchlist research scan (paper-only).",
    "fetch-candles": "Fetch historical OHLCV candles from IBKR into the local cache (read-only).",
    "backtest-intraday-smc": "Run the ICT/SMC Intraday backtest engine on a single symbol (cache-only, no broker).",
    "backtest-intraday-smc-watchlist": "Run the ICT/SMC Intraday backtest engine on multiple symbols (cache-only, no broker).",
    "backtest-report": "Print the latest backtest summary written under data/backtests/intraday/.",
    # Prompt 13F: ICT/SMC intraday paper bracket controls. PAPER ONLY —
    # the broker enforces account.mode=paper + every other invariant.
    # Although the names contain "paper", we still apply tight per-flag
    # validators below so the UI cannot smuggle ``--live`` etc.
    "auto-paper-intraday-smc": "Run one ICT/SMC Intraday paper bracket pass (paper account only).",
    "run-auto-paper-intraday-loop": "Run the ICT/SMC Intraday paper bracket loop (paper account only).",
    "intraday-paper-status": "Print intraday paper config + runtime + loop state (read-only).",
    "strategy-lab-engine-status": (
        "Read-only Strategy Lab engine + config snapshot (no TWS, no orders)."
    ),
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
        # Prompt 13F: intraday paper controls share the command runner;
        # belt-and-suspenders against an operator typing a live/market
        # token in the UI custom-args field.
        "--market",
        "--market-order",
        "--mkt",
        "--enable-live",
        "--allow-live",
        "--place-order",
        "--place_order",
        "--buy",
        "--sell",
        "--short",
        "--long",
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


# ---------------------------------------------------------------------------
# ICT/SMC Intraday scan validators (Prompt 13D)
# ---------------------------------------------------------------------------
# Tickers must be uppercase letters only (1–5 chars). Matches the
# canonical pattern used everywhere else in this module.
_SINGLE_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_INTRADAY_SOURCE_VALUES: frozenset[str] = frozenset({"static", "dynamic", "manual"})
_INTRADAY_DIRECTION_VALUES: frozenset[str] = frozenset({"auto", "long", "short"})
_INTRADAY_MODE_VALUES: frozenset[str] = frozenset(
    {"strict_and_aggressive", "strict_only", "aggressive_only"}
)
_INTRADAY_LIMIT_MIN = 1
_INTRADAY_LIMIT_MAX = 100

_SCAN_INTRADAY_SMC_FLAGS_VALUE: frozenset[str] = frozenset(
    {"--symbol", "--direction-hint", "--mode"}
)
_SCAN_INTRADAY_SMC_FLAGS_BOOL: frozenset[str] = frozenset(
    {"--ibkr", "--chart", "--telegram", "--save-json", "--no-save-json"}
)
_SCAN_INTRADAY_SMC_WL_FLAGS_VALUE: frozenset[str] = frozenset(
    {"--source", "--limit", "--mode"}
)
_SCAN_INTRADAY_SMC_WL_FLAGS_BOOL: frozenset[str] = frozenset(
    {"--ibkr", "--chart", "--telegram", "--save-json", "--no-save-json"}
)


def validate_scan_intraday_smc_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Validator for ``scan-intraday-smc`` (single symbol).

    Required: ``--symbol <TICKER>`` and ``--ibkr``.
    Accepted bool flags: ``--ibkr``, ``--chart``, ``--telegram``,
    ``--save-json`` / ``--no-save-json``.
    Accepted value flags: ``--symbol <TICKER>``,
    ``--direction-hint auto|long|short``,
    ``--mode strict_and_aggressive|strict_only|aggressive_only``.
    """
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token in _SCAN_INTRADAY_SMC_FLAGS_BOOL:
            flags[token] = True
            i += 1
            continue
        if token in _SCAN_INTRADAY_SMC_FLAGS_VALUE:
            if i + 1 >= len(args):
                return False, f"scan-intraday-smc: flag {token!r} requires a value."
            flags[token] = args[i + 1]
            i += 2
            continue
        return False, (
            f"scan-intraday-smc: unexpected token {token!r}; only "
            f"{sorted(_SCAN_INTRADAY_SMC_FLAGS_BOOL | _SCAN_INTRADAY_SMC_FLAGS_VALUE)} "
            f"are allowed."
        )

    if "--ibkr" not in flags:
        return False, "scan-intraday-smc: --ibkr is required."
    if "--symbol" not in flags:
        return False, "scan-intraday-smc: --symbol is required."
    sym = str(flags["--symbol"])
    if not _SINGLE_TICKER_RE.match(sym):
        return False, (
            f"scan-intraday-smc: --symbol {sym!r} must match "
            f"{_SINGLE_TICKER_RE.pattern} (uppercase, 1–5 letters)."
        )
    if "--direction-hint" in flags:
        v = str(flags["--direction-hint"])
        if v not in _INTRADAY_DIRECTION_VALUES:
            return False, (
                "scan-intraday-smc: --direction-hint must be one of "
                f"{sorted(_INTRADAY_DIRECTION_VALUES)}."
            )
    if "--mode" in flags:
        v = str(flags["--mode"])
        if v not in _INTRADAY_MODE_VALUES:
            return False, (
                "scan-intraday-smc: --mode must be one of "
                f"{sorted(_INTRADAY_MODE_VALUES)}."
            )
    if "--save-json" in flags and "--no-save-json" in flags:
        return False, "scan-intraday-smc: --save-json and --no-save-json are mutually exclusive."
    return True, ""


def validate_scan_intraday_smc_watchlist_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Validator for ``scan-intraday-smc-watchlist``.

    Required: ``--ibkr``.
    Accepted bool flags: ``--ibkr``, ``--chart``, ``--telegram``,
    ``--save-json`` / ``--no-save-json``.
    Accepted value flags: ``--source static|dynamic|manual``,
    ``--limit N`` (1..100),
    ``--mode strict_and_aggressive|strict_only|aggressive_only``.
    """
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token in _SCAN_INTRADAY_SMC_WL_FLAGS_BOOL:
            flags[token] = True
            i += 1
            continue
        if token in _SCAN_INTRADAY_SMC_WL_FLAGS_VALUE:
            if i + 1 >= len(args):
                return False, (
                    f"scan-intraday-smc-watchlist: flag {token!r} requires a value."
                )
            flags[token] = args[i + 1]
            i += 2
            continue
        return False, (
            f"scan-intraday-smc-watchlist: unexpected token {token!r}; only "
            f"{sorted(_SCAN_INTRADAY_SMC_WL_FLAGS_BOOL | _SCAN_INTRADAY_SMC_WL_FLAGS_VALUE)} "
            f"are allowed."
        )

    if "--ibkr" not in flags:
        return False, "scan-intraday-smc-watchlist: --ibkr is required."
    if "--source" in flags:
        v = str(flags["--source"]).lower()
        if v not in _INTRADAY_SOURCE_VALUES:
            return False, (
                "scan-intraday-smc-watchlist: --source must be one of "
                f"{sorted(_INTRADAY_SOURCE_VALUES)}."
            )
    if "--limit" in flags:
        try:
            n = int(str(flags["--limit"]))
        except (TypeError, ValueError):
            return False, "scan-intraday-smc-watchlist: --limit must be an integer."
        if not (_INTRADAY_LIMIT_MIN <= n <= _INTRADAY_LIMIT_MAX):
            return False, (
                "scan-intraday-smc-watchlist: --limit must be in "
                f"[{_INTRADAY_LIMIT_MIN}, {_INTRADAY_LIMIT_MAX}]."
            )
    if "--mode" in flags:
        v = str(flags["--mode"])
        if v not in _INTRADAY_MODE_VALUES:
            return False, (
                "scan-intraday-smc-watchlist: --mode must be one of "
                f"{sorted(_INTRADAY_MODE_VALUES)}."
            )
    if "--save-json" in flags and "--no-save-json" in flags:
        return False, (
            "scan-intraday-smc-watchlist: --save-json and --no-save-json are "
            "mutually exclusive."
        )
    return True, ""


# ---------------------------------------------------------------------------
# Backtest validators (Prompt 13E)
# ---------------------------------------------------------------------------
_BACKTEST_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BACKTEST_TIMEFRAME_VALUES: frozenset[str] = frozenset(
    {"1min", "5min", "30min", "4h", "daily"}
)
_BACKTEST_MODE_VALUES: frozenset[str] = frozenset(
    {"strict_only", "aggressive_only", "strict_and_aggressive"}
)
_BACKTEST_DIRECTION_VALUES: frozenset[str] = frozenset(
    {"long_only", "short_only", "both"}
)

_FETCH_CANDLES_FLAGS_VALUE: frozenset[str] = frozenset(
    {"--symbol", "--timeframe", "--start", "--end"}
)
_FETCH_CANDLES_FLAGS_BOOL: frozenset[str] = frozenset(
    {"--ibkr", "--force", "--use-rth", "--no-use-rth"}
)


def validate_fetch_candles_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Validator for ``fetch-candles``.

    Required: ``--symbol``, ``--timeframe``, ``--start``, ``--end``,
    ``--ibkr``. Optional: ``--force``, ``--use-rth`` / ``--no-use-rth``.
    """
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token in _FETCH_CANDLES_FLAGS_BOOL:
            flags[token] = True
            i += 1
            continue
        if token in _FETCH_CANDLES_FLAGS_VALUE:
            if i + 1 >= len(args):
                return False, f"fetch-candles: flag {token!r} requires a value."
            flags[token] = args[i + 1]
            i += 2
            continue
        return False, (
            f"fetch-candles: unexpected token {token!r}; only "
            f"{sorted(_FETCH_CANDLES_FLAGS_BOOL | _FETCH_CANDLES_FLAGS_VALUE)} are allowed."
        )
    if "--ibkr" not in flags:
        return False, "fetch-candles: --ibkr is required."
    for required in ("--symbol", "--timeframe", "--start", "--end"):
        if required not in flags:
            return False, f"fetch-candles: {required} is required."
    if not _SINGLE_TICKER_RE.match(str(flags["--symbol"])):
        return False, (
            f"fetch-candles: --symbol {flags['--symbol']!r} must match "
            f"{_SINGLE_TICKER_RE.pattern}."
        )
    if str(flags["--timeframe"]) not in _BACKTEST_TIMEFRAME_VALUES:
        return False, (
            "fetch-candles: --timeframe must be one of "
            f"{sorted(_BACKTEST_TIMEFRAME_VALUES)}."
        )
    if not _BACKTEST_DATE_RE.match(str(flags["--start"])):
        return False, "fetch-candles: --start must be YYYY-MM-DD."
    if not _BACKTEST_DATE_RE.match(str(flags["--end"])):
        return False, "fetch-candles: --end must be YYYY-MM-DD."
    if "--use-rth" in flags and "--no-use-rth" in flags:
        return False, "fetch-candles: --use-rth and --no-use-rth are mutually exclusive."
    return True, ""


_BACKTEST_SINGLE_FLAGS_VALUE: frozenset[str] = frozenset(
    {"--symbol", "--start", "--end", "--mode", "--direction"}
)
_BACKTEST_SINGLE_FLAGS_BOOL: frozenset[str] = frozenset(
    {"--chart", "--rth-only", "--no-rth-only"}
)


def validate_backtest_intraday_smc_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Validator for ``backtest-intraday-smc`` (single symbol)."""
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token in _BACKTEST_SINGLE_FLAGS_BOOL:
            flags[token] = True
            i += 1
            continue
        if token in _BACKTEST_SINGLE_FLAGS_VALUE:
            if i + 1 >= len(args):
                return False, f"backtest-intraday-smc: flag {token!r} requires a value."
            flags[token] = args[i + 1]
            i += 2
            continue
        return False, (
            f"backtest-intraday-smc: unexpected token {token!r}; only "
            f"{sorted(_BACKTEST_SINGLE_FLAGS_BOOL | _BACKTEST_SINGLE_FLAGS_VALUE)} are allowed."
        )
    for required in ("--symbol", "--start", "--end"):
        if required not in flags:
            return False, f"backtest-intraday-smc: {required} is required."
    if not _SINGLE_TICKER_RE.match(str(flags["--symbol"])):
        return False, (
            f"backtest-intraday-smc: --symbol {flags['--symbol']!r} must match "
            f"{_SINGLE_TICKER_RE.pattern}."
        )
    if not _BACKTEST_DATE_RE.match(str(flags["--start"])):
        return False, "backtest-intraday-smc: --start must be YYYY-MM-DD."
    if not _BACKTEST_DATE_RE.match(str(flags["--end"])):
        return False, "backtest-intraday-smc: --end must be YYYY-MM-DD."
    if "--mode" in flags and str(flags["--mode"]) not in _BACKTEST_MODE_VALUES:
        return False, (
            "backtest-intraday-smc: --mode must be one of "
            f"{sorted(_BACKTEST_MODE_VALUES)}."
        )
    if "--direction" in flags and str(flags["--direction"]) not in _BACKTEST_DIRECTION_VALUES:
        return False, (
            "backtest-intraday-smc: --direction must be one of "
            f"{sorted(_BACKTEST_DIRECTION_VALUES)}."
        )
    if "--rth-only" in flags and "--no-rth-only" in flags:
        return False, "backtest-intraday-smc: --rth-only and --no-rth-only are mutually exclusive."
    return True, ""


_BACKTEST_WL_FLAGS_VALUE: frozenset[str] = frozenset(
    {"--symbols", "--source", "--start", "--end", "--mode", "--direction", "--limit"}
)
_BACKTEST_WL_FLAGS_BOOL: frozenset[str] = frozenset(
    {"--chart", "--rth-only", "--no-rth-only"}
)
_BACKTEST_WL_SOURCES: frozenset[str] = frozenset({"static", "dynamic", "manual"})
_BACKTEST_WL_LIMIT_MIN = 1
_BACKTEST_WL_LIMIT_MAX = 100


def validate_backtest_intraday_smc_watchlist_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Validator for ``backtest-intraday-smc-watchlist``."""
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token in _BACKTEST_WL_FLAGS_BOOL:
            flags[token] = True
            i += 1
            continue
        if token in _BACKTEST_WL_FLAGS_VALUE:
            if i + 1 >= len(args):
                return False, (
                    f"backtest-intraday-smc-watchlist: flag {token!r} requires a value."
                )
            flags[token] = args[i + 1]
            i += 2
            continue
        return False, (
            f"backtest-intraday-smc-watchlist: unexpected token {token!r}; only "
            f"{sorted(_BACKTEST_WL_FLAGS_BOOL | _BACKTEST_WL_FLAGS_VALUE)} are allowed."
        )
    for required in ("--start", "--end"):
        if required not in flags:
            return False, f"backtest-intraday-smc-watchlist: {required} is required."
    if "--symbols" not in flags and "--source" not in flags:
        return False, (
            "backtest-intraday-smc-watchlist: provide either --symbols or --source."
        )
    if "--symbols" in flags and not _TICKER_LIST_RE.match(str(flags["--symbols"])):
        return False, (
            "backtest-intraday-smc-watchlist: --symbols must match "
            f"{_TICKER_LIST_RE.pattern} (uppercase, comma-separated)."
        )
    if "--source" in flags and str(flags["--source"]).lower() not in _BACKTEST_WL_SOURCES:
        return False, (
            "backtest-intraday-smc-watchlist: --source must be one of "
            f"{sorted(_BACKTEST_WL_SOURCES)}."
        )
    if not _BACKTEST_DATE_RE.match(str(flags["--start"])):
        return False, "backtest-intraday-smc-watchlist: --start must be YYYY-MM-DD."
    if not _BACKTEST_DATE_RE.match(str(flags["--end"])):
        return False, "backtest-intraday-smc-watchlist: --end must be YYYY-MM-DD."
    if "--mode" in flags and str(flags["--mode"]) not in _BACKTEST_MODE_VALUES:
        return False, (
            "backtest-intraday-smc-watchlist: --mode must be one of "
            f"{sorted(_BACKTEST_MODE_VALUES)}."
        )
    if "--direction" in flags and str(flags["--direction"]) not in _BACKTEST_DIRECTION_VALUES:
        return False, (
            "backtest-intraday-smc-watchlist: --direction must be one of "
            f"{sorted(_BACKTEST_DIRECTION_VALUES)}."
        )
    if "--limit" in flags:
        try:
            n = int(str(flags["--limit"]))
        except (TypeError, ValueError):
            return False, "backtest-intraday-smc-watchlist: --limit must be an integer."
        if not (_BACKTEST_WL_LIMIT_MIN <= n <= _BACKTEST_WL_LIMIT_MAX):
            return False, (
                "backtest-intraday-smc-watchlist: --limit must be in "
                f"[{_BACKTEST_WL_LIMIT_MIN}, {_BACKTEST_WL_LIMIT_MAX}]."
            )
    if "--rth-only" in flags and "--no-rth-only" in flags:
        return False, (
            "backtest-intraday-smc-watchlist: --rth-only and --no-rth-only are mutually exclusive."
        )
    return True, ""


_BACKTEST_REPORT_FLAGS_VALUE: frozenset[str] = frozenset({"--path"})
_BACKTEST_REPORT_FLAGS_BOOL: frozenset[str] = frozenset({"--latest"})


def validate_backtest_report_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Validator for ``backtest-report`` — read-only print of latest summary."""
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token in _BACKTEST_REPORT_FLAGS_BOOL:
            flags[token] = True
            i += 1
            continue
        if token in _BACKTEST_REPORT_FLAGS_VALUE:
            if i + 1 >= len(args):
                return False, f"backtest-report: flag {token!r} requires a value."
            value = args[i + 1]
            # Reject anything that looks like a shell metachar / abs URL etc.
            if any(ch in value for ch in (";", "|", "&", "`", "$", "<", ">")):
                return False, "backtest-report: --path contains forbidden characters."
            flags[token] = value
            i += 2
            continue
        return False, (
            f"backtest-report: unexpected token {token!r}; only "
            f"{sorted(_BACKTEST_REPORT_FLAGS_BOOL | _BACKTEST_REPORT_FLAGS_VALUE)} are allowed."
        )
    return True, ""


# ---------------------------------------------------------------------------
# Intraday paper bracket validators (Prompt 13F)
# ---------------------------------------------------------------------------
# These commands are PAPER-ONLY at the broker layer; the validators below
# reject any unknown / dangerous flag and re-validate every numeric range.
_INTRADAY_PAPER_LIMIT_MIN = 1
_INTRADAY_PAPER_LIMIT_MAX = 100
_INTRADAY_PAPER_INTERVAL_MIN = 5
_INTRADAY_PAPER_INTERVAL_MAX = 3600
_INTRADAY_PAPER_HEARTBEAT_MIN = 1
_INTRADAY_PAPER_HEARTBEAT_MAX = 360

_AUTO_PAPER_INTRADAY_FLAGS_VALUE: frozenset[str] = frozenset(
    {"--source", "--limit"}
)
_AUTO_PAPER_INTRADAY_FLAGS_BOOL: frozenset[str] = frozenset(
    {"--telegram", "--no-telegram"}
)

_RUN_INTRADAY_LOOP_FLAGS_VALUE: frozenset[str] = frozenset(
    {"--source", "--limit", "--interval-seconds", "--heartbeat-minutes"}
)
_RUN_INTRADAY_LOOP_FLAGS_BOOL: frozenset[str] = frozenset(
    {
        "--telegram",
        "--no-telegram",
        "--market-hours-only",
        "--ignore-market-hours",
    }
)

_INTRADAY_PAPER_STATUS_FLAGS_BOOL: frozenset[str] = frozenset(
    {"--json"}
)

_STRATEGY_LAB_ENGINE_STATUS_FLAGS: frozenset[str] = frozenset({"--json"})


def _check_no_forbidden(command: str, args: tuple[str, ...]) -> tuple[bool, str]:
    for tok in args:
        low = tok.lower()
        if low in FORBIDDEN_ARG_TOKENS:
            return False, f"{command}: forbidden token {tok!r} in args."
        for needle in (";", "&&", "||", "|", "`", "$("):
            if needle in tok:
                return False, f"{command}: shell metacharacter in {tok!r}."
    return True, ""


def validate_auto_paper_intraday_smc_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Validator for ``auto-paper-intraday-smc`` (one paper bracket pass)."""
    ok, err = _check_no_forbidden("auto-paper-intraday-smc", args)
    if not ok:
        return ok, err
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token in _AUTO_PAPER_INTRADAY_FLAGS_BOOL:
            flags[token] = True
            i += 1
            continue
        if token in _AUTO_PAPER_INTRADAY_FLAGS_VALUE:
            if i + 1 >= len(args):
                return False, f"auto-paper-intraday-smc: flag {token!r} requires a value."
            flags[token] = args[i + 1]
            i += 2
            continue
        return False, (
            f"auto-paper-intraday-smc: unexpected token {token!r}; only "
            f"{sorted(_AUTO_PAPER_INTRADAY_FLAGS_BOOL | _AUTO_PAPER_INTRADAY_FLAGS_VALUE)} are allowed."
        )
    if "--source" in flags:
        v = str(flags["--source"]).lower()
        if v not in _INTRADAY_SOURCE_VALUES:
            return False, (
                "auto-paper-intraday-smc: --source must be one of "
                f"{sorted(_INTRADAY_SOURCE_VALUES)}."
            )
    if "--limit" in flags:
        try:
            n = int(str(flags["--limit"]))
        except (TypeError, ValueError):
            return False, "auto-paper-intraday-smc: --limit must be an integer."
        if not (_INTRADAY_PAPER_LIMIT_MIN <= n <= _INTRADAY_PAPER_LIMIT_MAX):
            return False, (
                "auto-paper-intraday-smc: --limit must be in "
                f"[{_INTRADAY_PAPER_LIMIT_MIN}, {_INTRADAY_PAPER_LIMIT_MAX}]."
            )
    if "--telegram" in flags and "--no-telegram" in flags:
        return False, "auto-paper-intraday-smc: --telegram and --no-telegram are mutually exclusive."
    return True, ""


def validate_run_auto_paper_intraday_loop_args(
    args: tuple[str, ...],
) -> tuple[bool, str]:
    """Validator for ``run-auto-paper-intraday-loop``."""
    ok, err = _check_no_forbidden("run-auto-paper-intraday-loop", args)
    if not ok:
        return ok, err
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token in _RUN_INTRADAY_LOOP_FLAGS_BOOL:
            flags[token] = True
            i += 1
            continue
        if token in _RUN_INTRADAY_LOOP_FLAGS_VALUE:
            if i + 1 >= len(args):
                return False, (
                    f"run-auto-paper-intraday-loop: flag {token!r} requires a value."
                )
            flags[token] = args[i + 1]
            i += 2
            continue
        return False, (
            f"run-auto-paper-intraday-loop: unexpected token {token!r}; only "
            f"{sorted(_RUN_INTRADAY_LOOP_FLAGS_BOOL | _RUN_INTRADAY_LOOP_FLAGS_VALUE)} are allowed."
        )
    if "--source" in flags:
        v = str(flags["--source"]).lower()
        if v not in _INTRADAY_SOURCE_VALUES:
            return False, (
                "run-auto-paper-intraday-loop: --source must be one of "
                f"{sorted(_INTRADAY_SOURCE_VALUES)}."
            )
    if "--limit" in flags:
        try:
            n = int(str(flags["--limit"]))
        except (TypeError, ValueError):
            return False, "run-auto-paper-intraday-loop: --limit must be an integer."
        if not (_INTRADAY_PAPER_LIMIT_MIN <= n <= _INTRADAY_PAPER_LIMIT_MAX):
            return False, (
                "run-auto-paper-intraday-loop: --limit must be in "
                f"[{_INTRADAY_PAPER_LIMIT_MIN}, {_INTRADAY_PAPER_LIMIT_MAX}]."
            )
    if "--interval-seconds" in flags:
        try:
            n = int(str(flags["--interval-seconds"]))
        except (TypeError, ValueError):
            return False, (
                "run-auto-paper-intraday-loop: --interval-seconds must be an integer."
            )
        if not (_INTRADAY_PAPER_INTERVAL_MIN <= n <= _INTRADAY_PAPER_INTERVAL_MAX):
            return False, (
                "run-auto-paper-intraday-loop: --interval-seconds must be in "
                f"[{_INTRADAY_PAPER_INTERVAL_MIN}, {_INTRADAY_PAPER_INTERVAL_MAX}]."
            )
    if "--heartbeat-minutes" in flags:
        try:
            n = int(str(flags["--heartbeat-minutes"]))
        except (TypeError, ValueError):
            return False, (
                "run-auto-paper-intraday-loop: --heartbeat-minutes must be an integer."
            )
        if not (_INTRADAY_PAPER_HEARTBEAT_MIN <= n <= _INTRADAY_PAPER_HEARTBEAT_MAX):
            return False, (
                "run-auto-paper-intraday-loop: --heartbeat-minutes must be in "
                f"[{_INTRADAY_PAPER_HEARTBEAT_MIN}, {_INTRADAY_PAPER_HEARTBEAT_MAX}]."
            )
    if "--telegram" in flags and "--no-telegram" in flags:
        return False, (
            "run-auto-paper-intraday-loop: --telegram and --no-telegram are mutually exclusive."
        )
    if "--market-hours-only" in flags and "--ignore-market-hours" in flags:
        return False, (
            "run-auto-paper-intraday-loop: --market-hours-only and "
            "--ignore-market-hours are mutually exclusive."
        )
    return True, ""


def validate_intraday_paper_status_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Validator for ``intraday-paper-status`` (read-only print)."""
    ok, err = _check_no_forbidden("intraday-paper-status", args)
    if not ok:
        return ok, err
    for token in args:
        if token not in _INTRADAY_PAPER_STATUS_FLAGS_BOOL:
            return False, (
                f"intraday-paper-status: unexpected token {token!r}; only "
                f"{sorted(_INTRADAY_PAPER_STATUS_FLAGS_BOOL)} are allowed."
            )
    return True, ""


def validate_strategy_lab_engine_status_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Validator for ``strategy-lab-engine-status`` (read-only, no TWS)."""
    ok, err = _check_no_forbidden("strategy-lab-engine-status", args)
    if not ok:
        return ok, err
    for token in args:
        if token not in _STRATEGY_LAB_ENGINE_STATUS_FLAGS:
            return False, (
                f"strategy-lab-engine-status: unexpected token {token!r}; only "
                f"{sorted(_STRATEGY_LAB_ENGINE_STATUS_FLAGS)} or no args are allowed."
            )
    if len([t for t in args if t == "--json"]) > 1:
        return False, "strategy-lab-engine-status: duplicate --json."
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
    if command == "scan-intraday-smc":
        return validate_scan_intraday_smc_args(args)
    if command == "scan-intraday-smc-watchlist":
        return validate_scan_intraday_smc_watchlist_args(args)
    if command == "fetch-candles":
        return validate_fetch_candles_args(args)
    if command == "backtest-intraday-smc":
        return validate_backtest_intraday_smc_args(args)
    if command == "backtest-intraday-smc-watchlist":
        return validate_backtest_intraday_smc_watchlist_args(args)
    if command == "backtest-report":
        return validate_backtest_report_args(args)
    if command == "auto-paper-intraday-smc":
        return validate_auto_paper_intraday_smc_args(args)
    if command == "run-auto-paper-intraday-loop":
        return validate_run_auto_paper_intraday_loop_args(args)
    if command == "intraday-paper-status":
        return validate_intraday_paper_status_args(args)
    if command == "strategy-lab-engine-status":
        return validate_strategy_lab_engine_status_args(args)
    return True, ""
