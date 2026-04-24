# SMC Liquidity Reversal — Research Module (V0)

> Status: **research / dry-run only**. The bot must not place orders
> from this module. The evaluator hard-codes
> `execution_allowed=false` and the broker layer remains gated by
> `trading.enabled`, `block_live_trading`, and reconciliation.

This document describes the V0 *Market Structure Detection Layer* used
by the strategy named `SMC_LIQUIDITY_REVERSAL_RESEARCH`. It builds the
bot's "eyes" so a future execution version can opt in once the
detections are validated against multiple symbols.

---

## 1. Scope

V0 implements:

- swing high / swing low detection (`bot/market_structure.py`)
- bullish liquidity-sweep detection
- bullish change-of-character (ChoCH) detection
- bullish fair-value gap (FVG) detection
- bullish order-block (OB) detection
- structural stop calculation
- a deterministic dry-run plan (`bot/strategy_engine.py`)
- CLI entry points: `scan-smc`, `scan-smc-watchlist`
- JSON output to `data/smc_setups/YYYY-MM-DD-SYMBOL.json`

V0 explicitly does **not** implement:

- order placement (no `place_order` call from any new code path)
- automatic limit orders
- live trading
- shorting / options / crypto / forex
- autonomous LLM trading
- chart rendering (JSON only)

---

## 2. Engineering definitions

The SMC vocabulary is intentionally translated into deterministic
rules. Code never reads "ChoCH" or "FVG" as opaque concepts.

| SMC term            | Engineering definition |
|---------------------|------------------------|
| Swing high          | Candle whose `high` is strictly greater than the highs of `left_bars` candles before AND `right_bars` after. Confirmed only after `right_bars` complete. |
| Swing low           | Symmetric, with `low`. |
| Liquidity sweep     | A candle prints `low < swept_low` of the lowest confirmed prior swing low within `lookback_period` bars, then closes back above it. |
| ChoCH               | After a sweep, the first candle that **closes** above the most recent confirmed pivot high *before* the sweep, within `max_bars_after_sweep`. |
| Bullish FVG         | 3-candle imbalance where `candle3.low > candle1.high`. The middle candle's index is used to date the FVG. |
| Bullish order block | The last bearish (`close < open`) candle before the bullish impulse leg that produced the ChoCH. |
| Structural stop     | `min(sweep.sweep_low, order_block.low) - buffer_cents`. |

---

## 3. Lookahead safety

Swing points are only `confirmed=true` once `right_bars` candles have
elapsed after them. The detectors expose `allow_unconfirmed=True` only
for charting / debugging; every downstream call site uses the
confirmed-only default. Tests assert this (see
`tests/test_market_structure.py::test_swings_are_not_confirmed_at_the_right_edge`).

---

## 4. Strategy block (`config/strategy.yaml`)

```yaml
strategies:
  SMC_LIQUIDITY_REVERSAL_RESEARCH:
    enabled: true
    research_only: true
    execution_allowed: false
    dry_run_only: true
    market_filter:
      block_if_market_regime: [risk_off, crisis, unknown]
    swing_detection: {left_bars: 2, right_bars: 2}
    sweep:
      lookback_period: 20
      require_close_back_above_swept_low: true
      allow_intraday_wick_sweep: true
    choch:
      max_bars_after_sweep: 10
      require_close_above_pivot_high: true
    fvg:
      require_fvg: true
      min_fvg_size_pct: 0.10
      max_fvg_distance_from_choch_bars: 3
    order_block: {enabled: true, method: last_down_close_before_choch}
    entry:
      type: limit_at_fvg_top
      max_days_to_fill_limit: 3
      reject_if_price_extended_from_entry_pct: 3
    stop: {type: structural, buffer_cents: 0.05, max_allowed_stop_pct: 5.0}
    risk:
      max_account_risk_per_trade_pct: 1.0
      max_equity_per_position_pct: 10
      min_reward_to_risk: 2.0
    profit_management:
      target_method: prior_swing_high
      partial_sell_pct: 50
      move_stop_to_breakeven_after_partial: true
      trail_method: close_below_ema
      trail_ema_period: 10
```

`unknown` is included in `block_if_market_regime` because the pre-open
report sets that regime when both VIX and trend data are missing — we
should not open new structural longs without confirmation.

---

## 5. Evaluator output

`evaluate_smc_liquidity_reversal()` returns a `StrategyEvaluation`
whose `to_dict()` mirrors the schema in section 16 of the brief:

```json
{
  "strategy": "SMC_LIQUIDITY_REVERSAL_RESEARCH",
  "symbol": "AAPL",
  "timeframe": "daily",
  "approved_for_dry_run": true,
  "execution_allowed": false,
  "market_regime": "neutral",
  "candle_count": 250,
  "sequence": {
    "sweep":       {"found": true, "swept_low_price": 187.20, "sweep_low": 186.40, "close": 188.10, ...},
    "choch":       {"found": true, "pivot_high_broken": 192.40, "close": 193.10, ...},
    "fvg":         {"found": true, "low": 189.10, "high": 190.20, "size_pct": 0.58, ...},
    "order_block": {"found": true, "low": 188.50, "high": 189.40, ...}
  },
  "trade_plan": {
    "entry_type": "limit_at_fvg_top",
    "entry_price": 190.20,
    "entry_zone": {"low": 189.10, "high": 190.20},
    "structural_stop": 186.35,
    "risk_per_share": 3.85,
    "stop_distance_pct": 2.02,
    "target_1": 196.00,
    "risk_reward_to_target_1": 1.51,
    "qty_by_risk": 25,
    "position_value": 4755.00,
    "extension_pct_vs_latest_close": 0.42,
    "execution_allowed": false,
    "research_only": true
  },
  "rejection_reasons": [],
  "notes": []
}
```

When any rule rejects the setup the corresponding string is appended
to `rejection_reasons` and `approved_for_dry_run` becomes `false`.

---

## 6. Rejection rules

The evaluator can append the following rejection strings (substrings;
wording may include numbers):

| Rule                                      | Source |
|-------------------------------------------|--------|
| `market_regime=… blocks new setups`       | `market_filter.block_if_market_regime` |
| `insufficient_candles`                    | requires `left + right + 5` |
| `no_liquidity_sweep`                      | section 8 |
| `no_choch_after_sweep`                    | section 9 |
| `no_bullish_fvg`                          | section 10 (only if `require_fvg`) |
| `no_order_block`                          | section 11 (only if `order_block.enabled`) |
| `no_target_1_swing_high`                  | no eligible pivot high before sweep |
| `target_1_not_above_entry`                | swept area too close to entry |
| `stop_distance_pct … > max …`             | `stop.max_allowed_stop_pct` |
| `r_r_to_target_1 … < …`                   | `risk.min_reward_to_risk` |
| `qty_by_risk_non_positive`                | risk-based sizing returns 0 shares |
| `risk_per_share_non_positive`             | malformed setup |
| `price_extended_from_entry_pct … (no chasing)` | `entry.reject_if_price_extended_from_entry_pct` |

The notes list captures non-fatal events (e.g. quantity trimmed by
`max_equity_per_position_pct`).

---

## 7. CLI

```bash
# single symbol from a CSV
python -m bot.cli scan-smc --symbol AAPL --csv data/candles/AAPL.csv

# single symbol from IBKR (read-only daily bars)
python -m bot.cli scan-smc --symbol AAPL --ibkr --ibkr-days 300

# whole watchlist (config/watchlist.yaml -> equities) using a CSV folder
python -m bot.cli scan-smc-watchlist --candles-dir data/candles

# visual validation: render the annotated chart too
python -m bot.cli scan-smc --symbol AAPL --timeframe daily --ibkr --chart

# batch chart validation (first 10 watchlist symbols)
python -m bot.cli scan-smc-watchlist --timeframe daily --ibkr --chart --limit 10

# watchlist research digest: scan 20, render charts, send Telegram summary
python -m bot.cli scan-smc-watchlist --timeframe daily --ibkr --chart \
    --limit 20 --telegram

# scan today's dynamic high-volume/high-beta watchlist (rebuilds if missing)
python -m bot.cli scan-smc-watchlist --source dynamic --timeframe daily \
    --ibkr --chart --limit 20 --telegram

# scan the static list (config/watchlist.yaml -> static_core / equities)
python -m bot.cli scan-smc-watchlist --source static --timeframe daily \
    --ibkr --chart --limit 20 --telegram
```

See `docs/watchlist-builder.md` for how the dynamic watchlist is built
and `docs/market-regime.md` for how the regime label / confidence feed
into the scan output.

CSV format:

```
timestamp,open,high,low,close,volume
2025-01-02,184.10,186.50,183.50,186.10,55321000
...
```

Useful flags:

- `--market-regime risk_off` — override; default is the regime from the
  most recent `data/pre_open_news/*.json`, falling back to `neutral`.
- `--account-equity 1000000` — enables risk-based position sizing
  (mock dollars, never read from IBKR unless you opt in).
- `--available-cash 1000000` — informational; recorded in the JSON.
- `--use-account-values` — opt-in: read paper-account equity / cash
  from IBKR (read-only). Even with this flag the bot never submits
  orders; the values feed only the dry-run sizing math.
- `--chart` — also render the validation PNG to `data/debug_charts/`.
  Charts are written for **every** scan you ask for, including
  rejected and incomplete setups.
- `--no-save` — skip writing JSON under `data/smc_setups/`.
- `--ibkr` (single) / per-symbol fallback (watchlist) — opt-in IBKR
  read-only daily bars; never places orders.
- `--limit N` (watchlist) — stop after the first N symbols.
- `--telegram` (watchlist) — send a concise research digest to the
  operator's Telegram chat after the scan finishes. The digest is
  ranked by `smc_quality_score` and never contains account numbers,
  dollar risk, or IBKR socket data — it is a research notification,
  not a trade signal.

Each invocation also writes a journal event under category
`smc_research` so reviewers can audit which symbols were scanned.

---

## 8. Visual validation pack

The chart pipeline lives in `bot/smc_chart.py`. It is intentionally
independent of the broker: the renderer never imports
`bot.broker`, never opens an IBKR socket, and never mutates the
evaluation it draws.

### Run a chart

```bash
python -m bot.cli scan-smc \
    --symbol AAPL --timeframe daily --ibkr \
    --account-equity 1000000 --available-cash 1000000 \
    --chart
```

Output:

* `data/smc_setups/YYYY-MM-DD-AAPL.json` — structured payload
  (now includes `chart_path`, `candles_start`, `candles_end`,
  `detected_levels`, `validation_notes`).
* `data/debug_charts/YYYY-MM-DD-AAPL-daily-smc.png` — the annotated
  candlestick chart.

### Marker key

Every marker now carries an inline text label with the **exact date
and price** from the underlying candle, so a reviewer can cross-check
the detector against their own chart without guessing which bar the
bot latched onto.

| Marker / shape                              | Inline label               | Meaning |
|---------------------------------------------|----------------------------|---------|
| Green / red candle bodies & wicks           | —                          | OHLC bars (green = up close, red = down close). |
| Black down-triangle (▼)                     | —                          | Confirmed swing high. |
| Black up-triangle (▲)                       | —                          | Confirmed swing low. |
| Translucent pink vertical band              | —                          | Sweep candle highlighted at ±0.4 bars. |
| Red ✕                                        | `Sweep YYYY-MM-DD low=…`   | Liquidity-sweep candle (low printed below the swept swing low). |
| Red dotted horizontal line                  | `Swept low YYYY-MM-DD price=…` | The swept swing-low price ("buy-side liquidity below"). |
| Translucent purple vertical band            | —                          | ChoCH candle highlighted. |
| Purple ★                                    | `ChoCH YYYY-MM-DD close=… broke=…` | ChoCH candle (close above the most recent pivot high before the sweep). |
| Blue translucent rectangle                  | `FVG low–high`             | Bullish FVG zone (`candle1.high → candle3.low`). |
| Brown translucent rectangle                 | `OB low–high`              | Bullish order block (last bearish candle before the impulse). |
| Blue dashed horizontal line                 | `Entry …`                  | Proposed limit entry (`fvg.high` by default). |
| Red dashed horizontal line                  | `Stop …`                   | Structural stop (`min(sweep_low, ob_low) − buffer`). |
| Green dashed horizontal line                | `T1 …`                     | Target 1 — **nearest** confirmed buy-side-liquidity level above entry that satisfies R/R ≥ `min_risk_reward` and fits inside `max_target_distance_pct`. |
| Yellow box (bottom-right)                   | —                          | Missing structural steps when the sequence is incomplete. |
| Pink box (bottom-left)                      | —                          | Rejection reasons recorded by the engine. |
| Top-right red banner                        | —                          | Permanent reminder that execution is disabled. |

### What to look for

A "good" chart matches what a human chart reader would label:

1. The ✕ should sit just below an obvious prior swing low. If it's
   sweeping a flat bar in a trending market, the bot has mis-detected.
2. The ★ should close decisively above the right pivot — not just
   wick over it.
3. The blue FVG rectangle should be a real imbalance the eye can
   see; the auto note "FVG size_pct=… is very small" warns when the
   rectangle is suspiciously thin.
4. The brown OB rectangle should be the last bearish candle before
   the bullish impulse leg.
5. Entry / stop / target lines should produce an R/R ≥ 2.0; the
   chart shows R/R only via the title status — the JSON has the
   exact value.

### Why visual validation is required before execution

V1 (execution) will not be unlocked until at least the following are
true:

* a human reviewer has flipped through ≥ 3 weeks of charts across
  ≥ 5 symbols and confirmed the detector matches their reading,
* a paper-trading forward test has run for ≥ 2 weeks without
  spurious approvals,
* `tests/test_smc_chart.py` and the existing market-structure tests
  remain green.

> **Warning** — chart output does **not** imply trade approval.
> Even an "APPROVED FOR DRY RUN" PNG is still gated by every
> existing safety layer (`trading.enabled`, `block_live_trading`,
> reconciliation, asset allow-list, regime filter, …).

### Target 1 selection (V1)

Target 1 is the price level we expect the dry-run plan to look for
buy-side liquidity. **V0 picked the highest confirmed swing high in
the full history**, which produced unrealistically optimistic R/R
ratios on extended uptrends — for example a TSLA scan returned
`target_1 = 498.83` and `risk_reward ≈ 5.95` against an entry in the
low $400s, because the selector latched onto a multi-month-old
liquidity pocket.

V1 uses the **nearest buy-side liquidity** above entry instead:

```yaml
target:
  method: nearest_buy_side_liquidity
  lookback_bars_before_sweep: 60
  max_target_distance_pct: 25.0
  min_risk_reward: 2.0
```

Candidate hierarchy evaluated in order:

1. Every confirmed swing high in the `lookback_bars_before_sweep`
   window up to and including the sweep bar, above `entry_price`.
2. The ChoCH pivot high (if above entry).
3. The highest bar inside the same lookback window (used as a
   fallback when no pivot survived the confirmation rules).

The selector then:

* filters candidates to those within `max_target_distance_pct` of
  entry,
* sorts by *proximity* to entry (lowest price first — that is the
  "nearest" buy-side liquidity),
* picks the first candidate whose reward/risk meets
  `min_risk_reward`.

If no candidate qualifies the evaluator records a rejection reason:

| Scenario                                              | Rejection reason                         |
|-------------------------------------------------------|------------------------------------------|
| No candidate is above entry                           | `target_1_not_above_entry`               |
| Every candidate sits further than `max_target_distance_pct` | `target_1_too_far (> N.NN% from entry)` |
| Candidates exist but none reach `min_risk_reward`     | `r_r_to_target_1 below min N.NN for all candidates` |
| A target was selected but the final R/R is below `risk.min_reward_to_risk` | `r_r_to_target_1 N.NN < M.MM` |

Every candidate considered — including the rejected ones — is
written to `trade_plan.target_debug.candidates` so a reviewer can
see exactly why the bot picked (or didn't pick) a given level:

```json
"target_debug": {
  "method": "nearest_buy_side_liquidity",
  "candidates": [
    {
      "timestamp": "2025-02-14",
      "price": 418.50,
      "type": "swing_high",
      "distance_pct_from_entry": 3.85,
      "risk_reward": 1.80,
      "selected": false
    },
    {
      "timestamp": "2025-03-07",
      "price": 432.10,
      "type": "swing_high",
      "distance_pct_from_entry": 7.20,
      "risk_reward": 2.40,
      "selected": true
    }
  ],
  "rejection_reason": null,
  "lookback_bars_before_sweep": 60,
  "max_target_distance_pct": 25.0,
  "min_risk_reward": 2.0
}
```

**Why nearest rather than highest?** In the TSLA example the "nice
big high" sat outside the 60-bar lookback window and reflected a
different trend regime. A dry-run plan that targets it has an
optically high R/R but an extremely low hit rate. Nearest buy-side
liquidity is the level market-structure traders actually plan to
scale out at, and it is testable without a look-ahead peek at
multi-month-old prints.

---

### Failure modes the chart helps catch

* The detector latches onto the wrong pivot high because an earlier
  cluster has equal highs — visible because the ★ sits below an
  obvious higher pivot.
* The FVG selector picks an FVG far away from the OB — visible
  because the blue rectangle floats above the order block instead
  of being directly above it.
* The structural stop is set inside an obvious noise band — visible
  because the red dashed line ends up inside the prior consolidation
  range.

---

## 8b. Watchlist scanner (research digest)

`scan-smc-watchlist` can now rank every symbol in `config/watchlist.yaml`
by two research-only signals:

- **`smc_quality_score`** — an integer in `[0, 100]`. The scorer adds
  points for a full Sweep → ChoCH → FVG → OB sequence, a stop within
  the configured max distance, R/R ≥ 2, modest price extension, an
  allowed market regime, and a valid selected target. It subtracts
  points for the inverse conditions. **The score never gates
  execution.** A setup with a 95 score that was rejected by regime,
  stop, R/R, or `target_1_too_far` stays rejected. Score only
  affects the order in which setups appear in the digest.
- **`bucket`** — a label that summarises why a symbol is (or is not)
  actionable:

  | Bucket | Meaning |
  | --- | --- |
  | `WATCH_NOW` | Full structure found, only held back by regime=unknown or waiting for a pullback. |
  | `NEAR_ENTRY` | Full structure and latest close within ±1.5% of the planned entry. |
  | `TOO_EXTENDED` | Full structure but price already ran more than 3% above entry. |
  | `STRUCTURE_INCOMPLETE` | Sweep found but at least one of ChoCH / FVG / OB missing. |
  | `INVALID_RISK` | Stop too wide, R/R below the configured floor, or `target_1_too_far`. |
  | `BLOCKED` | `market_regime` is `risk_off` / `crisis`, or the symbol is news-halted. |

The batch is persisted to
`data/smc_setups/YYYY-MM-DD-watchlist-summary.json`, with this shape:

```json
{
  "date": "YYYY-MM-DD",
  "timeframe": "daily",
  "symbols_scanned": 20,
  "buckets": {
    "WATCH_NOW": [...],
    "NEAR_ENTRY": [...],
    "TOO_EXTENDED": [...],
    "STRUCTURE_INCOMPLETE": [...],
    "INVALID_RISK": [...],
    "BLOCKED": []
  },
  "top_by_score": [...],
  "closest_to_entry": [...],
  "execution_allowed": false,
  "research_only": true
}
```

Each entry inside a bucket re-exports `symbol`, `bucket`,
`smc_quality_score`, structural booleans, entry / stop / target,
R/R, stop-distance %, extension %, `rejection_reasons`, chart path,
and the score breakdown so reviewers can diff decisions day-to-day.

When `--telegram` is supplied the scanner also emits:

```
SMC Watchlist Research Digest — YYYY-MM-DD
Scanned: 20
WATCH_NOW: 3  NEAR_ENTRY: 1  TOO_EXTENDED: 4
STRUCTURE_INCOMPLETE: 9  INVALID_RISK: 3  BLOCKED: 0

Top by score
• AAPL | NEAR_ENTRY | score=85 | entry=184.20 | R/R=2.45
...

Closest to entry
• MSFT | NEAR_ENTRY | entry=410.05 | R/R=2.10 | ext=+0.30%
...

Research only. execution_allowed=false. Digest is not a trade signal.
```

No account numbers, dollar amounts, API keys, or position sizes are
included in the digest. Telegram privacy-mode redaction still runs on
top of the message as a second line of defence.

### Research, not approval

- `smc_quality_score` is a **ranking heuristic**. It does not set
  `approved_for_dry_run` and it does not flip `execution_allowed`.
- Buckets are derived from the evaluator's own `rejection_reasons`
  plus regime state — they do not reopen any rejected setup.
- Even a `WATCH_NOW` bucket means "eyeball this chart before anything
  else", never "submit an order".

---

## 9. Testing

Five suites cover the V0 / V1 surface:

- `tests/test_market_structure.py` — swing detection (incl. lookahead
  safety), sweep, ChoCH wick rejection, FVG, order block.
- `tests/test_structural_stop.py` — stop math, buffer, missing leg
  fallback, max stop distance trips a rejection.
- `tests/test_smc_liquidity_reversal.py` — full evaluator, including:
  - `execution_allowed` is always `false`,
  - `risk_off` regime blocks new setups,
  - low R/R triggers `r_r_to_target_1` rejection,
  - `--no chasing` rejection when latest close runs away from entry,
  - dry-run plan structure matches the documented schema.
- `tests/test_smc_chart.py` — visual debug pack:
  - `--chart` writes a PNG, even for rejected / incomplete setups,
  - `--account-equity` drives `qty_by_risk` without touching IBKR,
  - `--use-account-values` is opt-in, read-only, never places orders,
  - the persisted JSON includes `chart_path` only when a chart was
    rendered,
  - the renderer module never imports `bot.broker`,
  - every structural element carries an inline date + price label
    (Sweep / Swept low / ChoCH / FVG / OB / Entry / Stop / T1).
- `tests/test_smc_scanner.py` — watchlist scanner:
  - each bucket label is reachable under a distinct scenario
    (WATCH_NOW / NEAR_ENTRY / TOO_EXTENDED / STRUCTURE_INCOMPLETE /
    INVALID_RISK / BLOCKED),
  - `smc_quality_score` never pushes a rejected setup into
    `approved_for_dry_run` and never lifts it out of BLOCKED,
  - the Telegram digest contains the headline and counts but never
    leaks account numbers or dollar risk,
  - the batch-summary JSON is written with `execution_allowed=false`
    and `research_only=true`,
  - `scan-smc-watchlist` never calls `Broker.place_order`, even with
    `--telegram` enabled.
- `tests/test_smc_target.py` — V1 target selector:
  - nearest-buy-side-liquidity preference over distant highs,
  - skips candidates that fail the R/R floor,
  - rejects when everything sits beyond `max_target_distance_pct`,
  - the highest high outside the lookback window never wins,
  - `target_debug` payload is always populated (even on rejection),
  - candidate shape matches the documented JSON schema.

---

## 10. Safety summary

- No new code path imports `Broker.place_order`.
- The evaluator sets `execution_allowed=False` unconditionally.
- Existing safety layers stay intact:
  - `trading.enabled=false`,
  - `block_live_trading=true`,
  - reconciliation gate,
  - asset-class allow-list,
  - max-positions cap.
- The new `unknown` regime block is *additive*: it can only tighten
  posture, not loosen it.

The bot must first learn to see structure before it is allowed to
trade structure.
