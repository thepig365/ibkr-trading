# Dynamic watchlist builder

`bot/watchlist_builder.py` produces a daily *research* watchlist that
feeds the SMC scanner. It never places orders; every persisted file
carries `execution_allowed: false` and `research_only: true`.

## Static vs dynamic

| Mode | Source | Use when |
| --- | --- | --- |
| `static` | ``static_core`` + ``equities`` in ``config/watchlist.yaml`` | You want a small, hand-picked set; fastest to iterate on. |
| `dynamic` | Daily ranked merge of volume / relative-volume / volatility buckets + ``static_core`` | You want breadth: most-traded names of the day *plus* your hand-picked core. |

`default_source` in `config/watchlist.yaml` selects the one `scan-smc-watchlist` uses when `--source` is omitted.

## Metrics

For every seed symbol the builder pulls recent daily bars and computes:

| Field | How |
| --- | --- |
| `latest_price` | Most recent bar close. |
| `avg_20d_volume` | Mean of the last 20 bar volumes. |
| `avg_20d_dollar_volume` | `avg_20d_volume * latest_price`. |
| `current_volume` | Caller-supplied if available, else the latest bar's recorded volume. |
| `current_dollar_volume` | `current_volume * latest_price`. |
| `relative_volume` | `current_volume / avg_20d_volume`. |
| `atr_pct` | Classic Wilder ATR(20) divided by the latest close, in %. |
| `realized_vol_20d` | Annualised std-dev of 20 daily log-returns, in %. |
| `volume_activity` | `strong_activity` (≥2.0), `elevated_activity` (≥1.5), `normal_activity` (else), `unknown`. |

Anything that cannot be computed is recorded under `missing_fields` so
the operator sees *why* a row is thin, and so downstream logic can
degrade gracefully without crashing.

## Buckets

The builder selects the top *N* (default 30 per bucket) from four
categories, then merges and deduplicates:

1. **High current dollar volume** — ranked by `current_dollar_volume`.
2. **High relative volume** — names with `relative_volume ≥ 1.5`, ranked by the same.
3. **High average dollar volume** — ranked by `avg_20d_dollar_volume`.
4. **High beta / high volatility proxy** — admitted if `beta ≥ high_beta_threshold`, or `atr_pct ≥ high_atr_pct_threshold`, or `realized_vol_20d ≥ high_realized_vol_20d_threshold`.

`static_core` symbols are always added and are never dropped by the
`max_symbols` cap.

## Liquidity filters

After merging, each row is checked and the first failed filter stamps
`blocked=true` with a `block_reason`:

1. Symbol present in `blocked_symbols` (news / policy).
2. Symbol present in the leveraged-ETF blocklist (`TQQQ`, `SQQQ`, `SOXL`, …).
3. Looks like an OTC suffix (heuristic; extend as needed).
4. `price < min_price`.
5. `avg_20d_dollar_volume < min_avg_20d_dollar_volume`.
6. `current_dollar_volume < min_current_dollar_volume` (only applied when we have `current_volume`).

Blocked rows are *kept* in the JSON for auditability but removed from
the SMC scan.

## Ranking

```
rank_score_volume =
    0.45 * normalized_current_dollar_volume
  + 0.35 * normalized_relative_volume
  + 0.20 * normalized_avg_20d_dollar_volume
```

If either `current_dollar_volume` or `relative_volume` is missing we
fall back to:

```
rank_score_volume =
    0.70 * normalized_avg_20d_dollar_volume
  + 0.30 * normalized_atr_pct_or_realized_vol
```

Normalisation uses the batch maximum so weights stay comparable
between rebuilds.

## Output schema

Saved to `data/watchlists/YYYY-MM-DD-dynamic-watchlist.json`:

```json
{
  "date": "2025-04-24",
  "source": "ibkr",
  "symbols": [
    {
      "symbol": "NVDA",
      "reason": ["static_core", "high_current_dollar_volume", "high_relative_volume", "high_volatility_proxy"],
      "latest_price": 123.45,
      "current_volume": 50000000,
      "current_dollar_volume": 6172500000.0,
      "avg_20d_volume": 40000000,
      "avg_20d_dollar_volume": 4938000000.0,
      "relative_volume": 1.25,
      "volume_activity": "normal_activity",
      "volume_rank_score": 0.94,
      "beta": null,
      "atr_pct": 3.8,
      "realized_vol_20d": 48.3,
      "blocked": false,
      "block_reason": null,
      "missing_fields": ["beta"]
    }
  ],
  "missing_data": ["beta"],
  "research_only": true,
  "execution_allowed": false
}
```

## Config

`config/watchlist.yaml`:

```yaml
default_source: dynamic

static_core:
  - SPY
  - QQQ
  - AAPL
  # ...

dynamic:
  enabled: true
  max_symbols: 50
  min_price: 10
  min_avg_20d_dollar_volume: 20000000
  min_current_dollar_volume: 10000000
  min_relative_volume_for_relvol_bucket: 1.5
  strong_relative_volume: 2.0
  high_beta_threshold: 1.5
  high_atr_pct_threshold: 4.0
  high_realized_vol_20d_threshold: 50.0
  top_current_dollar_volume_count: 30
  top_relative_volume_count: 30
  top_avg_dollar_volume_count: 30
  top_high_volatility_count: 30
  exclude_leveraged_etfs: true
  exclude_otc: true
  exclude_blocked_symbols: true
  seed_universe:
    - SPY
    - QQQ
    # ... any symbols to scan
```

## CLI

Build today's list and print the table (no trading):

```bash
python -m bot.cli build-watchlist --ibkr --limit 50
```

Scan using today's dynamic list (rebuilds if missing):

```bash
python -m bot.cli scan-smc-watchlist --source dynamic --timeframe daily \
    --ibkr --chart --limit 20 --telegram
```

Scan using the static list:

```bash
python -m bot.cli scan-smc-watchlist --source static --timeframe daily \
    --ibkr --chart --limit 20 --telegram
```

## Why dynamic is research-only

High volume, high relative volume and high ATR% all signal **attention**
— not quality. The dynamic list is deliberately *widening* the aperture
of what the bot looks at; it does nothing about whether the setup is
tradeable. Every symbol on it must still pass:

* market regime gate (`bot.market_regime`);
* SMC structure (`bot.strategy_engine`);
* no-chasing rule, structural-stop rule, R/R floor;
* news blocklist (`pre-open-news`);
* reconciliation gate (`bot.reconciliation`).

Until those are all green, `execution_allowed` stays `false` and the
broker layer refuses to accept orders. High volume does not mean trade
approval.
