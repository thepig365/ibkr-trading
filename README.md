# IBKR Trading Engine

Automated, paper-first ICT 1-minute day-trading engine for IBKR US equities.
Strategy is pluggable behind a single `BaseStrategy` interface; ICT is the
default, Chanlun and others can plug in by adding a class to
`backend/strategy/registry.py`.

> **Status**: Paper validation. Do **not** point this at a live IBKR account
> until you have hit the validation bar in
> [Paper validation criteria](#paper-validation-criteria).

## What it does

* Subscribes to 1-minute IBKR bars (or aggregates 5-second realtime bars into
  proper 1-minute candles — see `backend/data/bar_aggregator.py`).
* Runs the ICT strategy: Daily Bias, 3-bar FVG detection, kill-zone time
  filter, scoring, retest entries, R:R / stop-width gates, breakeven move at
  +1R, trailing stop at +0.5R distance, and up to 2 scale-ins.
* Submits IBKR Bracket Orders via `ib_insync`, with a $100k daily notional
  cap, 2% daily-loss circuit breaker, and `2 trades / day` cap.
* 30-minute auto-disconnect of TWS with a Reconnect button on the dashboard.
* Forces flat at 3:45 PM ET each day.
* Pushes alerts to Telegram and exposes a 10-command Telegram bot.
* Stores trades / signals / candles / scale-ins in SQLite.
* Next.js 14 dashboard: live status, positions, equity curve, journal with
  TradingView Lightweight Charts annotation, analytics, and settings.

## Prerequisites

* Python 3.11
* Node.js 18+
* IBKR Paper account
* TWS or IB Gateway running and logged into the **paper** account
* (Optional) Finnhub API key for earnings + news blackouts
* (Optional) Telegram bot token + chat id for notifications

## Repository layout

```
backend/
  api/        REST + WebSocket
  connection/ ConnectionManager (30-minute auto-disconnect)
  data/       IBKR data feed + 5s→1m BarAggregator
  db/         Async SQLite + dataclass models
  engine/     TradingEngine dispatcher
  execution/  RiskManager + TradeManager (bracket orders)
  notifications/ FinnhubFeed + TelegramBot
  strategy/   BaseStrategy, Registry, ICTStrategy
frontend/
  app/        Next.js 14 App Router pages (dashboard, journal, analytics, settings)
  components/ ConnectionStatus, ChartModal, TradeRow, ...
  lib/        API + WebSocket client, formatters
tests/        pytest suite
config.example.yaml
.env.example
requirements.txt
pytest.ini
```

## Install

### Backend

```bash
cd ibkr-trading-engine
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
cp config.example.yaml config.yaml
```

Edit `.env` with real values (Finnhub, Telegram, IBKR account ID).
`config.yaml` references those values via `${VAR}` interpolation.

### Frontend

```bash
cd frontend
npm install
npm run build
```

## Configure TWS / IB Gateway

For TWS Paper (recommended):

* Socket port: **7497**
* Enable ActiveX and Socket Clients: **ON**
* Read-Only API: **OFF**
* Trusted IPs: **127.0.0.1**

For IB Gateway Paper, the default port is `4002`. Either run Gateway on `7497`
or change `ibkr.port` in `config.yaml`.

## First run

In one terminal:

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

In another terminal:

```bash
cd frontend
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

Quick health probes:

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/connection-status
curl -X POST http://localhost:8000/api/reconnect
```

WebSocket (engine status pushed every 1 second):

```
ws://localhost:8000/ws/engine-status
```

## Switching strategies

Open `config.yaml`:

```yaml
strategy: ICT      # change to Chanlun once that class is registered
```

To register a new strategy:

1. Subclass `backend/strategy/base.py::BaseStrategy`.
2. Add it to `backend/strategy/registry.py::StrategyRegistry._strategies`.
3. Set `strategy: <Name>` in `config.yaml` and restart.

The Settings page also lets you switch the live strategy in-memory; persist by
editing `config.yaml`.

## Telegram commands

| Command          | Action                                       |
| ---------------- | -------------------------------------------- |
| `/status`        | Engine state + connection                    |
| `/positions`     | Live open positions                          |
| `/pnl`           | Today's P&L + capital used                   |
| `/pause`         | Halt new entries                             |
| `/resume`        | Re-enable new entries                        |
| `/close SYMBOL`  | Manual market close                          |
| `/news SYMBOL`   | Last 5 Finnhub headlines                     |
| `/bias`          | Today's Daily Bias per symbol                |
| `/reconnect`     | Reconnect TWS                                |
| `/report`        | One-shot daily report                        |

## Tests

```bash
source .venv/bin/activate
python -m pytest -q
```

The suite covers config loading, SQLite migrations, the 5s→1m aggregator, the
ICT time filter, FVG detection + retest entry, and risk sizing + the daily
circuit breaker.

## Paper validation criteria

Do **not** flip to a live account until **all** of the following hold over at
least 20 trading days, with at least 30 signals and 15 executed trades:

| Metric            | Pass bar |
| ----------------- | -------- |
| Win rate          | >= 45%   |
| Average R         | >= 1.5R  |
| Profit factor     | >= 1.3   |
| Max single-day loss | < 2%   |
| Max drawdown      | < 8%     |

Also verify:

* Score >= 60 signals win more often than score < 60 signals.
* Trades with at least one scale-in have higher avg R than trades without.

When all pass, change `ibkr.port` to `7496` and re-validate cautiously.

## Safety

* The 2% daily-loss circuit breaker is a hard floor. Do not disable it.
* The $100k daily notional cap prevents accidental over-exposure on Paper.
* Every IBKR order goes through a Bracket: parent + stop + take-profit, so a
  position cannot be left unprotected if the engine crashes.
* All open positions are flattened at 3:45 PM ET — the engine never holds
  overnight.
* Never commit `.env`, `config.yaml`, real bot tokens, or your IBKR account
  ID. `.gitignore` already excludes those.

## Known limitations

* `ib_insync.reqRealTimeBars` returns 5-second bars. The engine's primary path
  uses `reqHistoricalDataAsync(barSize="1 min", keepUpToDate=True)`, which
  emits true completed 1-minute candles. The fallback path uses the 5s feed
  and aggregates locally via `BarAggregator`.
* Telegram and Finnhub gracefully no-op when credentials are missing.
* Live re-aggregation of partial bars is not supported; the engine only
  consumes completed bars.
