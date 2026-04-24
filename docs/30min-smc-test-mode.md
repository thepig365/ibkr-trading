# 30-Minute SMC/ICT Test Mode (Research Only)

## Purpose

The bot supports two bar granularities for the **SMC liquidity-reversal research** stack:

| Mode   | Use case | Relative speed / noise |
|--------|----------|-------------------------|
| `daily` | Slower, structural context over ~1Y of RTH daily bars | Lower noise, fewer candidates |
| `30min` | Faster feedback on 30m RTH bars over ~20 trading days | **Faster, noisier** — use for *dry-run* research only |

**This document describes scan/review behaviour only.** As of the current milestone:

- `execution_allowed` is **false** everywhere in payloads.
- `research_only` is **true** for research outputs.
- There is **no** automatic paper or live order placement, **no** `broker.place_order` call path, **no** market orders, and **no** bracket/OTO workflow yet.

## Configuration

### IBKR history (`config/settings.yaml` → `smc_timeframes`)

- **daily** — `duration: "1 Y"`, `bar_size: "1 day"`, `use_rth: true`, min/max bar counts cap what is loaded after the request.
- **30min** — `duration: "20 D"`, `bar_size: "30 mins"`, `what_to_show: TRADES`, `use_rth: true`, `min_bars: 100`, `max_bars: 300`.

Intraday history is **RTH only** (no extended hours in the request).

### Strategy thresholds (`config/strategy.yaml` → `strategies.SMC_LIQUIDITY_REVERSAL_RESEARCH.timeframes`)

`daily` and `30min` have **separate** numeric thresholds, for example:

- **30min** uses stricter `max_allowed_stop_pct`, `max_extension_pct`, higher floor on `min_risk_reward` vs daily in the product defaults, and `risk_per_trade_pct: 0.25` (research dry-run sizing only).
- **30min-only** session knobs: `avoid_first_minutes_after_open: 15`, `avoid_last_minutes_before_close: 15`, `max_hold_bars: 13` (documented for future research; not an execution hold).

## Session filters (30min)

For **review queue classification** on **30min** scans:

- US **RTH only** in the session guard: outside **09:30–16:00 ET** the guard does not treat rows as *entry-ready* for the purpose of the top review buckets.
- In the **first 15 minutes** after the cash open and the **last 15 minutes** before the cash close, candidates that would otherwise be **ready for manual chart review** (or **pullback watch**) are **demoted** to `STRUCTURE_WATCH` with a note, so the digest does not look like a real-time “trade me now” list during the noisiest parts of the session.

`daily` scans are **not** affected by this clock.

## CLI (examples)

```bash
python -m bot.cli scan-smc --symbol AAPL --timeframe 30min --ibkr --chart
python -m bot.cli scan-smc-watchlist --source dynamic --timeframe 30min --ibkr --chart --limit 20 --telegram
python -m bot.cli smc-review-queue --timeframe 30min --telegram --markdown --top 10 --include-charts
```

## Outputs

- **Charts** (when `--chart` is used): `data/debug_charts/YYYY-MM-DD-SYMBOL-30min-smc.png` (and `-daily-` for daily). The title includes the timeframe; annotations include structural timestamps from the bar data; a **research-only** banner is always present.
- **Watchlist batch summary**: `data/smc_setups/YYYY-MM-DD-30min-watchlist-summary.json` (and legacy / daily variants).
- **Review queue JSON**: `data/review_queue/YYYY-MM-DD-30min-smc-review-queue.json` (and `-daily-` for daily). Every item includes `"timeframe": "daily" | "30min"`.

## Safety recap

- No order placement, no paper execution, no live execution in this mode.
- Scheduler does **not** add 30-minute execution; optional future jobs may **report** only, never trade without a later explicit milestone.

For Telegram wording and command behaviour, see `docs/telegram-commands.md` and `docs/scheduler.md`.
