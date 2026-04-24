# Project context

This file is a long-lived memory document for the bot. It is **not** a
log; it captures durable facts that future sessions should know.

## Mission

Build a **paper-only** Interactive Brokers trading bot. Live trading is
not in scope. Order placement is not in scope for the foundation
milestone.

## Current milestone

System foundation only:

- read-only IBKR connection
- SQLite + JSONL audit logs
- reconciliation (read-only)
- Telegram notifications with safe fallback
- safety toggles in `config/settings.yaml`
- CLI for inspection
- tests covering the safety invariants

## Hard rules

See `docs/safety-rules.md`. The bot must never:

- connect to a live account while `account.block_live_trading` is true
- place an order while `trading.enabled` is false
- trade options, crypto, forex, or short positions in this milestone
- leave a position uncovered by a stop without flagging it via
  reconciliation
