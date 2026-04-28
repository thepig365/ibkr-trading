# ibkr-trading-bot

Safe foundation for a Python-based **paper-only** Interactive Brokers
trading bot. This milestone implements the system foundation only.

> No live trading. No order placement. No trading strategy.
> No SMC, breakout, momentum, RSI, or ORB logic.

See [`docs/safety-rules.md`](docs/safety-rules.md) for the invariants
that the codebase enforces.

## Features in this milestone

- Read-only IBKR client over `ib_async` (with `ib_insync` fallback).
- Configuration loaded from YAML + `.env`, validated by `pydantic`.
- SQLite journal + append-only JSONL audit logs in `data/`.
- Reconciliation that compares broker state with the local journal
  and never places orders.
- Risk engine and broker facade that hard-block live trading,
  options, crypto, forex, shorting, and order submission.
- Telegram notifications with safe fallback to
  `memory/DAILY-SUMMARY.md` when credentials are missing.
- Typer CLI with `portfolio`, `open-orders`, `reconcile`, and
  `test-telegram` commands.
- APScheduler skeleton wired to safe, read-only jobs only.
- Test suite covering the safety invariants.

## Quickstart

Requires **Python 3.11+**.

```bash
cd ibkr-trading-bot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env       # then edit the IBKR_* / TELEGRAM_* values
```

Set up TWS or IB Gateway for paper trading per
[`docs/ibkr-setup.md`](docs/ibkr-setup.md), then:

```bash
python -m bot.cli portfolio
python -m bot.cli open-orders
python -m bot.cli reconcile
python -m bot.cli test-telegram
```

Run the tests:

```bash
pytest
```

## Strategy Lab (local UI)

The FastAPI UI is **read-only on startup** (no TWS connection). For a
daily workflow, helper scripts, and smoke tests, see
[`docs/strategy-lab-daily-workflow.md`](docs/strategy-lab-daily-workflow.md).

**macOS:** double-click **`Strategy Lab.command`** in the repo root (see [`docs/mac-launchers.md`](docs/mac-launchers.md)) — starts the UI if needed (via `healthz`), then opens **`/dashboard`**. Use **Stop Strategy Lab.command** to stop; **Strategy Lab Doctor.command** for diagnostics. **Start Strategy Lab.command** runs the start script then opens the dashboard in the browser. **Open Strategy Lab.command** / **Open Strategy Lab Dashboard.command** only fire `open` on the dashboard URL (they do not start the server). All `.command` files resolve the repo via `dirname "$0"` (safe after moving the clone).

| Command | Purpose |
|--------|---------|
| `Strategy Lab.command` (macOS) | One-click: if UI up, open dashboard; else `start_strategy_lab_ui.sh` + wait for `healthz` + `open_strategy_lab_ui.sh` (paper-only, no TWS on start) |
| `Start Strategy Lab.command` (macOS, optional) | `"$ROOT/scripts/start_strategy_lab_ui.sh"` + wait for `healthz` + `open` → `/dashboard` |
| `Open Strategy Lab Dashboard.command` (macOS, optional) | `open` → `/dashboard` only (no `start`) |
| `python3 -m bot_ui` | Open the UI (default `http://127.0.0.1:8765/`) |
| `python3 -m bot.cli engine-status --json` | Full read-only lab snapshot (config + `data/*` + `ui_process`) |
| `./scripts/start_strategy_lab_ui.sh` | Background UI + PID + logs (see `docs/strategy-lab-daily-workflow.md`) |
| `make strategy-lab-smoke` | Pytest: `tests/test_engine_launch_workflow.py` |
| `docs/strategy-lab-user-manual.md` | 中文用户手册 / operator manual (ZH) |

## Project layout

```
ibkr-trading-bot/
├── *.command               # macOS: Strategy Lab (main), Start/Stop/Open/Dashboard-only, Doctor
├── bot/                    # all runtime code
│   ├── cli.py              # Typer entry point
│   ├── config.py           # YAML + env loader
│   ├── ibkr_client.py      # read-only IBKR wrapper
│   ├── broker.py           # safety facade (no order placement)
│   ├── reconciliation.py   # broker <-> journal cross-check
│   ├── risk_engine.py      # gating decisions
│   ├── journal.py          # SQLite + JSONL persistence
│   ├── scheduler.py        # APScheduler with safe jobs
│   └── notifications/      # Telegram adapter (with fallback)
├── bot_ui/                 # local Strategy Lab (FastAPI; no TWS on startup)
├── scripts/                # e.g. strategy-lab.sh (start/stop/status)
├── config/                 # settings.yaml / strategy.yaml / watchlist.yaml
├── data/                   # SQLite + JSONL audit logs (gitignored)
├── docs/                   # ibkr-setup / safety-rules / runbook
├── memory/                 # rolling, human-readable context files
├── tests/                  # safety + reconciliation + telegram tests
└── pyproject.toml          # build metadata
```

> Note: the spec listed both `bot/notifications.py` and the
> `bot/notifications/` package. Python only allows one of those names
> in a package, so we keep the `bot/notifications/` package
> (containing `telegram.py`) and import via
> `from bot.notifications import send_telegram_message`.

## Safety summary

The following defaults make the bot inert:

- `account.block_live_trading: true`
- `trading.enabled: false`
- `trading.dry_run_default: true`
- `trading.require_manual_confirmation: true`
- `trading.allow_options / allow_crypto / allow_forex / allow_shorting: false`

Even if every default were flipped, `Broker._submit_order` raises - the
order-submission code path is intentionally not implemented in this
milestone.

## Future milestones (not in this PR)

1. Implement the strategy module (`bot/strategy/`).
2. Replace `_submit_order` with a real submission path that always
   passes through `RiskEngine` and `Broker.place_order`.
3. Wire the scheduler to evaluate signals at safe intervals.
4. Add Perplexity-driven research notes to `memory/RESEARCH-LOG.md`.

Each of those PRs must update `docs/safety-rules.md` and add tests.
