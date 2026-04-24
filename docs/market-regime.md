# Market regime

The bot refuses to enable new-position execution when it cannot tell
which macro regime the market is in. This module is the source of
truth for that decision.

## Why regime gating is required

The project aims to add execution in a future milestone. Every hard
safety layer we already have (`block_live_trading=true`, reconcile
gate, asset-class allow-list, no-chasing rule, SMC structure
requirements, R/R floor) assumes we know the regime. When the regime
is unknown, the safest default is: **block new entries, keep research
running, prove data is healthy before loosening anything**.

## Why VIX / VIX3M may be unavailable in IBKR

Paper accounts frequently do not have a market-data subscription for
CBOE index quotes. TWS returns "Error 162 — Historical Market Data
Service error message" and `ib_async` bubbles the error up. The bot
logs the event at debug level, records the missing field under
`market_data.missing_fields`, and falls back to a trend-only
classification rather than crashing.

## Fallback hierarchy

`bot/market_regime.py::evaluate_regime` applies this ladder, in order
of preference:

1. **Full data (VIX + SPY/200MA + QQQ/200MA)** — the original rule set
   from the spec, using VIX as the primary volatility anchor.
2. **VIX missing, trend OK** — deterministic fallback:
   * `SPY < 200MA` or `QQQ < 200MA` → `risk_off`
   * otherwise → `neutral`
3. **Only SPY has a 200MA** — same logic but with `confidence=low`
   and SPY providing the only vote.
4. **Nothing usable** — `market_regime=unknown`,
   `confidence=low`, `new_positions_allowed=false`,
   `research_scans_allowed=false`.

## Confidence levels

| Confidence | What it means |
| --- | --- |
| `high` | VIX + VIX3M + SPY/200MA + QQQ/200MA all present. |
| `medium` | VIX present with only SPY *or* only QQQ trend data, OR no VIX but both SPY/QQQ trends present. |
| `low` | One or zero trend references; default for the unknown fallback. |

Research scans run under `medium` by default (config knob
`allow_medium_confidence_for_research`). Execution additionally
requires VIX (`require_vix_for_execution`) and the SPY 200MA
(`require_spy_200ma_for_execution`) — both default to `true`.

## Output schema

`evaluate_regime(inputs, cfg)` returns:

```json
{
  "market_regime": "risk_on | neutral | elevated_vol | risk_off | crisis | unknown",
  "regime_confidence": "high | medium | low",
  "new_positions_allowed": false,
  "research_scans_allowed": true,
  "reason": "VIX/VIX3M unavailable; using SPY/QQQ trend fallback; missing market data: VIX, VIX3M",
  "market_data": {
    "spy_close": 503.12,
    "spy_200ma": 489.30,
    "spy_above_200ma": true,
    "qqq_close": 445.60,
    "qqq_200ma": 430.20,
    "qqq_above_200ma": true,
    "vix": null,
    "vix3m": null,
    "vix_vix3m_ratio": null,
    "missing_fields": ["VIX", "VIX3M"]
  }
}
```

## Config

`config/settings.yaml`:

```yaml
market_regime:
  allow_medium_confidence_for_research: true
  allow_medium_confidence_for_new_positions: false
  require_vix_for_execution: true
  require_spy_200ma_for_execution: true
  require_qqq_200ma_for_execution: false
```

These knobs tighten or loosen the research/execution floor. They
cannot enable execution — that remains off globally until the broker
path is explicitly unlocked by a separate milestone.

## CLI

```bash
python -m bot.cli market-regime --ibkr
```

Prints the label, confidence, SPY/QQQ trend state, VIX/VIX3M if
available, `missing_fields`, and whether research scans and new
positions are allowed. Snapshots are saved under
`data/market_regime/YYYY-MM-DD.json`.

### How `scan-smc` / `scan-smc-watchlist` consume the snapshot

Both scan commands read the most recent snapshot in this order:

1. `data/market_regime/*.json` (written by `market-regime --ibkr`).
2. `data/pre_open_news/*.json` as a fallback.
3. Literal `neutral` if neither exists — the CLI also prints a
   notice so the reviewer knows a real evaluation has not been run.

Whatever label is resolved is passed into the strategy evaluator so
symbols do **not** get the `market_regime=unknown blocks new setups`
rejection when the snapshot said `neutral`. The batch-summary JSON
also carries `market_regime`, `regime_confidence`,
`regime_missing_fields`, `research_scans_allowed`, and
`new_positions_allowed` as top-level fields so downstream tooling
can audit what the scanner saw.

## Research vs execution

* **Research** (`scan-smc`, `scan-smc-watchlist`, `pre-open-news`,
  `build-watchlist`) runs as long as the regime is not `unknown`.
  The SMC pipeline always sets `execution_allowed=false` regardless
  of the regime output.
* **Execution** is blocked unconditionally in the current
  milestone. Even a clean `risk_on` + `confidence=high` result does
  *not* flip the broker safety layer. Any future execution code must
  also respect the `new_positions_allowed` flag.

## Why missing regime data blocks execution

If the only signal we have is "SPY > 200MA", we cannot distinguish a
grinding bull market from the early phase of a sharp unwind. The
term structure (VIX/VIX3M) is the indicator that separates those
two worlds in real time. Without it, we intentionally stay in a
"research only" posture.
