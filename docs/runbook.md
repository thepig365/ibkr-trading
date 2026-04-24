# Runbook

Step-by-step operating instructions for the foundation milestone.

## Daily startup

1. Launch TWS / IB Gateway and log in to the **paper** account.
2. From the project root:
   ```bash
   source .venv/bin/activate
   python -m bot.cli portfolio
   ```
   Confirm the account number begins with `DU` (paper accounts).
3. Run a reconciliation:
   ```bash
   python -m bot.cli reconcile
   ```
   Expected: `PASS`. Any `FAIL` is automatically surfaced via Telegram
   (or `memory/DAILY-SUMMARY.md` if Telegram is not configured). See
   the troubleshooting section below before doing anything else.

## CLI reference

All commands are read-only. **None** of them place orders.

| Command | Purpose | Touches IBKR? |
|---|---|---|
| `python -m bot.cli portfolio` | Account summary + positions | yes (read) |
| `python -m bot.cli open-orders` | List open orders at the broker | yes (read) |
| `python -m bot.cli reconcile` | Cross-check broker vs. journal | yes (read) |
| `python -m bot.cli test-telegram` | Send a test notification | **no** |

### Global flags

| Flag | Effect |
|---|---|
| `--verbose`, `-v` | Show third-party debug logs (`ib_async`, `httpx`, `apscheduler`). Also turns on routine IBKR status messages (farm connections, API ready). |

Examples:

```bash
python -m bot.cli portfolio --verbose
python -m bot.cli reconcile --verbose
python -m bot.cli -v open-orders
```

> The `--verbose` flag must appear **before** the subcommand name.

### `portfolio`

Prints `NetLiquidation`, `TotalCash`, `BuyingPower`, `AvailableFunds`
per account, followed by current positions. Also snapshots the values
into SQLite (`data/trading_bot.sqlite`). No orders, no modifications.

### `open-orders`

Lists each open order at the broker with its `permId`, `orderId`,
side, type, quantity, limit/aux prices, TIF, and status. The orders
are also written to `orders.jsonl` for audit.

### `reconcile`

Reads the current broker state and compares it against the local
journal. Produces three buckets:

- `positions_without_stops` - positions at the broker that lack a
  STP/STP LMT/TRAIL/MIT order on the opposite side.
- `unknown_open_orders` - broker orders whose `permId` is not in the
  journal.
- `missing_local_records` - symbols the journal expected to be open
  but that the broker no longer reports.

On **FAIL**, the CLI automatically sends a warning via
`notify_event("reconciliation.failed", ..., severity="warning")`; if
Telegram is not configured it falls back to
`memory/DAILY-SUMMARY.md`. The exit code is `3` on failure.

Add `--notify` to also notify on PASS (off by default to reduce
noise).

### `test-telegram`

Sends a small test message via `notify_event`. **Does not connect to
IBKR** and never trades. Useful for verifying your
`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` setup before a live session.

Exit codes:

- `0` - Telegram acknowledged delivery.
- `4` - Fallback triggered (missing creds, disabled in settings, or
  API rejection). Payload is saved to `memory/DAILY-SUMMARY.md`.

## What IBKR messages are normal

TWS / IB Gateway emits several advisory messages that are **not
errors**. The CLI hides them by default; re-enable with `--verbose`.

| Code | Meaning |
|---|---|
| `2104` | Market data farm connection is OK |
| `2105` | HMDS data farm connection is broken (advisory; usually recovers) |
| `2106` | HMDS data farm connection is OK |
| `2107` | HMDS data farm connection is inactive (advisory) |
| `2158` | Sec-def data farm connection is OK |
| `1100` / `1101` / `1102` | Connection lost/restored advisories |

If you see one of these in red, check the runbook only if it persists
for more than a few seconds.

## Troubleshooting reconciliation FAIL

### `positions_without_stops`

A position exists at the broker without a corresponding
STP / STP LMT / TRAIL / MIT order on the opposite side. In the
foundation milestone the bot does not auto-create stops. Manually
attach a stop in TWS, then re-run `reconcile`. **Do not disable the
check** - the safety layer depends on it to block new trades.

### `unknown_open_orders`

The broker shows an open order whose `permId` is not in the local
journal. This usually means the order was created manually in TWS.
Either cancel it or acknowledge it by running `open-orders`, which
imports the order into the journal.

### `missing_local_records`

The journal expected a position the broker no longer reports. Most
often this means a manual close. Re-run `portfolio` so a fresh
positions snapshot replaces the stale one.

## Shutdown

All CLI commands are short-lived; nothing stays running. If you later
add `bot.scheduler` as a long-running service, stop it with `Ctrl+C` -
jobs are configured with `coalesce=True` so restarts are safe.

## Recovery

All persistent state lives under `data/`:

- `trading_bot.sqlite` - structured store
- `*.jsonl` - append-only audit logs

Both are recreated on demand. To wipe state during development, delete
the contents of `data/`. Never do this in production-like setups
without first archiving the files.
