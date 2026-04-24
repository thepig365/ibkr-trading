# SMC Review Queue

This file is appended by `python -m bot.cli smc-review-queue --markdown`.
Each run adds a dated block with the market regime header, a summary
table, a top-N review table, and per-category lists (pullback watch,
invalid risk rejects, structure watch, blocked).

Hard rules:

* `execution_allowed` = `false` on every entry and envelope.
* `research_only` = `true` on every entry and envelope.
* The queue never places orders. It is a **manual review aid only**.
* "Candidate for manual review" is the correct phrasing; the queue
  is **not** a trade signal.

See `docs/smc-review-queue.md` for details.

# SMC Review Queue — 2026-04-24

Market regime: neutral
Confidence: medium
Missing fields: VIX, VIX3M
New positions allowed: no
Execution allowed: no
Research only: yes

## Summary

| Category | Count |
|---|---:|
| READY_FOR_MANUAL_CHART_REVIEW | 0 |
| PULLBACK_WATCH | 1 |
| INVALID_RISK_REJECT | 6 |
| STRUCTURE_WATCH | 13 |
| BLOCKED_BY_REGIME_OR_NEWS | 0 |
| IGNORE_FOR_NOW | 0 |

## Top Review Items

| Symbol | Category | Score | Entry | Stop | T1 | R/R | Reason |
|---|---|---:|---:|---:|---:|---:|---|
| AAPL | PULLBACK_WATCH | 55 | 256.46 | 245.46 | 280.18 | 2.16 | AAPL: full SMC structure exists, R/R=2.16, stop=4.29% is within 5.00%, but price is 6.62% above entry (256.46). Do not chase. Review only if price pulls back toward 256.46. |
| AVGO | INVALID_RISK_REJECT | 15 | 330.65 | 306.65 | 403.00 | 3.01 | AVGO: full SMC structure exists, but structural stop is 7.26%, wider than the 5.00% limit. Reject unless structure tightens or a better entry forms. |
| TSLA | INVALID_RISK_REJECT | 15 | 362.50 | 339.62 | 408.62 | 2.02 | TSLA: full SMC structure exists, but structural stop is 6.31%, wider than the 5.00% limit. Reject unless structure tightens or a better entry forms. |
| ADBE | STRUCTURE_WATCH | 0 | - | - | - | - | ADBE: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup. |
| AMD | STRUCTURE_WATCH | 0 | - | - | - | - | AMD: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup. |
| AMZN | STRUCTURE_WATCH | 0 | - | - | - | - | AMZN: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup. |
| ARM | INVALID_RISK_REJECT | 0 | 127.20 | 111.21 | - | 0.00 | ARM: full SMC structure exists, but structural stop is 12.57%, wider than the 5.00% limit; R/R=0.00 below min 2.00 or no valid target. Reject unless structure tightens or a better entry forms. |
| CRM | STRUCTURE_WATCH | 0 | - | - | - | - | CRM: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup. |
| CRWV | STRUCTURE_WATCH | 0 | - | - | - | - | CRWV: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup. |
| GOOGL | STRUCTURE_WATCH | 0 | - | - | - | - | GOOGL: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup. |

## Pullback Watch

- AAPL (score=55) — entry 256.46, stop 245.46 (4.29%), T1 280.18, R/R 2.16: AAPL: full SMC structure exists, R/R=2.16, stop=4.29% is within 5.00%, but price is 6.62% above entry (256.46). Do not chase. Review only if price pulls back toward 256.46.

## Invalid Risk Rejects

- AVGO (score=15) — entry 330.65, stop 306.65 (7.26%), T1 403.00, R/R 3.01: AVGO: full SMC structure exists, but structural stop is 7.26%, wider than the 5.00% limit. Reject unless structure tightens or a better entry forms.
- TSLA (score=15) — entry 362.50, stop 339.62 (6.31%), T1 408.62, R/R 2.02: TSLA: full SMC structure exists, but structural stop is 6.31%, wider than the 5.00% limit. Reject unless structure tightens or a better entry forms.
- ARM (score=0) — entry 127.20, stop 111.21 (12.57%), T1 -, R/R 0.00: ARM: full SMC structure exists, but structural stop is 12.57%, wider than the 5.00% limit; R/R=0.00 below min 2.00 or no valid target. Reject unless structure tightens or a better entry forms.
- MU (score=0) — entry 437.74, stop 357.62 (18.30%), T1 -, R/R 0.00: MU: full SMC structure exists, but structural stop is 18.30%, wider than the 5.00% limit; R/R=0.00 below min 2.00 or no valid target. Reject unless structure tightens or a better entry forms.
- ORCL (score=0) — entry 161.56, stop 134.52 (16.74%), T1 -, R/R 0.00: ORCL: full SMC structure exists, but structural stop is 16.74%, wider than the 5.00% limit; R/R=0.00 below min 2.00 or no valid target. Reject unless structure tightens or a better entry forms.
- TSM (score=0) — entry 359.23, stop 322.26 (10.29%), T1 -, R/R 0.00: TSM: full SMC structure exists, but structural stop is 10.29%, wider than the 5.00% limit; R/R=0.00 below min 2.00 or no valid target. Reject unless structure tightens or a better entry forms.

## Structure Watch

- ADBE (score=0) — entry -, stop - (-), T1 -, R/R -: ADBE: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup.
- AMD (score=0) — entry -, stop - (-), T1 -, R/R -: AMD: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup.
- AMZN (score=0) — entry -, stop - (-), T1 -, R/R -: AMZN: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup.
- CRM (score=0) — entry -, stop - (-), T1 -, R/R -: CRM: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup.
- CRWV (score=0) — entry -, stop - (-), T1 -, R/R -: CRWV: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup.
- GOOGL (score=0) — entry -, stop - (-), T1 -, R/R -: GOOGL: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup.
- META (score=0) — entry -, stop - (-), T1 -, R/R -: META: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup.
- MSFT (score=0) — entry -, stop - (-), T1 -, R/R -: MSFT: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup.
- NVDA (score=0) — entry -, stop - (-), T1 -, R/R -: NVDA: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup.
- PLTR (score=0) — entry -, stop - (-), T1 -, R/R -: PLTR: liquidity sweep exists, but ChoCH has not confirmed, no FVG, no order block. Watch for structure completion; do not treat as a setup.

## Reminder

This is a research review queue only. It does not approve trades. No orders are placed.

