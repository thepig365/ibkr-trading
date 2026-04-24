# IBKR setup (paper trading)

This bot only talks to a **paper** account. The safety layer refuses
to connect to a live account; see [`safety-rules.md`](safety-rules.md).

## 1. Install TWS or IB Gateway

Either of the following will work; pick one and keep it running while
the bot is active.

| App        | Paper port | Live port |
|------------|-----------:|----------:|
| TWS        |       7497 |      7496 |
| IB Gateway |       4002 |      4001 |

Install from <https://www.interactivebrokers.com/en/index.php?f=14099>.

## 2. Log in to your **paper** account

In TWS or IB Gateway, switch the login dropdown to "Paper Trading"
before signing in. Confirm that the title bar reads `Paper`.

## 3. Enable the API

In TWS / IB Gateway: **File → Global Configuration → API → Settings**.

- Tick **Enable ActiveX and Socket Clients**.
- Tick **Read-Only API** (the bot is read-only in this milestone, so
  this is the safest setting).
- **Untick** "Allow connections from localhost only" only if you need
  remote access; for local development, leave it ticked.
- Set **Socket port** to the paper port from the table above (default
  `7497`).
- Add `127.0.0.1` to **Trusted IPs**.

Restart TWS / IB Gateway after changing these settings.

## 4. Configure the bot

Copy the example env file:

```bash
cp .env.example .env
```

Edit `.env` and set:

```
IBKR_HOST=127.0.0.1
IBKR_PORT=7497          # or 4002 if you use IB Gateway paper
IBKR_CLIENT_ID=1        # any positive int that is not used by another app
IBKR_ACCOUNT_MODE=paper
```

Leave `TELEGRAM_*` empty if you do not want notifications - the bot
will silently fall back to `memory/DAILY-SUMMARY.md`.

## 5. Smoke test

```bash
python -m bot.cli portfolio
python -m bot.cli open-orders
python -m bot.cli reconcile
python -m bot.cli test-telegram
```

If any of these print `Live trading blocked`, you have either pointed
`IBKR_PORT` at a live port or set `IBKR_ACCOUNT_MODE=live`. Fix the
env file - do **not** disable `block_live_trading` in
`config/settings.yaml`.

## 6. Common issues

- **`API connection failed: TimeoutError`** - TWS is not running, or
  the API is disabled, or the port number is wrong.
- **`Couldn't connect to TWS`** - check the Trusted IPs list includes
  `127.0.0.1` and that "Read-Only API" is ticked.
- **`Client ID is already in use`** - increment `IBKR_CLIENT_ID` in
  `.env`.
