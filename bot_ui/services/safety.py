"""Allowlist constants for the UI command queue.

This module is the SINGLE source of truth for what the local Strategy
Lab UI may run. Anything not in :data:`ALLOWED_COMMANDS` is rejected
hard by :class:`bot_ui.services.command_queue.LocalCommandRunner`.

Rules:

* Read-only commands and **PAPER-only** intraday/MTF flows are listed here.
* MTF bracket commands (``auto-paper-mtf``, ``run-auto-paper-mtf-loop``) are
  **not** allowlisted. ICT/SMC intraday paper paths (``auto-paper-intraday-smc``,
  ``first-paper-pass``, ``run-automatic-paper-engine``) **are** allowlisted with
  strict argument validation — they may submit **paper** LIMIT brackets only.
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
    "candle-coverage": "Read-only check of local 1m cache coverage for a date range (no IBKR, no backtest).",
    "backtest-oneclick": (
        "Check 1m coverage, fetch missing 1m from IBKR if needed, then run intraday backtest (read-only data fetch; no orders)."
    ),
    "backtest-intraday-smc": "Run the ICT/SMC Intraday backtest engine on a single symbol (cache-only, no broker).",
    "backtest-intraday-smc-watchlist": "Run the ICT/SMC Intraday backtest engine on multiple symbols (cache-only, no broker).",
    "backtest-report": "Print the latest backtest summary written under data/backtests/intraday/.",
    "build-edge-profile": (
        "Backtest one symbol, write ticker edge profile JSON/MD (cache; optional --fetch for IBKR candles)."
    ),
    "build-edge-profiles": (
        "Backtest a symbol basket and save ranked edge profiles under data/edge_profiles/ (optional --fetch)."
    ),
    "edge-profile-report": "Print the latest data/edge_profiles/*-edge-profiles.json path (read-only, no IBKR).",
    # Prompt 13F: ICT/SMC intraday paper bracket controls. PAPER ONLY —
    # the broker enforces account.mode=paper + every other invariant.
    # Although the names contain "paper", we still apply tight per-flag
    # validators below so the UI cannot smuggle ``--live`` etc.
    "auto-paper-intraday-smc": "Run one ICT/SMC Intraday paper bracket pass (paper account only).",
    "ibkr-session-status": "Read IBKR session summary (TWS/IB; explicit button only).",
    "open-orders": "List open orders at IBKR (read-only; explicit button).",
    "portfolio": "List positions + account snapshot at IBKR (read-only; explicit button).",
    "intraday-paper-status": "Print intraday paper config + runtime + loop state (read-only).",
    "auto-loop-readiness": (
        "Read-only checklist before run-auto-paper-intraday-loop; optional --json / --probe-ibkr."
    ),
    "eod-paper-checklist": "Print read-only EOD paper review CLI sequence (no orders, no IBKR, no email).",
    "news-monitor-readiness": "Read-only news monitor env/config snapshot (no provider fetch, no orders).",
    "email-config-status": "Read-only Resend + recipient readiness (booleans only, no network).",
    "market-news-check": "Score market headlines (Finnhub/FMP); optional Telegram; never places trades.",
    "strategy-lab-engine-status": (
        "Read-only Strategy Lab engine + config snapshot (no TWS, no orders)."
    ),
    "engine-status": "Full read-only engine snapshot: config, latest artifacts, optional UI /healthz.",
    "paper-activation-status": "Show local paper activation (settings.local + runtime; optional --probe-ibkr).",
    "write-paper-local-config": "Merge safe PAPER keys into config/settings.local.yaml only (--write to apply).",
    "intraday-paper-on": "Set data/runtime/intraday_auto_paper_enabled = 1 (no orders).",
    "intraday-paper-off": "Set data/runtime/intraday_auto_paper_enabled = 0 (no orders).",
    "paper-readiness-check": "Intraday paper pre-flight (optional --probe-ibkr / --scan).",
    "first-paper-pass": "One controlled pass: readiness then auto-paper-intraday-smc (no loop).",
    "automatic-paper-engine-readiness": (
        "Read-only gates for run-automatic-paper-engine; optional --probe-ibkr."
    ),
    "run-automatic-paper-engine": (
        "ICT/SMC automatic paper session loop (PAPER LIMIT brackets; long-running; controlled flags)."
    ),
    "full-auto-paper-readiness": (
        "Read-only gates for run-full-auto-paper-supervisor; optional --probe-ibkr."
    ),
    "run-full-auto-paper-supervisor": (
        "Full-auto paper supervisor — outer loop, Telegram blockers, ICT/SMC engine (paper only)."
    ),
    "paper-daily-report": "Generate data/reports/paper daily JSON+MD (file-based; no IBKR).",
    "paper-weekly-report": "Generate data/reports/paper weekly JSON+MD (file-based; no IBKR).",
    "data-status": "Show disk usage for local data/ categories (read-only).",
    "data-cleanup": "List or remove old ephemeral files; UI may only use --dry-run.",
    "premarket-brief": "Generate Strategy Lab pre-market brief (read-only; never trades).",
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
        "run-auto-paper-intraday-loop",
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
        # Note: do not add bare "--market" — it substring-matches
        # --market-moving-only (market-news-check).
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
    {"--telegram", "--full", "--ibkr", "--no-ibkr", "--email"}
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
_BACKTEST_REPORT_FLAGS_BOOL: frozenset[str] = frozenset({"--latest", "--email"})


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
# Ticker edge profile CLI (Prompt 13L-alt) — cache + optional --fetch
# ---------------------------------------------------------------------------
_STRATEGY_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,70}$")
_EDGE_MIN_TRADES_MIN = 1
_EDGE_MIN_TRADES_MAX = 2000
_EDGE_TOP_MIN = 1
_EDGE_TOP_MAX = 500
_EDGE_BUILD_SINGLE_FLAGS: frozenset[str] = frozenset(
    {
        "--symbol",
        "--start",
        "--end",
        "--strategy",
        "--mode",
        "--direction",
        "--min-trades",
    }
)
_EDGE_BUILD_SINGLE_BOOL: frozenset[str] = frozenset({"--fetch"})
_EDGE_BUILD_MULTI_FLAGS: frozenset[str] = frozenset(
    {
        "--symbols",
        "--start",
        "--end",
        "--strategy",
        "--mode",
        "--direction",
        "--min-trades",
        "--top",
    }
)
_EDGE_BUILD_MULTI_BOOL: frozenset[str] = frozenset({"--fetch"})
_EDGE_REPORT_FLAGS_BOOL: frozenset[str] = frozenset({"--latest"})


def validate_build_edge_profile_args(
    args: tuple[str, ...],
) -> tuple[bool, str]:
    """Single-symbol edge profile: same backtest date/symbol/mode as intraday backtest + --fetch/--min-trades/--strategy."""
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token in _EDGE_BUILD_SINGLE_BOOL:
            flags[token] = True
            i += 1
            continue
        if token in _EDGE_BUILD_SINGLE_FLAGS:
            if i + 1 >= len(args):
                return False, f"build-edge-profile: flag {token!r} requires a value."
            flags[token] = args[i + 1]
            i += 2
            continue
        return False, (
            f"build-edge-profile: unexpected token {token!r}; only "
            f"{sorted(_EDGE_BUILD_SINGLE_BOOL | _EDGE_BUILD_SINGLE_FLAGS)} are allowed."
        )
    for req in ("--symbol", "--start", "--end"):
        if req not in flags:
            return False, f"build-edge-profile: {req} is required."
    if not _SINGLE_TICKER_RE.match(str(flags["--symbol"])):
        return False, "build-edge-profile: --symbol must be a single UPPER ticker."
    for key in ("--start", "--end"):
        if not _BACKTEST_DATE_RE.match(str(flags[key])):
            return False, f"build-edge-profile: {key} must be YYYY-MM-DD."
    if str(flags.get("--start", "")) > str(flags.get("--end", "z")):
        return False, "build-edge-profile: --start must be <= --end."
    if "--mode" in flags and str(flags["--mode"]) not in _BACKTEST_MODE_VALUES:
        return False, (
            "build-edge-profile: --mode must be one of "
            f"{sorted(_BACKTEST_MODE_VALUES)}."
        )
    if "--direction" in flags and str(
        flags["--direction"]
    ) not in _BACKTEST_DIRECTION_VALUES:
        return False, (
            "build-edge-profile: --direction must be one of "
            f"{sorted(_BACKTEST_DIRECTION_VALUES)}."
        )
    if "--strategy" in flags and not _STRATEGY_ID_RE.match(
        str(flags["--strategy"])
    ):
        return False, "build-edge-profile: --strategy is invalid."
    if "--min-trades" in flags:
        try:
            mt = int(str(flags["--min-trades"]))
        except (TypeError, ValueError):
            return False, "build-edge-profile: --min-trades must be an integer."
        if not (
            _EDGE_MIN_TRADES_MIN <= mt <= _EDGE_MIN_TRADES_MAX
        ):
            return False, "build-edge-profile: --min-trades out of range."
    return True, ""


def validate_build_edge_profiles_args(
    args: tuple[str, ...],
) -> tuple[bool, str]:
    """Basket edge profiles: comma tickers, dates, optional --top, --fetch."""
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token in _EDGE_BUILD_MULTI_BOOL:
            flags[token] = True
            i += 1
            continue
        if token in _EDGE_BUILD_MULTI_FLAGS:
            if i + 1 >= len(args):
                return False, f"build-edge-profiles: flag {token!r} requires a value."
            flags[token] = args[i + 1]
            i += 2
            continue
        return False, (
            f"build-edge-profiles: unexpected token {token!r}; only "
            f"{sorted(_EDGE_BUILD_MULTI_BOOL | _EDGE_BUILD_MULTI_FLAGS)} are allowed."
        )
    for req in ("--symbols", "--start", "--end"):
        if req not in flags:
            return False, f"build-edge-profiles: {req} is required."
    if not _TICKER_LIST_RE.match(str(flags["--symbols"])):
        return False, (
            "build-edge-profiles: --symbols must match "
            "^[A-Z]{1,5}(,[A-Z]{1,5})*$."
        )
    for key in ("--start", "--end"):
        if not _BACKTEST_DATE_RE.match(str(flags[key])):
            return False, f"build-edge-profiles: {key} must be YYYY-MM-DD."
    if str(flags.get("--start", "")) > str(flags.get("--end", "z")):
        return False, "build-edge-profiles: --start must be <= --end."
    if "--mode" in flags and str(flags["--mode"]) not in _BACKTEST_MODE_VALUES:
        return False, (
            "build-edge-profiles: --mode must be one of "
            f"{sorted(_BACKTEST_MODE_VALUES)}."
        )
    if "--direction" in flags and str(
        flags["--direction"]
    ) not in _BACKTEST_DIRECTION_VALUES:
        return False, (
            "build-edge-profiles: --direction must be one of "
            f"{sorted(_BACKTEST_DIRECTION_VALUES)}."
        )
    if "--strategy" in flags and not _STRATEGY_ID_RE.match(
        str(flags["--strategy"])
    ):
        return False, "build-edge-profiles: --strategy is invalid."
    if "--min-trades" in flags:
        try:
            mt = int(str(flags["--min-trades"]))
        except (TypeError, ValueError):
            return False, "build-edge-profiles: --min-trades must be an integer."
        if not (
            _EDGE_MIN_TRADES_MIN <= mt <= _EDGE_MIN_TRADES_MAX
        ):
            return False, "build-edge-profiles: --min-trades out of range."
    if "--top" in flags:
        try:
            top = int(str(flags["--top"]))
        except (TypeError, ValueError):
            return False, "build-edge-profiles: --top must be an integer."
        if not (_EDGE_TOP_MIN <= top <= _EDGE_TOP_MAX):
            return False, "build-edge-profiles: --top out of range."
    return True, ""


def validate_edge_profile_report_args(
    args: tuple[str, ...],
) -> tuple[bool, str]:
    """Read-only report; only ``--latest`` (optional) is allowed."""
    i = 0
    while i < len(args):
        token = args[i]
        if token in _EDGE_REPORT_FLAGS_BOOL:
            i += 1
            continue
        return False, (
            f"edge-profile-report: unexpected token {token!r}; only "
            f"{sorted(_EDGE_REPORT_FLAGS_BOOL)} is allowed (or no args)."
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

_INTRADAY_PAPER_STATUS_FLAGS_BOOL: frozenset[str] = frozenset(
    {"--json"}
)

_STRATEGY_LAB_ENGINE_STATUS_FLAGS: frozenset[str] = frozenset({"--json"})
_ENGINE_STATUS_FLAGS: frozenset[str] = frozenset({"--json", "--probe-ui"})


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


def validate_readonly_broker_view_args(
    name: str, args: tuple[str, ...]
) -> tuple[bool, str]:
    """``ibkr-session-status``, ``open-orders``, ``portfolio`` take no args."""
    ok, err = _check_no_forbidden(name, args)
    if not ok:
        return ok, err
    if args:
        return False, f"{name} does not accept arguments."
    return True, ""


_PAPER_REPORT_OUTPUT_DIR_RE = re.compile(
    r"^data/reports(/paper)?$|^data/reports/paper/?$"
)


def validate_paper_daily_report_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Tight allowlist: --latest / --today / --date, optional output dir under data/reports."""
    ok, err = _check_no_forbidden("paper-daily-report", args)
    if not ok:
        return ok, err
    if not args:
        return True, ""
    i = 0
    have_mode = 0
    while i < len(args):
        a = args[i]
        if a in {"--latest", "--today", "--no-markdown", "--no-save"}:
            if a in {"--latest", "--today"}:
                have_mode += 1
            i += 1
            continue
        if a == "--date":
            if i + 1 >= len(args):
                return False, "paper-daily-report: --date requires a value."
            d = str(args[i + 1])
            if not _DATE_RE.match(d):
                return False, "paper-daily-report: --date must be YYYY-MM-DD."
            have_mode += 1
            i += 2
            continue
        if a == "--output-dir":
            if i + 1 >= len(args):
                return False, "paper-daily-report: --output-dir requires a value."
            raw = str(args[i + 1]).strip()
            if ".." in raw or raw.startswith(("/", "~")):
                return False, "paper-daily-report: invalid --output-dir."
            if not _PAPER_REPORT_OUTPUT_DIR_RE.match(raw):
                return (
                    False,
                    "paper-daily-report: --output-dir must be data/reports or data/reports/paper.",
                )
            i += 2
            continue
        if a == "--markdown" or a == "--telegram" or a == "--email":
            i += 1
            continue
        return False, f"paper-daily-report: unexpected token {a!r}."
    if have_mode > 1:
        return (
            False,
            "paper-daily-report: use at most one of --latest, --today, or --date.",
        )
    return True, ""


def validate_paper_weekly_report_args(args: tuple[str, ...]) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("paper-weekly-report", args)
    if not ok:
        return ok, err
    if not args:
        return True, ""
    i = 0
    seen_start = seen_end = seen_latest = False
    while i < len(args):
        a = args[i]
        if a in {"--no-markdown", "--no-save", "--email"}:
            i += 1
            continue
        if a == "--latest":
            seen_latest = True
            i += 1
            continue
        if a == "--week-start":
            if i + 1 >= len(args):
                return False, "paper-weekly-report: --week-start requires a value."
            if not _DATE_RE.match(str(args[i + 1])):
                return False, "paper-weekly-report: --week-start must be YYYY-MM-DD."
            seen_start = True
            i += 2
            continue
        if a == "--week-end":
            if i + 1 >= len(args):
                return False, "paper-weekly-report: --week-end requires a value."
            if not _DATE_RE.match(str(args[i + 1])):
                return False, "paper-weekly-report: --week-end must be YYYY-MM-DD."
            seen_end = True
            i += 2
            continue
        if a == "--output-dir":
            if i + 1 >= len(args):
                return False, "paper-weekly-report: --output-dir requires a value."
            raw = str(args[i + 1]).strip()
            if ".." in raw or raw.startswith(("/", "~")):
                return False, "paper-weekly-report: invalid --output-dir."
            if not _PAPER_REPORT_OUTPUT_DIR_RE.match(raw):
                return (
                    False,
                    "paper-weekly-report: --output-dir must be data/reports or data/reports/paper.",
                )
            i += 2
            continue
        return False, f"paper-weekly-report: unexpected token {a!r}."
    if seen_latest and (seen_start or seen_end):
        return False, "paper-weekly-report: do not mix --latest with --week-start/--week-end."
    if (seen_start) ^ (seen_end):
        return False, "paper-weekly-report: provide both --week-start and --week-end, or use --latest."
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


def validate_engine_status_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """``engine-status`` — JSON and optional UI /healthz probe; no extra tokens."""
    ok, err = _check_no_forbidden("engine-status", args)
    if not ok:
        return ok, err
    for token in args:
        if token not in _ENGINE_STATUS_FLAGS:
            return False, (
                f"engine-status: unexpected token {token!r}; only "
                f"{sorted(_ENGINE_STATUS_FLAGS)} or combinations thereof (no values)."
            )
    if list(args).count("--json") > 1 or list(args).count("--probe-ui") > 1:
        return False, "engine-status: duplicate flag."
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


_PAPER_ACTIVATION_STATUS_FLAGS = frozenset({"--probe-ibkr"})

_AUTO_LOOP_READINESS_FLAGS: frozenset[str] = frozenset({"--json", "--probe-ibkr"})


def validate_auto_loop_readiness_args(args: tuple[str, ...]) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("auto-loop-readiness", args)
    if not ok:
        return ok, err
    for t in args:
        if t not in _AUTO_LOOP_READINESS_FLAGS:
            return (
                False,
                f"auto-loop-readiness: only {sorted(_AUTO_LOOP_READINESS_FLAGS)} or no args, got {t!r}.",
            )
    if args.count("--json") > 1 or args.count("--probe-ibkr") > 1:
        return False, "auto-loop-readiness: duplicate flag."
    return True, ""


_AUTO_ENG_READINESS_FLAGS = frozenset({"--json", "--no-json", "--probe-ibkr"})


def validate_automatic_paper_engine_readiness_args(
    args: tuple[str, ...],
) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("automatic-paper-engine-readiness", args)
    if not ok:
        return ok, err
    for t in args:
        if t not in _AUTO_ENG_READINESS_FLAGS:
            return False, f"automatic-paper-engine-readiness: unexpected token {t!r}."
    if args.count("--json") > 1 or args.count("--no-json") > 1:
        return False, "automatic-paper-engine-readiness: duplicate json flag."
    if "--json" in args and "--no-json" in args:
        return False, "automatic-paper-engine-readiness: --json and --no-json conflict."
    if args.count("--probe-ibkr") > 1:
        return False, "automatic-paper-engine-readiness: duplicate --probe-ibkr."
    return True, ""


def validate_run_automatic_paper_engine_args(
    args: tuple[str, ...],
) -> tuple[bool, str]:
    """Strict allowlist for the long-running automatic paper engine CLI."""
    ok, err = _check_no_forbidden("run-automatic-paper-engine", args)
    if not ok:
        return ok, err
    i = 0
    seen: dict[str, int] = {}
    bools = frozenset(
        {
            "--telegram",
            "--report-on-exit",
            "--no-report-on-exit",
            "--dry-run",
            "--json",
            "--once",
            "--market-hours-only",
            "--ignore-market-hours",
            "--probe-ibkr",
            "--no-probe-ibkr",
            "--no-runtime-on",
        }
    )
    while i < len(args):
        t = args[i]
        if t in bools:
            seen[t] = seen.get(t, 0) + 1
            if seen[t] > 1:
                return False, f"run-automatic-paper-engine: duplicate {t!r}."
            i += 1
            continue
        if t == "--session":
            if i + 1 >= len(args):
                return False, "run-automatic-paper-engine: --session needs a value."
            v = args[i + 1].strip().lower()
            if v not in {"morning", "full"}:
                return False, "run-automatic-paper-engine: --session must be morning|full."
            i += 2
            continue
        if t == "--source":
            if i + 1 >= len(args):
                return False, "run-automatic-paper-engine: --source needs a value."
            v = args[i + 1].strip().lower()
            if v not in {"static", "dynamic", "manual"}:
                return False, "run-automatic-paper-engine: --source must be static|dynamic|manual."
            i += 2
            continue
        if t == "--limit":
            if i + 1 >= len(args):
                return False, "run-automatic-paper-engine: --limit needs a value."
            try:
                n = int(args[i + 1])
            except ValueError:
                return False, "run-automatic-paper-engine: --limit must be int."
            if not 1 <= n <= 500:
                return False, "run-automatic-paper-engine: --limit out of range."
            i += 2
            continue
        if t == "--sleep-seconds":
            if i + 1 >= len(args):
                return False, "run-automatic-paper-engine: --sleep-seconds needs a value."
            try:
                n = int(args[i + 1])
            except ValueError:
                return False, "run-automatic-paper-engine: --sleep-seconds must be int."
            if not 5 <= n <= 3600:
                return False, "run-automatic-paper-engine: --sleep-seconds out of range."
            i += 2
            continue
        if t == "--max-cycles":
            if i + 1 >= len(args):
                return False, "run-automatic-paper-engine: --max-cycles needs a value."
            try:
                n = int(args[i + 1])
            except ValueError:
                return False, "run-automatic-paper-engine: --max-cycles must be int."
            if not 1 <= n <= 50_000:
                return False, "run-automatic-paper-engine: --max-cycles out of range."
            i += 2
            continue
        if t == "--stop-after-minutes":
            if i + 1 >= len(args):
                return False, "run-automatic-paper-engine: --stop-after-minutes needs a value."
            try:
                n = float(args[i + 1])
            except ValueError:
                return False, "run-automatic-paper-engine: --stop-after-minutes must be a number."
            if not 0.1 <= n <= 24 * 60:
                return False, "run-automatic-paper-engine: --stop-after-minutes out of range."
            i += 2
            continue
        return False, f"run-automatic-paper-engine: unexpected token {t!r}."
    if "--report-on-exit" in seen and "--no-report-on-exit" in seen:
        return False, "run-automatic-paper-engine: --report-on-exit conflicts with --no-report-on-exit."
    if "--probe-ibkr" in seen and "--no-probe-ibkr" in seen:
        return False, "run-automatic-paper-engine: --probe-ibkr conflicts with --no-probe-ibkr."
    if "--market-hours-only" in seen and "--ignore-market-hours" in seen:
        return False, "run-automatic-paper-engine: conflicting market-hours flags."
    return True, ""


_FA_READINESS_FLAGS = frozenset({"--json", "--no-json", "--probe-ibkr"})


def validate_full_auto_paper_readiness_args(
    args: tuple[str, ...],
) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("full-auto-paper-readiness", args)
    if not ok:
        return ok, err
    i = 0
    while i < len(args):
        t = args[i]
        if t in _FA_READINESS_FLAGS:
            i += 1
            continue
        if t == "--session":
            if i + 1 >= len(args):
                return False, "full-auto-paper-readiness: --session needs a value."
            v = args[i + 1].strip().lower()
            if v not in {"morning", "full"}:
                return False, "full-auto-paper-readiness: --session must be morning|full."
            i += 2
            continue
        return False, f"full-auto-paper-readiness: unexpected token {t!r}."
    if args.count("--json") > 1 or args.count("--no-json") > 1:
        return False, "full-auto-paper-readiness: duplicate json flag."
    if "--json" in args and "--no-json" in args:
        return False, "full-auto-paper-readiness: --json and --no-json conflict."
    if args.count("--probe-ibkr") > 1:
        return False, "full-auto-paper-readiness: duplicate --probe-ibkr."
    return True, ""


def validate_run_full_auto_paper_supervisor_args(
    args: tuple[str, ...],
) -> tuple[bool, str]:
    """Strict allowlist: full-auto supervisor (long-running; paper only)."""
    ok, err = _check_no_forbidden("run-full-auto-paper-supervisor", args)
    if not ok:
        return ok, err
    i = 0
    seen: dict[str, int] = {}
    bools = frozenset(
        {
            "--telegram",
            "--no-telegram",
            "--report-on-exit",
            "--no-report-on-exit",
            "--once",
            "--dry-run",
            "--json",
            "--market-open-check-only",
            "--no-trade",
            "--news-only",
        }
    )
    while i < len(args):
        t = args[i]
        if t in bools:
            seen[t] = seen.get(t, 0) + 1
            if seen[t] > 1:
                return False, f"run-full-auto-paper-supervisor: duplicate {t!r}."
            i += 1
            continue
        if t == "--session":
            if i + 1 >= len(args):
                return False, "run-full-auto-paper-supervisor: --session needs a value."
            v = args[i + 1].strip().lower()
            if v not in {"morning", "full"}:
                return False, "run-full-auto-paper-supervisor: --session must be morning|full."
            i += 2
            continue
        if t == "--sleep-seconds":
            if i + 1 >= len(args):
                return False, "run-full-auto-paper-supervisor: --sleep-seconds needs a value."
            try:
                n = float(args[i + 1])
            except ValueError:
                return False, "run-full-auto-paper-supervisor: --sleep-seconds must be a number."
            if not 5.0 <= n <= 3600.0:
                return False, "run-full-auto-paper-supervisor: --sleep-seconds out of range."
            i += 2
            continue
        if t == "--max-runtime-minutes":
            if i + 1 >= len(args):
                return False, "run-full-auto-paper-supervisor: --max-runtime-minutes needs a value."
            try:
                n = float(args[i + 1])
            except ValueError:
                return (
                    False,
                    "run-full-auto-paper-supervisor: --max-runtime-minutes must be a number.",
                )
            if not 1.0 <= n <= 24 * 60:
                return False, "run-full-auto-paper-supervisor: --max-runtime-minutes out of range."
            i += 2
            continue
        return False, f"run-full-auto-paper-supervisor: unexpected token {t!r}."
    if "--report-on-exit" in seen and "--no-report-on-exit" in seen:
        return (
            False,
            "run-full-auto-paper-supervisor: --report-on-exit conflicts with --no-report-on-exit.",
        )
    if "--telegram" in seen and "--no-telegram" in seen:
        return False, "run-full-auto-paper-supervisor: --telegram conflicts with --no-telegram."
    return True, ""


def validate_eod_paper_checklist_args(args: tuple[str, ...]) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("eod-paper-checklist", args)
    if not ok:
        return ok, err
    if args:
        return False, "eod-paper-checklist: no arguments allowed."
    return True, ""


_NEWS_MON_READ_FLAGS = frozenset({"--json"})


def validate_news_monitor_readiness_args(args: tuple[str, ...]) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("news-monitor-readiness", args)
    if not ok:
        return ok, err
    for t in args:
        if t not in _NEWS_MON_READ_FLAGS:
            return False, f"news-monitor-readiness: only --json or empty, got {t!r}."
    if args.count("--json") > 1:
        return False, "news-monitor-readiness: duplicate --json."
    return True, ""


def validate_email_config_status_args(args: tuple[str, ...]) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("email-config-status", args)
    if not ok:
        return ok, err
    for t in args:
        if t not in _NEWS_MON_READ_FLAGS:
            return False, f"email-config-status: only --json or empty, got {t!r}."
    if args.count("--json") > 1:
        return False, "email-config-status: duplicate --json."
    return True, ""


_MARKET_NEWS_FLAGS = frozenset(
    {
        "--symbols",
        "--watchlist",
        "--core-basket",
        "--no-core-basket",
        "--market-moving-only",
        "--all-scored",
        "--lookback-minutes",
        "--min-score",
        "--telegram",
        "--no-telegram",
        "--email",
        "--no-email",
        "--dry-run",
        "--no-dry-run",
        "--json",
    }
)


def validate_market_news_check_args(args: tuple[str, ...]) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("market-news-check", args)
    if not ok:
        return ok, err
    if "--telegram" in args and "--no-telegram" in args:
        return False, "market-news-check: --telegram and --no-telegram are exclusive."
    if "--email" in args and "--no-email" in args:
        return False, "market-news-check: --email and --no-email are exclusive."
    if "--dry-run" in args and "--no-dry-run" in args:
        return False, "market-news-check: --dry-run and --no-dry-run are exclusive."
    n_source = 0
    if "--core-basket" in args:
        n_source += 1
    if "--watchlist" in args:
        n_source += 1
    if "--symbols" in args:
        n_source += 1
    if n_source > 1:
        return False, "market-news-check: at most one of --core-basket, --watchlist, --symbols."
    i = 0
    while i < len(args):
        t = args[i]
        if t in _MARKET_NEWS_FLAGS and t not in {
            "--symbols",
            "--watchlist",
            "--lookback-minutes",
            "--min-score",
        }:
            if t in {"--core-basket", "--market-moving-only", "--all-scored", "--telegram", "--email", "--dry-run", "--no-dry-run", "--json", "--no-telegram", "--no-email", "--no-core-basket"}:
                i += 1
                continue
            return False, f"market-news-check: unknown flag {t!r}."
        if t in {"--symbols", "--watchlist", "--lookback-minutes", "--min-score"}:
            if i + 1 >= len(args):
                return False, f"market-news-check: {t} needs a value."
            i += 2
            continue
        if t in {"--core-basket", "--json", "--telegram", "--email", "--dry-run", "--no-dry-run", "--market-moving-only", "--all-scored", "--no-telegram", "--no-email", "--no-core-basket"}:
            i += 1
            continue
        return False, f"market-news-check: unexpected token {t!r}."
    if "--watchlist" in args:
        w_idx = args.index("--watchlist")
        if w_idx + 1 < len(args) and args[w_idx + 1] != "latest":
            return False, "market-news-check: only --watchlist latest is allowed from UI."
    if "--no-dry-run" in args and "--telegram" in args:
        return (
            False,
            "market-news-check: from UI, do not use --no-dry-run with --telegram; use --dry-run only.",
        )
    return True, ""


def validate_paper_activation_status_args(args: tuple[str, ...]) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("paper-activation-status", args)
    if not ok:
        return ok, err
    for t in args:
        if t not in _PAPER_ACTIVATION_STATUS_FLAGS:
            return False, f"paper-activation-status: only --probe-ibkr is allowed, got {t!r}."
    if args.count("--probe-ibkr") > 1:
        return False, "paper-activation-status: duplicate --probe-ibkr."
    return True, ""


def validate_write_paper_local_config_args(args: tuple[str, ...]) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("write-paper-local-config", args)
    if not ok:
        return ok, err
    if not args or args == ("--write",):
        if args.count("--write") > 1:
            return False, "write-paper-local-config: duplicate --write."
        return True, ""
    return False, "write-paper-local-config: use no args (dry-run) or exactly --write."


def validate_intraday_paper_on_off_args(args: tuple[str, ...]) -> tuple[bool, str]:
    if args:
        return False, "intraday-paper-on/off: no arguments allowed."
    return True, ""


_PAPER_READINESS_BOOL = frozenset(
    {"--intraday", "--no-intraday", "--probe-ibkr", "--scan"}
)
_PAPER_READINESS_VALUE = frozenset({"--source", "--limit"})


def validate_paper_readiness_check_args(args: tuple[str, ...]) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("paper-readiness-check", args)
    if not ok:
        return ok, err
    if "--intraday" in args and "--no-intraday" in args:
        return False, "paper-readiness-check: --intraday and --no-intraday are mutually exclusive."
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token in _PAPER_READINESS_BOOL:
            flags[token] = True
            i += 1
            continue
        if token in _PAPER_READINESS_VALUE:
            if i + 1 >= len(args):
                return False, f"paper-readiness-check: {token} requires a value."
            flags[token] = args[i + 1]
            i += 2
            continue
        return False, f"paper-readiness-check: unexpected token {token!r}."
    if "--source" in flags:
        v = str(flags["--source"]).lower()
        if v not in _INTRADAY_SOURCE_VALUES:
            return False, "paper-readiness-check: invalid --source."
    if "--limit" in flags:
        try:
            n = int(str(flags["--limit"]))
        except (TypeError, ValueError):
            return False, "paper-readiness-check: --limit must be int."
        if not (_INTRADAY_PAPER_LIMIT_MIN <= n <= _INTRADAY_PAPER_LIMIT_MAX):
            return False, "paper-readiness-check: --limit out of range."
    return True, ""


def validate_first_paper_pass_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Same shape as one-shot intraday auto-paper (source / limit / telegram)."""
    return validate_auto_paper_intraday_smc_args(args)


_COVERAGE_TICKER_RE = re.compile(r"^[A-Z]{1,5}(,[A-Z]{1,5})*$")
_COVERAGE_WL_OK = frozenset({"latest"})


def validate_candle_coverage_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Validate ``candle-coverage`` — local file check only, no IBKR path."""
    ok, err = _check_no_forbidden("candle-coverage", args)
    if not ok:
        return ok, err
    flags: dict[str, str] = {}
    json_count = 0
    has_core = False
    i = 0
    while i < len(args):
        t = args[i]
        if t == "--json":
            json_count += 1
            if json_count > 1:
                return False, "candle-coverage: duplicate --json"
            i += 1
            continue
        if t == "--core-basket":
            has_core = True
            i += 1
            continue
        if t in ("--start", "--end", "--timeframe", "--symbols", "--watchlist"):
            if i + 1 >= len(args):
                return False, f"candle-coverage: {t} needs a value"
            v = str(args[i + 1]).strip()
            if t in ("--start", "--end") and not _BACKTEST_DATE_RE.match(v):
                return False, f"candle-coverage: {t} must be YYYY-MM-DD"
            if t == "--timeframe" and v.lower() not in ("1min", "1m"):
                return False, "candle-coverage: only --timeframe 1min is allowed"
            if t == "--symbols" and not _COVERAGE_TICKER_RE.match(v):
                return (
                    False,
                    "candle-coverage: --symbols must match ^[A-Z]{1,5}(,[A-Z]{1,5})*$",
                )
            if t == "--watchlist" and v.lower() not in _COVERAGE_WL_OK:
                return False, "candle-coverage: --watchlist only accepts 'latest'."
            flags[t] = v
            i += 2
            continue
        return False, f"candle-coverage: unexpected token {t!r}"
    if not flags.get("--start") or not flags.get("--end"):
        return False, "candle-coverage: --start and --end are required"
    n_spec = int(has_core) + int(bool(flags.get("--watchlist"))) + int(
        bool(flags.get("--symbols"))
    )
    if n_spec != 1:
        return (
            False,
            "candle-coverage: require exactly one of --core-basket, --watchlist, or --symbols",
        )
    if has_core and (flags.get("--watchlist") or flags.get("--symbols")):
        return (
            False,
            "candle-coverage: --core-basket is mutually exclusive with other symbol sources",
        )
    if flags.get("--timeframe") and str(flags["--timeframe"]).lower() not in (
        "1min",
        "1m",
    ):
        return False, "candle-coverage: only 1min"
    return True, ""


_BACKTEST_ONECLICK_STRATEGY_OK = frozenset({"ict_smc_intraday_v1"})


def validate_backtest_oneclick_args(args: tuple[str, ...]) -> tuple[bool, str]:
    """Validate ``backtest-oneclick`` (coverage + optional IBKR fetch + backtest)."""
    ok, err = _check_no_forbidden("backtest-oneclick", args)
    if not ok:
        return ok, err
    has_core = False
    json_n = 0
    has_chart = 0
    has_allow_partial = 0
    flags: dict[str, str] = {}
    i = 0
    while i < len(args):
        t = args[i]
        if t == "--json":
            json_n += 1
            if json_n > 1:
                return False, "backtest-oneclick: duplicate --json"
            i += 1
            continue
        if t == "--core-basket":
            has_core = True
            i += 1
            continue
        if t == "--chart":
            has_chart += 1
            if has_chart > 1:
                return False, "backtest-oneclick: duplicate --chart"
            i += 1
            continue
        if t == "--allow-partial":
            has_allow_partial += 1
            if has_allow_partial > 1:
                return False, "backtest-oneclick: duplicate --allow-partial"
            i += 1
            continue
        if t in ("--rth-only", "--no-rth-only"):
            i += 1
            continue
        if t in (
            "--start",
            "--end",
            "--timeframe",
            "--symbols",
            "--watchlist",
            "--strategy",
            "--mode",
            "--direction",
        ):
            if i + 1 >= len(args):
                return False, f"backtest-oneclick: {t} needs a value"
            v = str(args[i + 1]).strip()
            if t in ("--start", "--end") and not _BACKTEST_DATE_RE.match(v):
                return False, f"backtest-oneclick: {t} must be YYYY-MM-DD"
            if t == "--timeframe" and v.lower() not in ("1min", "1m"):
                return False, "backtest-oneclick: only --timeframe 1min"
            if t == "--symbols" and not _COVERAGE_TICKER_RE.match(v):
                return (
                    False,
                    "backtest-oneclick: --symbols must match ^[A-Z]{1,5}(,[A-Z]{1,5})*$",
                )
            if t == "--watchlist" and v.lower() not in _COVERAGE_WL_OK:
                return False, "backtest-oneclick: --watchlist only accepts 'latest'."
            if t == "--strategy" and v not in _BACKTEST_ONECLICK_STRATEGY_OK:
                return False, "backtest-oneclick: --strategy must be ict_smc_intraday_v1"
            if t == "--mode" and v not in _BACKTEST_MODE_VALUES:
                return (
                    False,
                    f"backtest-oneclick: --mode must be one of {sorted(_BACKTEST_MODE_VALUES)}",
                )
            if t == "--direction" and v not in _BACKTEST_DIRECTION_VALUES:
                return (
                    False,
                    f"backtest-oneclick: --direction must be one of {sorted(_BACKTEST_DIRECTION_VALUES)}",
                )
            flags[t] = v
            i += 2
            continue
        return False, f"backtest-oneclick: unexpected token {t!r}"
    if not flags.get("--start") or not flags.get("--end"):
        return False, "backtest-oneclick: --start and --end are required"
    n_spec = int(has_core) + int(bool(flags.get("--watchlist"))) + int(
        bool(flags.get("--symbols"))
    )
    if n_spec != 1:
        return (
            False,
            "backtest-oneclick: require exactly one of --core-basket, --watchlist, or --symbols",
        )
    if has_core and (flags.get("--watchlist") or flags.get("--symbols")):
        return (
            False,
            "backtest-oneclick: --core-basket is mutually exclusive with other symbol sources",
        )
    if args.count("--rth-only") > 1 or args.count("--no-rth-only") > 1:
        return False, "backtest-oneclick: duplicate RTH flag"
    if "--rth-only" in args and "--no-rth-only" in args:
        return (
            False,
            "backtest-oneclick: --rth-only and --no-rth-only are mutually exclusive",
        )
    if flags.get("--timeframe") and str(flags["--timeframe"]).lower() not in (
        "1min",
        "1m",
    ):
        return False, "backtest-oneclick: only 1min"
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
    if command == "candle-coverage":
        return validate_candle_coverage_args(args)
    if command == "backtest-oneclick":
        return validate_backtest_oneclick_args(args)
    if command == "backtest-intraday-smc":
        return validate_backtest_intraday_smc_args(args)
    if command == "backtest-intraday-smc-watchlist":
        return validate_backtest_intraday_smc_watchlist_args(args)
    if command == "backtest-report":
        return validate_backtest_report_args(args)
    if command == "build-edge-profile":
        return validate_build_edge_profile_args(args)
    if command == "build-edge-profiles":
        return validate_build_edge_profiles_args(args)
    if command == "edge-profile-report":
        return validate_edge_profile_report_args(args)
    if command == "auto-paper-intraday-smc":
        return validate_auto_paper_intraday_smc_args(args)
    if command == "ibkr-session-status":
        return validate_readonly_broker_view_args("ibkr-session-status", args)
    if command == "open-orders":
        return validate_readonly_broker_view_args("open-orders", args)
    if command == "portfolio":
        return validate_readonly_broker_view_args("portfolio", args)
    if command == "paper-daily-report":
        return validate_paper_daily_report_args(args)
    if command == "paper-weekly-report":
        return validate_paper_weekly_report_args(args)
    if command == "intraday-paper-status":
        return validate_intraday_paper_status_args(args)
    if command == "strategy-lab-engine-status":
        return validate_strategy_lab_engine_status_args(args)
    if command == "engine-status":
        return validate_engine_status_args(args)
    if command == "paper-activation-status":
        return validate_paper_activation_status_args(args)
    if command == "auto-loop-readiness":
        return validate_auto_loop_readiness_args(args)
    if command == "automatic-paper-engine-readiness":
        return validate_automatic_paper_engine_readiness_args(args)
    if command == "run-automatic-paper-engine":
        return validate_run_automatic_paper_engine_args(args)
    if command == "full-auto-paper-readiness":
        return validate_full_auto_paper_readiness_args(args)
    if command == "run-full-auto-paper-supervisor":
        return validate_run_full_auto_paper_supervisor_args(args)
    if command == "eod-paper-checklist":
        return validate_eod_paper_checklist_args(args)
    if command == "news-monitor-readiness":
        return validate_news_monitor_readiness_args(args)
    if command == "email-config-status":
        return validate_email_config_status_args(args)
    if command == "market-news-check":
        return validate_market_news_check_args(args)
    if command == "write-paper-local-config":
        return validate_write_paper_local_config_args(args)
    if command in {"intraday-paper-on", "intraday-paper-off"}:
        return validate_intraday_paper_on_off_args(args)
    if command == "paper-readiness-check":
        return validate_paper_readiness_check_args(args)
    if command == "first-paper-pass":
        return validate_first_paper_pass_args(args)
    if command == "data-status":
        return validate_data_status_args(args)
    if command == "data-cleanup":
        return validate_data_cleanup_args(args)
    if command == "premarket-brief":
        return validate_premarket_brief_args(args)
    return True, ""


def validate_data_status_args(args: tuple[str, ...]) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("data-status", args)
    if not ok:
        return ok, err
    if not args:
        return True, ""
    if args == ("--json",):
        return True, ""
    return False, "data-status: only optional --json"


def validate_data_cleanup_args(args: tuple[str, ...]) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("data-cleanup", args)
    if not ok:
        return ok, err
    if args in ((), ("--dry-run",)):
        return True, ""
    if args == ("--apply",):
        return (
            False,
            "data-cleanup: --apply is CLI-only; use --dry-run from the UI.",
        )
    return False, "data-cleanup: use --dry-run from the UI (or CLI --apply)."


def validate_premarket_brief_args(args: tuple[str, ...]) -> tuple[bool, str]:
    ok, err = _check_no_forbidden("premarket-brief", args)
    if not ok:
        return ok, err
    allowed = frozenset({"--latest", "--today", "--email", "--telegram"})
    i = 0
    while i < len(args):
        t = args[i]
        if t in allowed:
            i += 1
            continue
        if t == "--date":
            if i + 1 >= len(args):
                return False, "premarket-brief: --date needs YYYY-MM-DD."
            val = str(args[i + 1])
            if not _DATE_RE.match(val):
                return False, "premarket-brief: --date must be YYYY-MM-DD."
            i += 2
            continue
        return False, f"premarket-brief: unexpected token {t!r}."
    return True, ""
