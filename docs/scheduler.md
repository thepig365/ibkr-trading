# Daily Telegram Scheduler (Prompt 9 Part B)

The scheduler runs the daily **report-only** workflow on New York
market time:

| Time (America/New_York) | Job                 | Purpose                                                                 |
| ----------------------- | ------------------- | ----------------------------------------------------------------------- |
| 08:30 Mon–Fri           | `pre_open_news`     | 1 hour before the US open — major news + regime digest.                 |
| 09:45 Mon–Fri           | `opening_smc_review`| 15 minutes after the open — market regime → watchlist → TWS export → scan → review. |

Both jobs write Telegram digests, JSON reports, and markdown memos.
Neither places orders. The scheduler's hard safety contract:

* `reports_only: true`
* `execution_allowed: false` (hard-forced, ignoring any YAML override)
* `broker.place_order` is never called
* any sequence step whose first token is `place-order`, `trade`,
  `enable-trading`, `modify-order`, or `cancel-order` is
  **rejected** by `ensure_report_only` before it runs

Logs for each run go to `data/scheduler/YYYY-MM-DD.jsonl`.

## Schedule configuration

`config/schedule.yaml`:

```yaml
schedule:
  timezone: America/New_York
  enabled: true

  jobs:
    pre_open_news:
      enabled: true
      time: "08:30"
      days: ["mon", "tue", "wed", "thu", "fri"]
      command: "pre-open-news"
      telegram: true

    opening_smc_review:
      enabled: true
      time: "09:45"
      days: ["mon", "tue", "wed", "thu", "fri"]
      sequence:
        - "market-regime --ibkr"
        - "build-watchlist --ibkr --limit 50"
        - "export-tws-watchlist --latest --telegram"
        - "scan-smc-watchlist --source dynamic --timeframe daily --ibkr --chart --limit 20 --telegram"
        - "smc-review-queue --telegram --markdown --top 10 --include-charts"

  safety:
    reports_only: true
    execution_allowed: false
    skip_if_tws_unavailable: true
    send_error_to_telegram: true
```

The 09:45 sequence is run **in order**. If a step crashes:

* `skip_if_tws_unavailable: true` and the step uses `--ibkr` — the
  scheduler skips that step, records it as `skipped`, sends a
  Telegram warning, and **continues** with the remaining steps.
* Otherwise — the scheduler stops the sequence, records `failed`,
  and sends a Telegram warning.

Telegram failures always fall back to `memory/DAILY-SUMMARY.md`; the
scheduler never crashes on missing credentials.

## CLI

```bash
python -m bot.cli schedule-status
python -m bot.cli run-pre-open-report
python -m bot.cli run-opening-review
python -m bot.cli run-scheduler
```

* `schedule-status` prints the timezone, the enabled jobs, their
  cron times, and the next run time computed against NY local time.
* `run-pre-open-report` — run the 08:30 workflow once.
* `run-opening-review` — run the 09:45 sequence once (still gated by
  `ensure_report_only`).
* `run-scheduler` — block the foreground with a
  `BlockingScheduler`. Use Ctrl-C to stop.

## Running with cron / launchd

For reliability, point an external scheduler at the one-shot
commands instead of leaving `run-scheduler` in the foreground.

### cron (Linux/macOS)

```cron
# Mon-Fri 08:30 America/New_York (adjust for host timezone / DST)
30 8 * * 1-5 cd /path/to/project && .venv/bin/python -m bot.cli run-pre-open-report
45 9 * * 1-5 cd /path/to/project && .venv/bin/python -m bot.cli run-opening-review
```

### launchd (macOS)

Use `StartCalendarInterval` with `Weekday` + `Hour` + `Minute` and
wrap each command in a shell script so environment variables from
`.env` are loaded.

### GitHub Actions / server schedulers

The same two commands are safe to drop into any scheduler. Each
one-shot exits after writing its log entry to
`data/scheduler/YYYY-MM-DD.jsonl`.

## Telegram content

**08:30** — `【盘前重大市场新闻报告】YYYY-MM-DD` — the **Chinese full
report** (Prompt 9.2) built by the `pre-open-news` command. The
Telegram body ships the seven sections defined in
`bot/news_report_zh.py`: market regime + confidence + fallback, major
news headlines (bilingual), earnings, analyst ratings grouped by
symbol, manual-review symbols, blocked symbols, and the research-only
reminder (`execution_allowed=false`, `research_only=true`). If the
message exceeds `max_message_length` (default 3500), the body is split
into `(Part i/N)` chunks — never silently truncated. When run outside
pre-open hours, the title switches to `【即时重大市场新闻报告】
YYYY-MM-DD HH:mm`.

**09:45** — the SMC watchlist digest (from `scan-smc-watchlist
--telegram`) *and* the review queue digest (from `smc-review-queue
--telegram`). Both speak Chinese by default (Prompt 9.2) and respect
the same `max_message_length` cap. The review queue digest names
ICT/SMC **tradeable candidates** explicitly or says `No ICT/SMC
tradeable candidates found. Research only. No orders placed.`

## Manual triggers via Telegram

The Telegram command interface (see
[`telegram-commands.md`](telegram-commands.md)) exposes the same
workflows on demand:

- `/news` — Chinese full pre-open / intraday briefing (same content as
  the 08:30 job).
- `/opening` — full opening review sequence (same four steps the 09:45
  job runs).
- `/review` — just the SMC review queue refresh.

Commands that would place or modify an order are hard-rejected with
`execution_allowed=false`.

Privacy: both jobs respect the existing redactor
(`bot/notifications/telegram.py`). Account numbers, dollar values,
bot tokens, and API keys never leave the process.

## Limitations (V0)

* Weekdays only (Mon-Fri). Exchange holidays are not yet skipped —
  tracked as TODO.
* No retry logic. A failed step is logged and either skipped (if
  `--ibkr` + `skip_if_tws_unavailable`) or aborts the sequence. Run
  the one-shot command manually to retry.
* Python-local scheduler only; production should use
  cron/launchd/CI.

## Safety summary

| Safeguard                              | Enforced in                        |
| -------------------------------------- | ---------------------------------- |
| `execution_allowed=False`              | `bot/daily_scheduler.py` log path  |
| `broker.place_order` never called      | Never imported                     |
| Unsafe commands (`place-order`, etc.)  | `ensure_report_only` raises        |
| Unknown commands                       | Warning logged, runs in report-only |
| TWS unavailable                        | Step skipped, Telegram warning     |
| Telegram unavailable                   | Fallback to `memory/DAILY-SUMMARY.md` |
| Sequence crash                         | Recorded, warning sent, no retry   |
