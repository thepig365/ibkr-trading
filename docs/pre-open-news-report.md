# Pre-Open Major News Report

Workflow name: **`pre_open_news`**.

## Purpose

Produce a deterministic, machine-readable **risk-posture briefing**
one hour before the US stock market opens. The report is
**informational only**: it never places orders, never modifies broker
state, and never bypasses the existing safety layer. Its job is to
tell the operator (and, later, the risk engine) which symbols the
bot should **not** consider for new entries today.

> **Do not trade from LLM output alone.** Perplexity is a research
> source; every risk decision is gated by deterministic rules in
> `bot/news_report.py` and, downstream, the risk engine and broker
> facade.

## Command

```bash
python -m bot.cli pre-open-news            # normal run
python -m bot.cli pre-open-news --dry-run  # no writes, no Telegram
python -m bot.cli --verbose pre-open-news  # also show third-party logs
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Report generated (and saved unless `--dry-run`) |
| `5` | Orchestrator raised; details written to `bot_events` table |

The command **never** returns a non-zero code merely because the
report says "no new entries". Use the JSON output to drive downstream
decisions.

## Scheduling

The canonical schedule is **08:30 America/New_York** on US trading
weekdays. `bot/scheduler.py::build_scheduler` registers a
`CronTrigger(day_of_week="mon-fri", hour=8, minute=30,
timezone="America/New_York")` job (`id="pre_open_news"`). The
foundation milestone does not start the scheduler automatically; the
CLI is the primary entry point.

Timezone handling never hard-codes Melbourne or any other local
timezone - the cron trigger carries `America/New_York` explicitly.

## Output files

* `memory/NEWS-REPORT.md` - append-only markdown log, human-friendly.
* `data/pre_open_news/YYYY-MM-DD.json` - machine-readable structured
  report, one file per trading day.

JSON shape (stable across runs):

```json
{
  "date": "YYYY-MM-DD",
  "run_time_new_york": "08:30",
  "market_regime": "risk_on | neutral | elevated_vol | risk_off | crisis | unknown",
  "trade_allowed": true,
  "new_positions_allowed": true,
  "research_available": true,
  "ibkr_news_available": true,
  "external_research_available": true,
  "blocked_symbols": [],
  "manual_review_required": [],
  "market_data": {
    "spy_above_200ma": true,
    "qqq_above_200ma": true,
    "vix": 16.2,
    "vix3m": 19.4,
    "vix_vix3m_ratio": 0.835,
    "missing_fields": []
  },
  "major_news":     [{"headline": "...", "source": "...", "symbols": ["..."], "severity": "high", "category": "major"}],
  "analyst_ratings":[{"headline": "...", "source": "...", "symbols": ["..."], "severity": "low",  "category": "analyst"}],
  "earnings_news":  [{"headline": "...", "source": "...", "symbols": ["..."], "severity": "medium", "category": "earnings"}],
  "macro_news":     [{"headline": "...", "source": "...", "symbols": [],      "severity": "high", "category": "macro"}],
  "macro_events":   [{"event": "CPI release", "time_new_york": "08:30", "severity": "high", "market_relevance": "broad"}],
  "holdings_risk": [],
  "watchlist_catalysts": [],
  "bot_instruction": ""
}
```

Each item carries the standard fields used by the risk rules:
`headline`, `source`, `symbols`, `asset_classes`, `impact`,
`severity ∈ {low, medium, high}`, `confidence`, `summary`, `category`.

`market_data.missing_fields` is the canonical record of which inputs
came back `None` (e.g. `["VIX", "VIX3M"]` when the paper account has
no historical-vol subscription). The Telegram digest mirrors that
list.

`trade_allowed` governs *existing* positions (they always remain
manageable in the foundation milestone). `new_positions_allowed` is
the gate the risk engine will read in future milestones.

## Data sources

### IBKR state (required)

The orchestrator reuses the existing read-only `IBKRClient`:

* `get_positions()` - holdings to analyse individually.
* `get_open_orders()` - reconciliation context.
* Account mode is read from `settings.account.mode`; **no sensitive
  account values** (NetLiquidation, TotalCash, BuyingPower) are read
  or shipped to Telegram.

If the IBKR connection fails, `new_positions_allowed` is forced to
`false` and `market_regime` is set to `unknown`.

### IBKR news (optional)

When the account has a news subscription (`reqNewsProviders` returns
≥1 provider code) the bot pulls up to
`pre_open_news.ibkr_news.max_headlines_per_symbol` headlines via
`reqHistoricalNews` for each holding and watchlist symbol. Without a
subscription the call returns an empty list and
`ibkr_news_available` becomes `false`; the report still runs.

### Perplexity (external fallback)

With `PERPLEXITY_API_KEY` set, the bot calls
`https://api.perplexity.ai/chat/completions` with a strict JSON
schema prompt (see `bot/research.py::build_prompt`). The client
defends against:

* Missing key - `is_configured == False` short-circuits.
* Network errors - caught; returns an empty research bundle.
* Non-JSON content - tolerated; returns an empty bundle with
  `raw_text` preserved for debugging.

When `external_research_available` is `false` the report blocks new
entries and the operator is notified.

### Market-data inputs for the regime classifier

`bot/market_regime.py` consumes VIX, VIX3M (optional), SPY/QQQ spot
and the matching 200-day simple moving averages. The orchestrator
fetches these via `IBKRClient.get_latest_close` and
`get_simple_moving_average` using the same read-only socket. Errors
(no subscription, contract not found, etc.) degrade silently:

* The IBKR error 162 stack traces are filtered out of the CLI by
  default - run with `--verbose` if you actually need to see them.
* Each missing field is recorded in `market_data.missing_fields` and
  surfaced on the Telegram digest.

## Market regime rules

**With VIX present**, evaluated in order (first match wins):

1. VIX ≥ 30 → **crisis**
2. VIX ≥ 20 → **elevated_vol**
3. VIX / VIX3M ≥ 1.0 → **risk_off** (term-structure inversion)
4. SPY < 200MA OR QQQ < 200MA → **risk_off**
5. VIX < 15 and SPY > 200MA → **risk_on**
6. else → **neutral**

**With VIX missing** (trend-only fallback):

* SPY < 200MA OR QQQ < 200MA → **risk_off**
* otherwise → **neutral** (`risk_on` is never returned without VIX)

If neither SPY-trend nor QQQ-trend is available the regime is
**unknown** and `new_positions_allowed` is forced to `false`.

## Risk rules

`new_positions_allowed` is set to `false` whenever **any** of:

* `market_regime ∈ {risk_off, crisis, unknown}`
* `external_research_available == false`
* `ibkr_connected == false`
* any high-severity macro event is within `minutes_after_open_block`
  minutes (default 30) of the US open (09:30 ET)

`blocked_symbols` is filled when a symbol is mentioned in a news item
that matches any of:

* trading halt / halted
* SEC or DOJ investigation / subpoena
* guidance cut / withdrawn / slashed
* bankruptcy / Chapter 11 / liquidity crisis / going concern
* any high-severity item with `impact ∈ {negative, mixed}`

`manual_review_required` is the union of:

* every `blocked_symbols` entry
* holdings or watchlist items flagged by Perplexity with
  `severity == "high"`
* symbols with earnings today
* symbols whose holdings-risk `recommendation` contains
  `reduce / exit / hedge / close`

Existing positions are **not** auto-liquidated by the report. Only the
operator (today) or the risk engine (future milestone) may act.

## Telegram format

The digest is sent via `notify_event(event_type="pre_open_news",
severity=info|warning|urgent)`. Severity maps as follows:

| Condition | Severity |
|---|---|
| regime == crisis | **urgent** |
| any blocked symbol OR new entries blocked | **warning** |
| otherwise | **info** |

Fields included:

* Market regime
* New positions allowed (yes/no)
* `Missing market data: VIX, VIX3M, ...` (only if any field is missing)
* `External research unavailable; using IBKR headlines only.` when
  Perplexity is not configured
* Blocked symbols (up to `telegram.max_blocked_items`)
* Manual-review symbols (up to `telegram.max_manual_review_items`)
* Top **3** major news items, **high-severity preferred** (when at
  least one high-severity item exists, low-severity items are
  hidden from the digest)
* `Earnings news: N items in full report.` (count only)
* `Analyst ratings: N updates detected, see full report.` (count only -
  individual analyst headlines never appear in the Telegram body so
  they cannot dominate the message)
* Bot instruction

The full breakdown - per-headline analyst ratings, every cleaned
IBKR headline by category, and so on - lives in the JSON file and
the Markdown log.

**Privacy.** The Telegram adapter always runs outgoing text through
`_redact` when `privacy_mode` is on (default). That means any of the
following leaking into the digest are automatically replaced:

* Account numbers (`DU…`, `DF…`)
* Exact NetLiquidation / TotalCash / BuyingPower values
* Dollar amounts
* API keys / tokens / secrets

Fallback: if Telegram credentials are missing or the API rejects the
message, the full digest is appended to `memory/DAILY-SUMMARY.md` and
the CLI exits `0` with a yellow note.

## Why this report does not trade

* No order-placement path is reachable from `news_report.py`. It never
  imports `Broker.place_order`, `IBKRClient._ib.placeOrder`, or
  anything equivalent.
* The existing safety invariants (`trading.enabled = false`,
  `account.block_live_trading = true`) remain in force. See
  [`docs/safety-rules.md`](safety-rules.md).
* Reconciliation is untouched. If it currently fails (for example
  because an AAPL position has no recognised stop order), the bot
  still refuses to open new entries. The pre-open report only adds
  extra reasons to say "no" - it never adds a reason to say "yes".
* LLM responses are *inputs* to deterministic rules. The rules can
  only move posture in the defensive direction.

## IBKR news subscription limitations

`reqHistoricalNews` requires that the account is subscribed to at
least one news provider (Reuters, Benzinga, Briefing.com, etc.).
Paper accounts typically have a limited subset. When no providers are
returned:

* `ibkr_news_available` is `false`.
* The report continues on Perplexity-only research.
* Operators see a soft note in `memory/NEWS-REPORT.md`.

To add providers: TWS → Account Management → News → Subscriptions.

## Perplexity fallback

Create an API key at <https://www.perplexity.ai/> and add:

```
PERPLEXITY_API_KEY=pplx-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

to `.env`. Without this key, the bot still produces a report, but
`external_research_available` is `false` and new entries are
blocked - this is deliberate.

## News pipeline (cleaning, dedup, categorisation, severity)

`bot/news_filters.py` contains the deterministic helpers that run on
every headline before it reaches the JSON, Markdown or Telegram
output:

* `clean_ibkr_headline(raw)` strips leading IBKR metadata blocks
  (`{A:800015:L:en:K:n/a:C:0.97...}`), leading exclamation marks,
  provider tags (`[BRFG]`, `[BRFUPDN]`, `[DJN]`, ...) and trailing
  symbol runs.
* `dedupe_headlines` collapses duplicate cleaned headlines, merging
  the symbol lists and keeping the highest severity.
* `categorize_headline` routes each headline into one of:
  * `analyst` - analyst ratings, price-target updates, broker desks
  * `earnings` - earnings beats / misses / guidance / quarterly
    results
  * `macro` - Fed / CPI / payrolls / tariffs / central banks /
    geopolitical
  * `major` - everything else (default bucket)
* `classify_severity` assigns `low`, `medium` or `high` using a
  fixed keyword table:
  * **high**: SEC / DOJ probes, fraud, bankruptcy, liquidity crisis,
    trading halt, guidance cut, earnings miss, CEO resigns
    unexpectedly, tariff shock, Fed surprise.
  * **medium**: AI-infrastructure deal, acquisition, antitrust,
    large premarket move, downgrade by major bank.
  * **low**: routine analyst reiteration, minor article, price
    target tweaks.

LLM payloads keep their declared severity; only when it is missing
does `classify_severity` fill it in.

## Configuration knobs

See `config/news.yaml`:

| Key | Default | Purpose |
|---|---|---|
| `pre_open_news.schedule_time_new_york` | `"08:30"` | Cron hour:minute in NY timezone |
| `pre_open_news.timezone` | `America/New_York` | Cron timezone |
| `pre_open_news.minutes_after_open_block` | `30` | High-severity macro window |
| `pre_open_news.perplexity.model` | `sonar` | LLM model |
| `pre_open_news.perplexity.timeout_seconds` | `30` | HTTP timeout |
| `pre_open_news.ibkr_news.max_headlines_per_symbol` | `5` | Per-symbol cap |
| `pre_open_news.telegram.max_news_items` | `3` | Top-N major-news lines on Telegram |

## Safety summary

| Invariant | Enforced by |
|---|---|
| No order placement from the report | `news_report.py` imports nothing that can place orders |
| LLM output cannot unblock trades | Risk rules only tighten, never relax |
| No live-account access | `account.block_live_trading=true` in `settings.yaml` |
| No SMC / strategy logic | Strategy module still absent |
| Reconciliation still blocks new trades | `risk_engine.py` unchanged |

Any change that weakens one of these invariants requires an explicit
code review and a matching test.
