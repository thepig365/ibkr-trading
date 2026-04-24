# Cursor Implementation Brief — SMC Liquidity Reversal Research Module

## Important Strategic Reset

This document replaces the earlier breakout-first implementation plan.

We are **not** building a naive breakout bot.

We are also **not** assuming that SMC / ICT language is automatically “institutional-grade alpha.” The goal is to translate liquidity-sweep / ChoCH / FVG / order-block concepts into **testable market-structure rules**.

This must be built as a **research and dry-run module first**, not as an execution module.

---

# 0. Current Implementation Scope

## Implement This Week

```text
V0 — Market Structure Detection Layer
```

Build the bot’s “eyes” first.

Do not place trades.  
Do not create live orders.  
Do not create autonomous execution.  
Do not connect this strategy to real execution yet.

The current deliverable is:

```text
- market_structure.py
- swing high / swing low detection
- liquidity sweep detection
- ChoCH detection
- bullish FVG detection
- bullish order block detection
- structural stop calculation
- dry-run setup report
- chart/debug output
- unit tests
```

## Do Not Implement Yet

```text
- actual IBKR order placement
- automatic limit orders
- live trading
- options
- shorting
- full strategy router integration
- autonomous LLM trading
```

---

# 1. Strategy Name

```text
SMC_LIQUIDITY_REVERSAL_RESEARCH
```

Future execution version may be called:

```text
SMC_LIQUIDITY_REVERSAL
```

But for now, keep it as a research/detection module only.

---

# 2. Strategy Type

```text
Long-only US stock structural reversal strategy
```

This module looks for:

```text
Liquidity Sweep
→ Change of Character
→ Fair Value Gap / Order Block
→ Return to repair zone
→ Structural stop
→ Asymmetric dry-run plan
```

The bot must **not** buy breakouts.  
The bot must **not** chase ChoCH candles.  
The bot should only generate a dry-run plan after the full sequence is detected.

---

# 3. Conceptual Translation

## SMC / ICT Language

```text
Liquidity sweep
ChoCH
FVG
Order Block
Buy-side liquidity
Sell-side liquidity
```

## Engineering Language

```text
Stop-run below recent swing low
Bullish structural reversal confirmation
Three-candle imbalance zone
Last bearish candle before impulse move
Prior swing high target
Recent swing low invalidation
```

Cursor must implement the engineering version.

---

# 4. Required Files

Create or update:

```text
bot/market_structure.py
bot/strategy_engine.py
bot/risk_engine.py
bot/cli.py
config/strategy.yaml
tests/test_market_structure.py
tests/test_smc_liquidity_reversal.py
tests/test_structural_stop.py
docs/smc-liquidity-reversal.md
```

Optional debug outputs:

```text
data/debug_charts/
data/smc_setups/
```

V0 can output JSON only. Chart rendering can be added later.

---

# 5. Data Requirements

The module must accept OHLCV candles.

Minimum required fields:

```text
timestamp
open
high
low
close
volume
```

Recommended timeframes:

```text
daily
60-minute
15-minute
5-minute
```

V0 can start with daily data, but the design must support intraday later.

---

# 6. Market Structure Module

Create:

```text
bot/market_structure.py
```

## Core Functions

```python
def detect_swing_highs(candles, left: int = 2, right: int = 2) -> list:
    pass

def detect_swing_lows(candles, left: int = 2, right: int = 2) -> list:
    pass

def detect_liquidity_sweep(candles, lookback: int = 20) -> list:
    pass

def detect_choch_after_sweep(candles, sweep_event) -> dict | None:
    pass

def detect_bullish_fvg(candles) -> list:
    pass

def detect_bullish_order_block(candles, choch_event) -> dict | None:
    pass

def calculate_structural_stop(setup, buffer_cents: float = 0.05) -> float:
    pass
```

---

# 7. Swing High / Swing Low Definitions

## Swing High

A candle is a swing high if:

```text
its high is higher than the highs of N candles to the left
and higher than the highs of N candles to the right
```

Default:

```yaml
swing_detection:
  left_bars: 2
  right_bars: 2
```

## Swing Low

A candle is a swing low if:

```text
its low is lower than the lows of N candles to the left
and lower than the lows of N candles to the right
```

Important:

```text
A swing point is only confirmed after right_bars have completed.
Do not use future data in live detection.
```

For live / forward mode:

```text
Only confirmed swing points may be used.
```

Return shape:

```json
{
  "type": "swing_high",
  "index": 0,
  "timestamp": "",
  "price": 0.0,
  "left_bars": 2,
  "right_bars": 2,
  "confirmed": true
}
```

---

# 8. Liquidity Sweep Detection

## Bullish Liquidity Sweep

A bullish liquidity sweep occurs when:

```text
1. price trades below a significant prior swing low
2. the break is not accepted
3. price closes back above the swept swing low
```

Default detection:

```text
swept_low = lowest confirmed swing low within lookback period
sweep_event = candle.low < swept_low.price
confirmation = candle.close > swept_low.price
```

Optional divergence confirmation can be added later, but not required in V0.

Config:

```yaml
smc_liquidity_reversal:
  sweep:
    lookback_period: 20
    require_close_back_above_swept_low: true
    allow_intraday_wick_sweep: true
```

Reject if:

```text
- price closes far below swept low
- sweep candle has abnormal downside continuation
- market_regime = risk_off
```

Return shape:

```json
{
  "found": true,
  "timestamp": "",
  "index": 0,
  "swept_low_price": 0.0,
  "sweep_low": 0.0,
  "close": 0.0,
  "closed_back_above": true
}
```

---

# 9. Change of Character Detection

## Bullish ChoCH

After a bullish sweep, price must break bullish structure.

Definition:

```text
After the sweep, identify the most recent lower high / pivot high before the sweep.
ChoCH occurs when a later candle closes above that pivot high.
```

Rules:

```text
- ChoCH must happen after sweep_event
- ChoCH must occur within max_choch_bars_after_sweep
- ChoCH requires candle close above pivot high, not just wick
```

Config:

```yaml
smc_liquidity_reversal:
  choch:
    max_bars_after_sweep: 10
    require_close_above_pivot_high: true
```

Reject if:

```text
- no pivot high is identifiable
- ChoCH takes too long
- ChoCH candle is low volume and weak
- price chops sideways with no structural shift
```

Return shape:

```json
{
  "found": true,
  "timestamp": "",
  "index": 0,
  "pivot_high_broken": 0.0,
  "close": 0.0,
  "bars_after_sweep": 0
}
```

---

# 10. Bullish Fair Value Gap Detection

## Bullish FVG Definition

A bullish FVG is a 3-candle imbalance:

```text
Candle 3 low > Candle 1 high
FVG zone = Candle 1 high to Candle 3 low
```

Rules:

```text
- FVG should appear during or immediately after the impulse leg that creates ChoCH
- FVG must be above or near the order block zone
- FVG must have enough size to be tradable
```

Config:

```yaml
smc_liquidity_reversal:
  fvg:
    require_fvg: true
    min_fvg_size_pct: 0.10
    max_fvg_distance_from_choch_bars: 3
```

Reject if:

```text
- no bullish FVG appears after the sweep/ChoCH sequence
- FVG is too tiny
- FVG is too far above structural stop, creating poor R/R
```

Return shape:

```json
{
  "found": true,
  "start_index": 0,
  "end_index": 2,
  "timestamp": "",
  "low": 0.0,
  "high": 0.0,
  "size_pct": 0.0
}
```

---

# 11. Bullish Order Block Detection

## Bullish Order Block

Definition for V0:

```text
The last bearish candle before the bullish impulse move that causes ChoCH.
```

Rules:

```text
- candle close < candle open
- appears before the ChoCH impulse
- order block high/low define the repair zone and structural invalidation area
```

Config:

```yaml
smc_liquidity_reversal:
  order_block:
    enabled: true
    method: last_down_close_before_choch
```

Order block zone:

```text
OB low = candle.low
OB high = candle.high
```

Return shape:

```json
{
  "found": true,
  "index": 0,
  "timestamp": "",
  "low": 0.0,
  "high": 0.0,
  "open": 0.0,
  "close": 0.0
}
```

---

# 12. Entry Zone Logic

## No Chasing

The bot must reject market-order chasing.

Do not buy:

```text
- the sweep candle
- the ChoCH candle
- an extended move after ChoCH
```

## Dry-Run Entry Zone

Generate one or both:

```text
entry_zone_fvg_top = bullish_fvg.high
entry_zone_ob_top = order_block.high
```

Preferred V0 entry:

```text
limit_entry = top of FVG
```

Alternative:

```text
limit_entry = top of Order Block
```

Config:

```yaml
smc_liquidity_reversal:
  entry:
    type: limit_at_fvg_top
    max_days_to_fill_limit: 3
    reject_if_price_extended_from_entry_pct: 3
```

The module should output a dry-run plan only:

```json
{
  "entry_type": "limit_at_fvg_top",
  "entry_price": 0,
  "entry_zone": {
    "low": 0,
    "high": 0
  },
  "execution_allowed": false
}
```

---

# 13. Structural Stop

## Stop Definition

Use structure, not arbitrary fixed percentage.

```text
structural_stop = min(sweep_low, order_block_low) - buffer
```

Default buffer:

```yaml
stop:
  type: structural
  buffer_cents: 0.05
  max_allowed_stop_pct: 5.0
```

Reject if:

```text
risk_per_share / entry_price > 5%
```

Reason:

```text
If the stop must be wider than 5%, the structure is too loose for this setup.
```

---

# 14. Position Sizing

Position sizing is risk-based.

Formula:

```text
risk_per_share = entry_price - structural_stop
max_dollar_risk = account_equity * 0.01
qty_by_risk = floor(max_dollar_risk / risk_per_share)
```

Also cap by exposure:

```text
position_value <= account_equity * max_equity_per_position_pct
```

Config:

```yaml
risk:
  max_account_risk_per_trade_pct: 1.0
  max_equity_per_position_pct: 10
```

Reject if:

```text
- qty_by_risk <= 0
- risk_per_share <= 0
- stop distance > 5%
- position cost exceeds cash
```

---

# 15. Profit Targets

## Target 1

```text
Target 1 = prior swing high before the downward sweep
```

This represents buy-side liquidity.

At Target 1:

```text
sell 50%
move stop to breakeven
```

Config:

```yaml
profit_management:
  target_method: prior_swing_high
  partial_sell_pct: 50
  move_stop_to_breakeven_after_partial: true
```

## Remaining Position

Trail remaining position using:

```text
close below 10-period EMA
```

Config:

```yaml
profit_management:
  trail_method: close_below_ema
  trail_ema_period: 10
```

V0 only needs to calculate these levels.  
Do not execute exits yet.

---

# 16. Required Strategy Evaluation Output

Implement:

```python
evaluate_smc_liquidity_reversal(symbol: str, candles) -> StrategyEvaluation
```

Output shape:

```json
{
  "strategy": "SMC_LIQUIDITY_REVERSAL_RESEARCH",
  "symbol": "AAPL",
  "approved_for_dry_run": true,
  "execution_allowed": false,
  "sequence": {
    "sweep": {
      "found": true,
      "timestamp": "",
      "swept_low": 0,
      "sweep_low": 0,
      "close_back_above": true
    },
    "choch": {
      "found": true,
      "timestamp": "",
      "pivot_high_broken": 0,
      "close": 0
    },
    "fvg": {
      "found": true,
      "low": 0,
      "high": 0
    },
    "order_block": {
      "found": true,
      "low": 0,
      "high": 0
    }
  },
  "trade_plan": {
    "entry_price": 0,
    "structural_stop": 0,
    "risk_per_share": 0,
    "stop_distance_pct": 0,
    "target_1": 0,
    "risk_reward_to_target_1": 0,
    "qty_by_risk": 0
  },
  "rejection_reasons": []
}
```

Critical:

```text
execution_allowed must always be false in V0.
```

---

# 17. CLI Commands

Add:

```bash
python -m bot.cli scan-smc --symbol SYMBOL --timeframe daily
```

Output:

```text
Strategy: SMC_LIQUIDITY_REVERSAL_RESEARCH
Symbol: SYMBOL
Timeframe: daily

Sequence:
- Liquidity Sweep: FOUND / NOT FOUND
- ChoCH: FOUND / NOT FOUND
- Bullish FVG: FOUND / NOT FOUND
- Order Block: FOUND / NOT FOUND

Dry-run plan:
- Limit entry:
- Structural stop:
- Stop distance:
- Target 1:
- R/R:
- Qty by 1% risk:

Execution:
- Disabled
- Research mode only
```

Add batch scan:

```bash
python -m bot.cli scan-smc-watchlist --timeframe daily
```

---

# 18. Debug / Chart Output

The bot should save structured debug output:

```text
data/smc_setups/YYYY-MM-DD-SYMBOL.json
```

Optional future chart output:

```text
data/debug_charts/YYYY-MM-DD-SYMBOL-smc.png
```

V0 can output JSON only.  
Chart rendering can be added later.

---

# 19. Config Example

Add to:

```text
config/strategy.yaml
```

```yaml
strategies:
  SMC_LIQUIDITY_REVERSAL_RESEARCH:
    enabled: true
    research_only: true
    execution_allowed: false
    dry_run_only: true

    market_filter:
      block_if_market_regime:
        - risk_off
        - crisis

    swing_detection:
      left_bars: 2
      right_bars: 2

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

    order_block:
      enabled: true
      method: last_down_close_before_choch

    entry:
      type: limit_at_fvg_top
      max_days_to_fill_limit: 3
      reject_if_price_extended_from_entry_pct: 3

    stop:
      type: structural
      buffer_cents: 0.05
      max_allowed_stop_pct: 5.0

    risk:
      max_account_risk_per_trade_pct: 1.0
      max_equity_per_position_pct: 10

    profit_management:
      target_method: prior_swing_high
      partial_sell_pct: 50
      move_stop_to_breakeven_after_partial: true
      trail_method: close_below_ema
      trail_ema_period: 10
```

---

# 20. Tests

Create:

```text
tests/test_market_structure.py
tests/test_smc_liquidity_reversal.py
tests/test_structural_stop.py
```

## Required Test Cases

```text
1. detects swing highs correctly
2. detects swing lows correctly
3. does not use future data in live mode
4. detects bullish liquidity sweep only when price trades below prior confirmed swing low
5. rejects sweep if close does not reclaim swept low
6. detects ChoCH only after sweep
7. rejects ChoCH if candle only wicks above pivot high but closes below
8. detects bullish FVG where candle3.low > candle1.high
9. rejects if no FVG exists
10. detects bullish order block as last down-close candle before ChoCH impulse
11. calculates structural stop below min(sweep_low, order_block_low)
12. rejects setup if stop distance > 5%
13. calculates qty by 1% account risk
14. rejects market-order chasing
15. generates dry-run plan only
16. confirms execution_allowed is always false in V0
17. rejects setup in risk_off regime
18. rejects if R/R to target 1 is below 2.0
```

---

# 21. Weekly Build Plan

## Day 1–2 — Market Structure Detection

Build:

```text
market_structure.py
```

Only solve:

```text
- swing highs
- swing lows
- bullish FVG
- order block detection
```

No trading.

## Day 3–4 — Historical Visual / Debug Validation

Run detection on 3–5 selected stocks.

Save:

```text
data/smc_setups/
```

Manually inspect whether the machine’s detected structure matches human chart reading.

Do not proceed if detection is poor.

## Day 5 — Risk Lock

Implement:

```text
structural_stop
1% risk cap
5% max stop distance
dry-run rejection tests
```

No execution yet.

---

# 22. Critical Engineering Warnings

## 22.1 SMC Terms Are Ambiguous

Different traders define ChoCH, OB, and FVG differently.

Therefore:

```text
Do not rely on vague terms.
Use the mathematical definitions in this document.
```

## 22.2 Avoid Lookahead Bias

Swing highs/lows require right-side confirmation.

In live mode:

```text
A swing is only confirmed after right_bars candles have completed.
```

## 22.3 Daily vs Intraday Difference

A daily-chart FVG and a 5-minute FVG are not equivalent.

V0 must record timeframe clearly.

## 22.4 Do Not Overfit

Do not tune parameters on one chart until it looks perfect.

The same detection rules must be tested across multiple symbols.

## 22.5 Do Not Pretend This Is Proven Alpha Yet

This is a research module.

It becomes a trading strategy only after:

```text
- detection works
- backtest is clean
- forward paper testing is stable
- dry-run plans make sense
- risk engine rejects bad setups correctly
```

---

# 23. Final Cursor Instruction

Implement only:

```text
V0 Market Structure Detection Layer
```

The immediate goal is:

```text
Correct structural recognition.
Correct dry-run setup generation.
Correct rejection.
No execution.
```

Do not implement order placement.  
Do not implement live trading.  
Do not implement automatic limit orders.  
Do not connect this to IBKR execution yet.

The bot must first learn to see structure before it is allowed to trade structure.
