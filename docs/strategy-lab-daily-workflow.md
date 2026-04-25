# Strategy Lab — local daily workflow

This document describes a **read-only, paper-only** local workflow for the
Strategy Lab UI and engine. It does not enable live trading, does not
place orders, and does not require writing to `.env` (aside from your
normal IBKR work when you *choose* to run research CLI that connects to
TWS; the UI still never opens TWS on its own).

## One command: open the UI (foreground)

From the `ibkr-trading-bot` repo root, with the virtualenv active:

```bash
python3 -m bot_ui
```

Defaults: `http://127.0.0.1:8765/` (redirects to `/dashboard`). The UI
**never** connects to IBKR/TWS on startup.

## One command: engine + UI process status

**Engine + config (read-only, no TWS, no browser):**

```bash
python3 -m bot.cli strategy-lab-engine-status --json
```

**Background UI** (if you started the UI with the helper script) **and** engine
snapshot:

```bash
./scripts/strategy-lab.sh status
```

or:

```bash
make strategy-lab-status
```

## Start / stop (background, optional)

- **Start** (detached, log under `data/runtime/`):

  ```bash
  ./scripts/strategy-lab.sh start
  ```

- **Stop**:

  ```bash
  ./scripts/strategy-lab.sh stop
  ```

Environment (optional): `STRATEGY_LAB_HOST`, `STRATEGY_LAB_PORT`, `PYTHON`.

## Pages to use each day (smoke / workflow)

| Page | Path | Role |
|------|------|------|
| Dashboard | `/dashboard` | Overview |
| Research | `/research` | Research report / queue |
| Watchlist | `/watchlist` | Watchlist + builder context |
| Signals (MTF) | `/signals` | MTF / SMC signals (read) |
| Backtest | `/backtest` | ICT/SMC intraday backtest (research) |
| Paper Trading | `/paper` | Paper controls (safe command runner) |
| Journal | `/journal` | Paper orders + backtest trade log (read) |

**Health check** (if the server is up):

```text
http://127.0.0.1:8765/healthz
```

## Automated smoke test (no browser, no TWS)

```bash
make strategy-lab-smoke
# or: python3 -m pytest -q tests/test_strategy_lab_smoke.py
```

## Makefile shortcuts

| Target | What it does |
|--------|----------------|
| `make strategy-lab` | Foreground UI |
| `make strategy-lab-bg` | Background start via `scripts/strategy-lab.sh` |
| `make strategy-lab-stop` | Stop background UI |
| `make strategy-lab-status` | Script status (UI pid + engine JSON) |
| `make strategy-lab-smoke` | Pytest smoke |

## Worker (CLI) vs UI

- **UI render**: HTTP only, FastAPI, no TWS. Buttons enqueue **allowlisted** commands
  (see `bot_ui/services/safety.py`). The read-only command
  `strategy-lab-engine-status` is allowlisted for “engine check” from the
  command runner with optional `--json` only.
- **Worker / `python3 -m bot.cli …`**: runs scanners, backtests, and paper
  execution *when you invoke it*—never from a bare Jinja template import.

## Files that stay local (do not commit)

- `data/runtime/*` (PID, logs, auto-paper / intraday flags, loop state)
- `config/settings.local.yaml` (optional overlay, gitignored)
- `.env` (secrets, gitignored)

## See also

- [deployment-architecture.md](deployment-architecture.md) — UI / worker split
- [safety-rules.md](safety-rules.md) — paper-only invariants
- [ibkr-setup.md](ibkr-setup.md) — TWS/Gateway (only for CLI that connects)
