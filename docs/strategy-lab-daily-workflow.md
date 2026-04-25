# Strategy Lab — local daily workflow

This document describes a **read-only, paper-only** local workflow for the
Strategy Lab UI and engine. It does not enable live trading, does not
place orders, and does not require writing to `.env` (aside from your
normal IBKR work when you *choose* to run research CLI that connects to
TWS; the UI still never opens TWS on its own).

**Chinese operator docs:** `docs/strategy-lab-user-manual.md`, `docs/daily-operation-checklist.md`, `docs/troubleshooting.md`.

## One command: open the UI (foreground)

From the `ibkr-trading-bot` repo root, with the virtualenv active:

```bash
python3 -m bot_ui
```

Defaults: `http://127.0.0.1:8765/` (redirects to `/dashboard`). The UI
**never** connects to IBKR/TWS on startup.

## One command: engine + lab snapshot (read-only, no TWS)

Full snapshot (config + latest `data/*` paths + `ui_process`):

```bash
python3 -m bot.cli engine-status --json
```

Optional: probe `GET /healthz` when the UI process is up (no IBKR):

```bash
python3 -m bot.cli engine-status --json --probe-ui
```

Legacy, smaller JSON (no `artifacts` / `ui_process`):

```bash
python3 -m bot.cli strategy-lab-engine-status --json
```

## Start / stop / status (background)

Canonical scripts (Prompt 13G):

| Action | Command |
|--------|---------|
| Start | `./scripts/start_strategy_lab_ui.sh` |
| Stop | `./scripts/stop_strategy_lab_ui.sh` |
| Status + log tail | `./scripts/status_strategy_lab_ui.sh` |
| Open browser (macOS) | `./scripts/open_strategy_lab_ui.sh` |
| Environment doctor | `./scripts/strategy_lab_doctor.sh` |

**Compat wrapper** (delegates to the above): `./scripts/strategy-lab.sh {start|stop|status|open|restart|doctor}`

- PID: `data/runtime/strategy_lab_ui.pid`  
- Logs: `logs/strategy-lab-ui.stdout.log`, `logs/strategy-lab-ui.stderr.log` (gitignored)  

Environment (optional): `STRATEGY_LAB_HOST`, `STRATEGY_LAB_PORT`, `PYTHON`.

**Makefile:** `make strategy-lab-bg|strategy-lab-stop|strategy-lab-status|strategy-lab-open|strategy-lab-doctor` — see `Makefile`.

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
# or: python3 -m pytest -q tests/test_engine_launch_workflow.py
```

## Worker (CLI) vs UI

* **UI render**: HTTP only, FastAPI, no TWS. Buttons enqueue **allowlisted** commands
  (see `bot_ui/services/safety.py`). The read-only commands
  `engine-status` and `strategy-lab-engine-status` are allowlisted for status checks.
* **Worker / `python3 -m bot.cli …`**: runs scanners, backtests, and paper
  execution *when you invoke it*—never from a bare Jinja template import.

## See also

* [deployment-architecture.md](deployment-architecture.md) — UI / worker split
* [safety-rules.md](safety-rules.md) — paper-only invariants
* [ibkr-setup.md](ibkr-setup.md) — TWS/Gateway (only for CLI that connects)
* [strategy-lab-user-manual.md](strategy-lab-user-manual.md) — 中文用户手册
* [daily-operation-checklist.md](daily-operation-checklist.md) — 中文每日清单
* [troubleshooting.md](troubleshooting.md) — 中文故障排除

## Files that stay local (do not commit)

* `data/runtime/*` (PID, flags, loop state)
* `logs/strategy-lab-ui.*`
* `config/settings.local.yaml` (optional overlay, gitignored)
* `.env` (secrets, gitignored)
