# Deployment Architecture (Local now → Vercel + Worker later)

This document explains how the project is structured so the **same code** works:

1. **Today (local)** — single laptop, FastAPI UI on `127.0.0.1:8765` reading
   project files and shelling out to the existing `python -m bot.cli` commands.
2. **Future (cloud)** — UI hosted on Vercel, IBKR Worker running 24/7 on a
   normal VM / Mac mini / Linode, and a shared Postgres (Supabase) database
   acting as the only bridge between them.

The **goal of the local UI work (Prompt 13A)** is to get *just the UI shell*
in place using clean abstractions so the move to cloud later is a
backend-swap, not a rewrite.

---

## Hard rules (always)

- **Paper trading only.** `IBKR_ACCOUNT_MODE` must be `paper`. Live mode is
  rejected by `bot/config.py`.
- **The UI never connects to IBKR / TWS at startup.** No socket connect, no
  `place_order`, no contract qualification on import. (Readiness cards that
  include **Full Auto Paper** use a UI-safe path without IBKR API probes; optional
  TCP to localhost for TWS is **not** used on page render—run
  `full-auto-paper-readiness` in a terminal for live checks.)
- **macOS background**: Optional `launchd` job (see `scripts/install_full_auto_paper_launchd.sh`)
  runs a **wrapper** under `Library/Application Support` with `STRATEGY_LAB_REPO_DIR` + `PYTHONPATH`
  (avoids TCC issues with executing paths under `~/Documents`). It does not change
  the UI safety model (no live trading, no UI-driven `launchctl`).
- **The UI never places an order directly.** It can only enqueue an
  *allowlisted CLI command* (e.g. `paper-reconcile`, `scan-mtf-smc-watchlist`).
  The actual broker calls only happen inside the existing `bot/` modules
  triggered by those CLI commands.
- **No secrets in git.** `.env`, `config/settings.local.yaml`,
  `data/runtime/`, `data/auto_paper_loop/`, and `*.sqlite` / `*.jsonl`
  artifacts are gitignored.

---

## Three logical components

```
┌─────────────────────────┐
│   UI  (bot_ui/)         │   FastAPI + Jinja, dark theme
│   "Strategy Lab"        │   - Renders dashboards/watchlist/signals
│                         │   - Buttons enqueue commands
│   Today: 127.0.0.1:8765 │   - NEVER touches IBKR
│   Tomorrow: Vercel      │
└──────────┬──────────────┘
           │   reads StateStore
           │   writes CommandQueue
           ▼
┌─────────────────────────┐
│   Shared State (today:  │   - LocalFileStateStore: reads
│   filesystem; tomorrow: │     data/, config/, logs/ on disk
│   Postgres/Supabase)    │   - DatabaseStateStore: same
│                         │     interface, backed by Postgres
└──────────┬──────────────┘
           ▲
           │   reads/writes shared state
           │
┌──────────┴──────────────┐
│   Worker (existing      │   - bot/ package + CLI
│   bot/* + cli.py)       │   - Connects to IBKR/TWS
│                         │   - Runs auto-paper loop, MTF SMC
│   Today: same machine   │     scans, paper-reconcile, etc.
│   Tomorrow: VM / Mac    │
│   mini / Linode         │
└─────────────────────────┘
```

The UI does **not** import `bot.broker` or `bot.ibkr_client`. The UI only
imports two abstractions:

| Abstraction      | Local backend             | Future cloud backend          |
|------------------|---------------------------|-------------------------------|
| `StateStore`     | `LocalFileStateStore`     | `DatabaseStateStore`          |
| `CommandQueue`   | `LocalCommandRunner`      | `RemoteCommandQueue` (HTTP/DB)|

Switching is controlled by `STRATEGY_LAB_BACKEND` (`local` | `remote`).
Today only `local` is implemented; `remote` raises `NotImplementedError` so
nobody can accidentally point the local UI at production.

---

## Why this design

- **Vercel cannot keep a long-lived TWS connection.** Vercel functions are
  serverless / stateless. The IBKR session needs a process that lives 24/7
  → that's the Worker.
- **The UI must stay fast and stateless.** Reading files / DB rows is fine.
  Reaching out to TWS from the request handler is not.
- **Commands need an audit trail.** Both today (JSONL) and tomorrow
  (Postgres) every UI-issued command is allowlisted, validated, logged with
  timestamp + status, and rejected if it doesn't match the safe list.

---

## Allowlisted commands (single source of truth)

The UI may only run these subcommands of `python -m bot.cli`:

- `paper-reconcile`
- `reconcile-fills` (read-only: TWS executions vs local Strategy Lab paper rows; **`broker_readonly`** roster)
- `refresh-paper-account-state`
- `build-watchlist`
- `scan-mtf-smc-watchlist`
- `mtf-near-alignment-alert`
- `research-report`
- `research-status`
- `macro-calendar`

Anything else is a hard reject in `bot_ui/services/command_queue.py`. There
is **no** `place_order`, `order`, `bracket`, `cancel`, `liquidate`, or
arbitrary shell command path from the UI.

The UI today is **status + control flags only**. Concretely the only ways
the UI affects the running paper bot are:

1. Toggling on-disk runtime files (the canonical paths below).
2. Enqueuing one of the read-only / research CLI commands above.

The UI does **not** enqueue `auto-paper-mtf` itself, and there is no UI
button that submits a paper order. Interactive paper execution controls
(e.g. "queue this MTF setup as a paper bracket from the UI") are deferred
to a later **Prompt 13F** which will add a separate, equally narrow,
allowlist.

---

## Canonical runtime file paths (UI ↔ worker contract)

The UI and the existing auto-paper loop / Telegram commands MUST agree on
where these files live. All paths are relative to the project root and
match exactly what `bot/auto_paper_mtf.py`, `bot/auto_paper_loop.py`, and
`bot/telegram_commands.py` already use.

| Purpose                       | Canonical path                                | Producers                                             | Consumers                                            |
|-------------------------------|------------------------------------------------|-------------------------------------------------------|------------------------------------------------------|
| Kill switch (file presence)   | `data/KILL_SWITCH`                             | UI POST `/paper/runtime/kill-switch`, Telegram `/resume` only (not `/kill`; use UI for on) | `bot.auto_paper_mtf.is_kill_switch_active`, auto-paper loop |
| MTF auto-paper toggle         | `data/runtime/mtf_auto_paper_enabled`          | UI POST `/paper/runtime/mtf-auto`, Telegram `/auto_mtf_on`, `/auto_mtf_off` | `bot.auto_paper_mtf.is_runtime_mtf_auto_enabled` / `..._disabled_explicit` |
| Loop snapshot (last cycle)    | `data/runtime/auto_paper_loop_state.json`      | `bot.auto_paper_loop.run_auto_paper_mtf_loop`         | UI dashboard / paper status cards (read-only)        |
| Loop history (per-day JSONL)  | `data/auto_paper_loop/<YYYY-MM-DD>-loop.jsonl` | `bot.auto_paper_loop.run_auto_paper_mtf_loop`         | UI dashboard fallback                                |

Notes:

- The kill switch is a **file-presence** check (any non-empty content
  works). The file's body is logged for forensic context.
- The MTF auto flag is a **content** check: `1`/`on`/`true`/`yes` => on,
  `0`/`off`/`false`/`no` => explicit off (overrides
  `settings.fully_automatic`). Empty file is treated as on.
- Both `LocalFileStateStore.kill_switch_path` and
  `LocalFileStateStore.mtf_auto_paper_enabled_path` are exposed precisely
  so that UI templates and tests can assert "this is the same file the
  worker is reading."

## Research Intelligence Layer v2 (Prompt 13B)

A separate UI page at `/research` reads research artefacts written by
`python -m bot.cli research-report`. The same hard rules apply: the UI
NEVER imports `bot.ibkr_client` or any provider module on render; it
only reads files. IBKR connection happens exclusively when the operator
clicks a button that runs an allowlisted CLI command, or when the
worker calls the CLI directly.

| Purpose                                    | Canonical path                                                      | Producer                                                                          | Consumer                                                            |
|--------------------------------------------|---------------------------------------------------------------------|-----------------------------------------------------------------------------------|---------------------------------------------------------------------|
| Manual macro calendar (input)              | `config/macro_calendar.yaml`                                        | Operator (committed YAML)                                                         | `bot.research_providers.manual_macro_calendar.load_macro_calendar`  |
| IBKR news cache (per-day)                  | `data/research/cache/ibkr_news/<YYYY-MM-DD>-news.json`              | `python -m bot.cli ibkr-news-fetch`                                               | `bot.cli._build_research_report` (fallback when fresh fetch fails)  |
| Research report (per-day, full payload)    | `data/research/<YYYY-MM-DD>-research-report.json`                   | `python -m bot.cli research-report`                                               | `LocalFileStateStore.get_research_summary`, `/research` UI page     |
| Research instruction (per-day, machine-readable) | `data/research/<YYYY-MM-DD>-research-instructions.json`        | `python -m bot.cli research-report`                                               | `LocalFileStateStore.get_research_summary`, future strategy engine  |
| Latest Chinese Markdown summary            | `memory/RESEARCH-REPORT.md`                                         | `python -m bot.cli research-report`                                               | `/research` UI page (excerpt), human review                         |

UI command runner allowlist additions for v2:

- `research-report` (with optional `--telegram`, `--full`, `--ibkr`/`--no-ibkr`)
- `research-status`
- `macro-calendar` (with optional `--today` or strict `--date YYYY-MM-DD`)
- `ibkr-news-status`
- `ibkr-news-fetch` (`--symbols ^[A-Z]{1,5}(,[A-Z]{1,5})*$` and `--limit 1..200`)
- `build-edge-profile` / `build-edge-profiles` (backtest from **local** 1m cache; optional `--fetch` for explicit IBKR candle fill)
- `edge-profile-report` (read-only latest `data/edge_profiles/*-edge-profiles.json`)
- `auto-loop-readiness` (read-only; optional `--json` / `--probe-ibkr`; does not start the intraday auto loop)
- `automatic-paper-engine-readiness` (read-only file/optional `--probe-ibkr` gates for `run-automatic-paper-engine`)
- `run-automatic-paper-engine` (ICT/SMC intraday automatic paper session; **not** `run-auto-paper-intraday-loop`; strict arg validation; subprocess timeout extended to 8h unless `--dry-run`)
- `eod-paper-checklist` (read-only; prints recommended EOD command sequence; no orders, no email)
- `news-monitor-readiness` (read-only; env + config for market-news monitor; no provider fetch)
- `market-news-check` (Finnhub/FMP REST when keys present; default `--dry-run` avoids Telegram; never trades)
- `journal-generate-trade-chart` / `generate-trade-chart` (writes `data/reports/trade_charts/<trade_id>.png` from **local** 1m cache only via `--trade-id` plus optional `--json`, `--force`, `--window-before-minutes`, `--window-after-minutes`; no IBKR; no orders)
- `forex-auto-paper-readiness` (optional `--json`, `--probe-ibkr`; no orders)
- `run-forex-auto-paper-supervisor` (**from UI** only `--dry-run --json`; non-dry-run via CLI/launchd when gates pass)
- `forex-auto-paper-enable` / `forex-auto-paper-disable` (`--json`; runtime `data/runtime/forex_auto_paper_enabled.json` only)
- `generate-trade-charts` / `journal-generate-trade-charts` (**batch** PNGs for recent paper journal rows via `--latest` or `--date`; optional `--limit`, `--json`; no IBKR; no orders; summary may be written under `data/runtime/trade_chart_batch_last.json`)

`run-auto-paper-intraday-loop` remains **forbidden** in the UI (use
`run-automatic-paper-engine` from the allowlist instead). All other
CLI subcommands (especially `auto-paper-mtf`, `place-order`,
`run-auto-paper-mtf-loop`, `telegram-listen`, `run-scheduler`) remain
forbidden via `bot_ui.services.safety.FORBIDDEN_COMMAND_TOKENS`.

This contract is enforced by tests in `tests/test_ui_state_store.py`
(`test_runtime_flags_paths_match_worker_module`,
`test_paper_route_writes_canonical_kill_switch`,
`test_paper_route_writes_canonical_mtf_auto_flag`,
`test_runtime_flags_ignores_legacy_runtime_kill_switch`) and in
`tests/test_ui_routes.py` (`test_kill_switch_toggle_creates_and_removes_file`).

---

## What ships in Prompt 13A (local skeleton)

- `bot_ui/` FastAPI app on `127.0.0.1:8765`.
- Pages: Dashboard, Watchlist, Signals, Paper Trading, Logs, Settings.
- `state_store.LocalFileStateStore` reads existing JSON / JSONL / SQLite.
- `command_queue.LocalCommandRunner` executes allowlisted CLI commands as
  subprocesses with hard timeouts.
- Placeholders for `DatabaseStateStore` and `RemoteCommandQueue` so the
  imports / type contract already exist — but they raise
  `NotImplementedError`.
- Tests for: state store fallbacks, command runner allowlist, route
  smoke tests, and a "no IBKR import on UI startup" architecture safety
  test.

What is **NOT** in 13A: live trading, auto bracket placement from the UI,
chart rendering inside the UI (Prompt 13B), Vercel deploy, Postgres,
auth.

See `docs/vercel-worker-architecture.md` for how the future cloud split
will look component-by-component.
