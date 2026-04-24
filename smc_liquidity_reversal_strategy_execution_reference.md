# SMC / ICT Liquidity Reversal Strategy — Execution Logic Reference

## Purpose

This document explains how the current SMC / ICT liquidity-reversal trading strategy is intended to work inside the IBKR paper trading bot.

Important: the bot is **not yet trading automatically**.  
The current stage is:

```text
structure recognition
→ dry-run trade plan
→ chart validation
→ rejection / approval logic
→ no execution yet
```

The strategy will only move toward paper execution after the structure detection, chart annotations, target logic, and risk engine are validated.

---

# 1. Core Strategy Idea

This strategy does **not** buy naive breakouts.

It looks for this sequence:

```text
price sweeps below a key low
→ retail stop-loss liquidity is taken
→ price reclaims the level
→ bullish structure shift appears
→ FVG / Order Block is created
→ price returns to the repair zone
→ entry is considered with tight structural stop
→ target is the nearest realistic buy-side liquidity
```

In short:

```text
Liquidity Sweep
→ ChoCH
→ FVG / OB
→ Pullback into repair zone
→ Buy Limit
→ Structural Stop
→ Target 1
```

The philosophy is:

> Do not chase price when emotion is at its peak. Wait for the market to break, sweep liquidity, repair structure, and return to a defined zone.

---

# 2. Market Regime Comes First

Before any setup is tradable, the bot checks the pre-open report and market regime.

If the regime is:

```text
risk_off
crisis
unknown
```

then new entries are blocked.

At the current stage, the system may show:

```text
market_regime = unknown
```

because VIX / VIX3M data fallback is not fully repaired yet. This is correct safety behavior: if the environment is unclear, the bot should not open new trades.

---

# 3. Swing High / Swing Low Detection

The bot first identifies structural points from OHLCV candles.

## Swing Low

A swing low is a candle whose low is lower than the lows of a defined number of candles to the left and right.

## Swing High

A swing high is a candle whose high is higher than the highs of a defined number of candles to the left and right.

These points define:

```text
sell-side liquidity = liquidity below lows
buy-side liquidity = liquidity above highs
```

They are the foundation for detecting sweeps, targets, and structural invalidation.

---

# 4. Liquidity Sweep

A bullish liquidity sweep occurs when price:

```text
1. trades below a prior confirmed swing low
2. sweeps liquidity below that low
3. closes back above the swept low
```

Example:

```text
Prior low: 246.00
Sweep low: 245.51
Close: 246.63
```

This suggests that price swept stops below 246 but did not accept lower prices.

This is the first sign of potential repair.

Important: a sweep alone is not enough to enter. Price can sweep and continue lower.

---

# 5. ChoCH — Change of Character

After a sweep, the bot waits for a bullish structural shift.

A bullish ChoCH occurs when:

```text
price closes above the pivot high / lower high that led into the sweep
```

This indicates the bearish structure may have broken and demand may be entering.

If there is no ChoCH, the setup is incomplete.

Example from the current scans:

```text
NVDA:
- Liquidity Sweep: FOUND
- ChoCH: NOT FOUND
- Result: REJECTED

AMD:
- Liquidity Sweep: FOUND
- ChoCH: NOT FOUND
- Result: REJECTED
```

This is correct behavior. The bot should not trade only because a low was swept.

---

# 6. FVG and Order Block

After the sweep and ChoCH, the bot looks for a footprint left by the bullish impulse.

## Bullish FVG

A bullish Fair Value Gap is currently defined as:

```text
Candle 3 low > Candle 1 high
```

The gap between them is the FVG zone.

The logic is that price moved upward aggressively and left an imbalance or unfilled area.

## Bullish Order Block

The current V0 definition is:

```text
the last bearish candle before the bullish impulse that caused ChoCH
```

This is treated as a possible repair zone and structural invalidation area.

---

# 7. No Chasing

The bot must not buy:

```text
- the sweep candle
- the ChoCH candle
- an extended move after ChoCH
- price far above the FVG / OB repair zone
```

The strategy waits for price to return to the repair zone.

The proposed entry is usually:

```text
Entry = top of FVG
```

or sometimes:

```text
Entry = top of Order Block
```

If current price is already too far above entry, the bot rejects the setup.

Example:

```text
AAPL:
Entry = 256.46
Latest close is 6.62% above entry
Result: REJECTED
Reason: no chasing
```

This is a critical rule. It prevents the bot from buying after the move has already run.

---

# 8. Structural Stop

This strategy does not use a traditional fixed 7–8% stop.

It uses a structural stop:

```text
structural_stop = min(sweep_low, order_block_low) - buffer
```

Example:

```text
AAPL:
Sweep low = 245.51
OB low = 245.51
Structural stop = 245.46
```

If price breaks this level, the logic of the sweep / repair setup is invalid.

---

# 9. Stop Distance Filter

SMC liquidity-reversal setups require tight structural risk.

Current rule:

```text
maximum stop distance = 5%
```

If the stop distance is wider than 5%, the setup is rejected.

Example:

```text
TSLA:
Entry = 362.50
Stop = 339.62
Stop distance = 6.31%
Result: REJECTED
```

The rejection is correct because the structure is too wide for this setup.

---

# 10. Target 1

Target 1 should represent the nearest realistic buy-side liquidity.

That usually means a meaningful swing high above entry.

The current target logic still needs improvement. The bot must not automatically choose a distant historical high just because it creates a beautiful R/R number.

Example issue:

```text
TSLA:
Target 1 = 498.83
R/R = 5.95
```

This may be too optimistic if it is selecting a distant swing high.

The next repair is:

```text
Target 1 should be the nearest valid buy-side liquidity level,
not the highest high in the full 300-candle history.
```

---

# 11. Risk / Reward Requirement

A setup should only remain valid if:

```text
R/R to Target 1 >= 2.0
```

But the target must be realistic.

The system should reject setups where:

```text
- target is too far away
- target is below or too close to entry
- R/R is under 2
- target is based on an unrealistic distant high
```

---

# 12. Position Sizing

Future execution will use risk-based sizing.

Formula:

```text
risk_per_share = entry_price - structural_stop
max_dollar_risk = account_equity × 1%
qty = floor(max_dollar_risk / risk_per_share)
```

Example:

```text
Account equity = 100,000
Max risk = 1,000
Entry = 256
Stop = 245
Risk per share = 11
Qty ≈ 90 shares
```

Current scans show:

```text
qty_by_risk = 0
```

because account equity is not yet being passed into the scan command.

Future options:

```bash
--account-equity 1000000
```

or:

```bash
--use-account-values
```

---

# 13. Full Future Execution Flow

The final paper-trading execution flow should be:

```text
1. Pre-open news report
   ↓
2. Determine market_regime
   ↓
3. Scan watchlist
   ↓
4. Detect liquidity sweep
   ↓
5. Detect ChoCH
   ↓
6. Detect FVG / Order Block
   ↓
7. Calculate Entry / Stop / Target
   ↓
8. Validate:
   - regime allows entry
   - stop distance ≤ 5%
   - R/R ≥ 2
   - price is not extended from entry
   - symbol is not blocked by news
   - reconciliation is PASS
   ↓
9. Generate dry-run plan
   ↓
10. Send Telegram confirmation request
   ↓
11. Manual confirmation first
   ↓
12. Submit IBKR paper buy limit order
   ↓
13. After fill, place protective stop
   ↓
14. At Target 1, sell 50%
   ↓
15. Move stop to breakeven
   ↓
16. Trail remaining shares with 10 EMA
```

---

# 14. Current System Status

Already working:

```text
✅ IBKR paper connection
✅ Telegram notification
✅ Pre-open news report
✅ SMC structure detection
✅ Real ticker scans using IBKR candles
✅ Chart output
✅ Dry-run rejection logic
```

Not yet implemented:

```text
❌ automatic order placement
❌ buy limit execution
❌ automatic protective stop placement
❌ Target 1 partial exit
❌ 10 EMA trailing exit
❌ live trading
```

---

# 15. Current Example Results

## AAPL

```text
Sweep: FOUND
ChoCH: FOUND
FVG: FOUND
Order Block: FOUND
Entry: 256.46
Stop: 245.46
Target 1: 288.62
R/R: 2.92
Result: REJECTED
Reasons:
- market_regime = unknown
- price extended 6.62% above entry
```

Interpretation:

The structure exists, but price has already moved too far from the repair zone. No chasing.

## TSLA

```text
Sweep: FOUND
ChoCH: FOUND
FVG: FOUND
Order Block: FOUND
Entry: 362.50
Stop: 339.62
Target 1: 498.83
R/R: 5.95
Result: REJECTED
Reasons:
- market_regime = unknown
- stop distance 6.31% > 5%
- price extended 3.10% above entry
```

Interpretation:

The structure exists, but risk is too wide and target logic may be too optimistic.

---

# 16. Key Principle

This system is not trying to predict the market.

It waits for:

```text
liquidity sweep
→ structural reversal confirmation
→ repair zone
→ controlled risk
→ realistic target
```

Only then can a trade be considered.

In one sentence:

> The strategy does not chase breakouts. It waits for price to sweep liquidity, confirm structural reversal, return to a repair zone, and offer a defined-risk entry toward nearby buy-side liquidity.

---

# 17. Next Required Improvement

Before any paper execution, we must complete:

```text
Prompt 6 — SMC Visual Audit Repair + Target Logic Fix
```

Main goals:

```text
- better chart labels
- clearer Sweep / ChoCH / FVG / OB annotations
- improved Target 1 logic
- nearest realistic buy-side liquidity target
- no distant target inflation
```

Execution should remain disabled until this is fixed and validated.
