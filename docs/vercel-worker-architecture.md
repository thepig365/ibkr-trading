# Vercel UI + Worker Architecture (future)

> Status: **design only.** Local development today runs everything on one
> machine. This document defines the contract that the local UI is being
> built against, so the cloud move is a backend swap, not a rewrite.

## High-level split

```
            ┌─────────────────────────────────────────┐
            │              Browser (you)              │
            └────────────────────┬────────────────────┘
                                 │  HTTPS
                                 ▼
            ┌─────────────────────────────────────────┐
            │           Vercel  (UI tier)             │
            │ - Next.js / FastAPI-on-Vercel route(s)  │
            │ - Reads state from Supabase Postgres    │
            │ - Writes commands to `commands` table   │
            │ - NEVER touches IBKR                    │
            └────────────────────┬────────────────────┘
                                 │  Postgres (Supabase)
                                 ▼
       ┌──────────────────────────────────────────────────┐
       │ Supabase Postgres (single source of truth)        │
       │ tables: commands, command_results, account_state, │
       │         positions_snapshot, signals, loop_state   │
       └────────────────────┬─────────────────────────────┘
                            │  Postgres
                            ▼
       ┌──────────────────────────────────────────────────┐
       │ Worker  (this repo, running on a VM / Mac mini)   │
       │ - Polls `commands` table (or LISTEN/NOTIFY)       │
       │ - Executes allowlisted CLI commands               │
       │ - Talks to TWS / IB Gateway (paper)               │
       │ - Writes results + state back to Postgres         │
       │ - Sends Telegram notifications                    │
       └──────────────────────────────────────────────────┘
```

## Why it MUST be split

1. **Vercel is serverless** — there is no long-running process to hold the
   IBKR socket. Every request is a cold function. TWS won't tolerate that.
2. **TWS / IB Gateway needs a desktop session** to log in. That belongs on
   a worker, not Vercel.
3. **Auth and audit live in the DB** — the worker only trusts commands
   that came from a signed UI session and were committed to Postgres.

## Component responsibilities

### Vercel UI tier

- Renders the same pages as the local UI (Dashboard, Watchlist, Signals,
  Paper Trading, Logs, Settings).
- Reads via `DatabaseStateStore` (today: `LocalFileStateStore`).
- Writes commands via `RemoteCommandQueue` (today: `LocalCommandRunner`).
- Auth: Supabase auth or NextAuth — out of scope for 13A.
- **Cannot import** `bot.broker`, `bot.ibkr_client`, or any TWS-touching
  module. Enforced by an architectural test
  (`tests/test_ui_architecture_safety.py`).

### Worker tier (this repo's `bot/` package)

- Runs `python -m bot.cli run-auto-paper-mtf-loop` (already exists).
- Adds (in a later prompt, NOT 13A) a `worker-poll-commands` loop that:
  1. Selects oldest pending row from `commands` table.
  2. Verifies command is in the allowlist + signature is valid.
  3. Executes the matching CLI subcommand.
  4. Writes stdout / stderr / exit_code to `command_results`.
  5. Marks command done.
- Periodically writes account snapshot, positions, watchlist, signals,
  loop heartbeat into Postgres so the UI can read them.

### Database (Supabase / Postgres) — proposed schema (placeholder)

```sql
-- enqueued by UI, dequeued by worker
create table commands (
  id            uuid primary key default gen_random_uuid(),
  created_at    timestamptz not null default now(),
  command       text not null,        -- e.g. 'paper-reconcile'
  args          jsonb not null default '[]'::jsonb,
  requested_by  text not null,        -- user / session id
  status        text not null default 'pending', -- pending|running|done|rejected
  signature     text                   -- HMAC over command+args+nonce
);

create table command_results (
  command_id    uuid references commands(id) primary key,
  finished_at   timestamptz not null default now(),
  exit_code     int,
  stdout        text,
  stderr        text
);

create table account_state (
  ts            timestamptz primary key,
  account_id    text,
  net_liq       numeric,
  cash          numeric,
  buying_power  numeric,
  raw           jsonb
);

create table positions_snapshot (
  ts            timestamptz,
  account_id    text,
  symbol        text,
  position      numeric,
  avg_cost      numeric,
  primary key (ts, account_id, symbol)
);

create table signals (
  ts            timestamptz,
  symbol        text,
  alignment     text,
  ftc_eligible  boolean,
  payload       jsonb,
  primary key (ts, symbol)
);

create table loop_state (
  ts            timestamptz primary key,
  payload       jsonb
);
```

## Mapping local files → DB tables

| Local file (today)                               | Future DB table          |
|--------------------------------------------------|--------------------------|
| `data/account_snapshots.jsonl`                   | `account_state`          |
| `data/trading_bot.sqlite` (`positions` table)    | `positions_snapshot`     |
| `data/mtf_smc/*-mtf-smc.json`                    | `signals`                |
| `data/auto_paper_loop/*.jsonl`                   | `loop_state`             |
| `data/KILL_SWITCH` (canonical, file-presence)    | `loop_state.payload`     |
| `data/runtime/mtf_auto_paper_enabled`            | `loop_state.payload`     |
| `data/watchlists/latest-tws-watchlist.csv`       | (read directly as table) |

The `StateStore` interface is the same for both backends. The UI never
sees the difference.

## What 13A delivers toward this future

- The two abstractions (`StateStore`, `CommandQueue`) exist with both a
  *local* concrete implementation (works today) and a *remote*
  placeholder that raises `NotImplementedError` (so we can't accidentally
  ship half-done remote code).
- `STRATEGY_LAB_BACKEND=local|remote` env switch is wired up.
- The UI imports do NOT pull in `bot.broker` / `bot.ibkr_client` — proven
  by an architectural test.
- Allowlist of commands is the same shape we'll send through the future
  `commands` table.

## What 13A does NOT do

- No Postgres migration, no Supabase project, no Vercel deploy.
- No worker-side command poller.
- No auth / signing / HMAC.
- No live trading. Ever. This system stays paper-only.
