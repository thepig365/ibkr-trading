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
  `place_order`, no contract qualification on import.
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
