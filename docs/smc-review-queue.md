# SMC Review Queue (Prompt 9 Part A)

The review queue turns a `scan-smc-watchlist` batch summary into a
**human review queue**. It is strictly a *research* layer:
`execution_allowed` is hard-coded to `False`, `research_only` stays
`True`, and the module does not import `bot.broker`. See
`tests/test_review_queue.py` for the guardrails.

## Why a review queue?

The scanner already produces useful rows — `WATCH_NOW`, `NEAR_ENTRY`,
`TOO_EXTENDED`, `STRUCTURE_INCOMPLETE`, `INVALID_RISK`, `BLOCKED` —
but those are *detection* labels. A human reviewer needs a different
sort order:

1. Which setups are clean enough to pull up on a chart?
2. Which only need a pullback?
3. Which are already out of risk geometry and can be dismissed?
4. Which partial structures are worth monitoring?
5. Which are blocked outright by regime/news?

The review queue answers those five questions and assigns a separate
`review_priority_score` so the list can be sorted for the eye.

## Categories

| Category                         | Meaning                                                                                                                        |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `READY_FOR_MANUAL_CHART_REVIEW`  | Full SMC structure, R/R ≥ 2, stop ≤ 5%, extension ≤ 3%. **Candidate for manual review only**; not an execution approval.       |
| `PULLBACK_WATCH`                 | Full structure + acceptable R/R and stop, but price is too extended from entry. Wait for a pullback before reviewing again.    |
| `INVALID_RISK_REJECT`            | Full structure, but stop too wide OR R/R too low OR no valid target. Reject for now.                                           |
| `STRUCTURE_WATCH`                | Liquidity sweep exists but ChoCH / FVG / OB is missing. Watch for structure completion; do not treat as a setup.               |
| `BLOCKED_BY_REGIME_OR_NEWS`      | Scanner bucket `BLOCKED` or rejection reason mentions `market_regime=…`, halt, or a news block. Do not review until cleared.   |
| `IGNORE_FOR_NOW`                 | No sweep, no meaningful structure. Low-value noise row.                                                                        |

## `review_priority_score` vs `smc_quality_score`

`smc_quality_score` comes from the scanner and measures **how clean
the detection is**. `review_priority_score` is a **human-sort** hint:

* `+30` full structure (`-30` otherwise)
* `+15` R/R ≥ min
* `+15 / -25` stop within / over max
* `+15` extension within max, `+10` extra when near entry, `-20` if
  over max
* `+10 / -25` target valid / missing
* `+5 / -10` chart present / missing
* `+5` dynamic-watchlist high-volume / high-relative-volume hint
* `-50` market-regime block; `-50` news block

Score is clamped to `[0, 100]`. It **does not** approve trades and
does not flip `execution_allowed`.

## Tradeable candidates

The opening 09:45 Telegram digest must explicitly list any ICT/SMC
tradeable *candidates* (rows in `READY_FOR_MANUAL_CHART_REVIEW`). If
none exist, the digest says

> No ICT/SMC tradeable candidates found. Research only. No orders placed.

The wording is intentional: candidates are **for manual review**;
they are **not trade signals** and must still pass market regime,
news blocks, chart inspection, and risk checks before any future
paper execution.

## Output

### JSON (`data/review_queue/YYYY-MM-DD-smc-review-queue.json`)

```json
{
  "date": "YYYY-MM-DD",
  "source_summary": "...-watchlist-summary.json",
  "market_regime": "neutral",
  "regime_confidence": "medium",
  "regime_missing_fields": ["VIX", "VIX3M"],
  "new_positions_allowed": false,
  "research_scans_allowed": true,
  "execution_allowed": false,
  "research_only": true,
  "counts": {
    "READY_FOR_MANUAL_CHART_REVIEW": 0,
    "PULLBACK_WATCH": 0,
    "INVALID_RISK_REJECT": 0,
    "STRUCTURE_WATCH": 0,
    "BLOCKED_BY_REGIME_OR_NEWS": 0,
    "IGNORE_FOR_NOW": 0
  },
  "items": [ ... ]
}
```

### Markdown (`memory/SMC-REVIEW-QUEUE.md`)

Each run appends a block with the regime block, a summary table, a
top-N review table, and per-category lists for pullback / invalid /
structure / blocked items.

### Telegram digest

Sent when `--telegram` is passed. Includes regime, counts, tradeable
candidates (or the "none found" sentinel), pullback watch, invalid
risk rejects, and structure-watch names. Privacy redaction is
applied by the normal `send_telegram_message` path; dollar values,
account numbers, and API tokens never reach the chat.

## CLI

```bash
python -m bot.cli smc-review-queue --telegram --markdown --top 10 --include-charts
python -m bot.cli smc-review-queue --date 2026-04-24 --no-save
python -m bot.cli smc-review-queue --min-score 40
```

Options:

* `--date YYYY-MM-DD` — pick a specific day's summary. Defaults to the latest.
* `--top N` — how many rows to show in the markdown top-items table.
* `--include-charts` — show chart filenames (not full paths) in the console table.
* `--min-score N` — drop `IGNORE_FOR_NOW` items below this priority score.
* `--telegram` / `--markdown` — enable either output channel.
* `--no-save` — skip writing the JSON; useful for dry runs.

## Safety

* `execution_allowed` is `False` at every level.
* `research_only` is `True` at every level.
* The module does not import `bot.broker` and never calls
  `.place_order(...)`.
* "Trade signal" wording is forbidden by tests; the review queue
  uses "candidate for manual review".
* A missing summary returns exit code `6` and prints a clear error;
  it never crashes the CLI.
