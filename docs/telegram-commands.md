# Telegram Command Interface

The bot accepts a small, safe set of Telegram commands and delivers
full Chinese reports. **All commands are report-only.** The interface
hard-blocks anything that looks like a trade instruction (buy, sell,
trade, order, execute, short, options, close position, enable trading,
live, place order) with a Chinese safety reply. `execution_allowed`
remains `false` and `research_only` remains `true` regardless of what
the user types.

See also:

- Part A (Chinese full news report): `bot/news_report_zh.py`,
  `config/news.yaml -> news_report:` block.
- Scheduler integration (08:30 / 09:45): [`scheduler.md`](scheduler.md).
- Safety invariants: [`safety-rules.md`](safety-rules.md).

---

## Push vs command listener

- **Outbound push** (engine → Telegram) works as soon as `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHAT_ID` are set: the bot **sends** messages.
- **Inbound commands** (`/status`, `/help`, …) require something to call Telegram’s
  `getUpdates` API. Use `python -m bot.cli telegram-command-listener` (or install
  the launchd job in `scripts/install_telegram_command_listener_launchd.sh`) so a
  background process long-polls and replies. Offset is stored in
  `data/runtime/telegram_command_listener_state.json`.

## Supported commands

| Command       | Maps to CLI / logic                                                                                   | Chinese reply                             |
| ------------- | ----------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| `/help`       | _(in-process)_                                                                                        | Lists core + research commands            |
| `/ping`       | _(in-process)_                                                                                        | `pong` + safety line                      |
| `/status`     | Read-only: `full_auto_paper_readiness` + launchd hints (no orders)                                    | Engine / TWS / budget / readiness         |
| `/news`       | Read-only: news monitor state on disk (does **not** run `pre-open-news` / live fetches)             | Provider count, last scored, state path   |
| `/reports`    | Read-only: latest paper / backtest / edge paths under `data/reports` / `data/edge_profiles`        | Short path summary; open `/reports` in UI |
| `/regime`     | `market-regime --ibkr`                                                                                | Market regime + confidence + fallback     |
| `/watchlist`  | `build-watchlist --ibkr --limit 50` + `export-tws-watchlist --latest --telegram`                      | Dynamic watchlist + TWS CSV/TXT exported  |
| `/smc`        | `scan-smc-watchlist --source dynamic --timeframe daily --ibkr --chart --limit 20 --telegram`          | SMC research scan completed               |
| `/review`     | `smc-review-queue --telegram --markdown --top 10 --include-charts`                                    | SMC human-review queue ready              |
| `/opening`    | `market-regime → build-watchlist → export-tws-watchlist → scan-smc-watchlist → smc-review-queue`      | Opening review completed                  |

`/stop` and `/kill` **do not** flip the on-disk kill switch from Telegram; use the
Strategy Lab UI or a local terminal. `/resume` still removes `data/KILL_SWITCH` if
you create it via UI/CLI.

Unknown commands: short help; repeated same unknown text within ~120s is deduped
to a one-line message.

**Not supported in Telegram (rejected):** `/starttrading`, `/trade`, `/buy`, `/sell`,
`/live`, `/market` (and the usual free-text safety patterns).

## Safety rules

The dispatcher runs a unified safety check **before** touching the CLI.
Any message matching these case-insensitive patterns is rejected:

```
buy / sell / trade / order / execute / enable trading / live /
place order / close position / short / options / long
```

The rejection reply is:

> 该 Telegram bot 当前只允许研究报告和人工复核，不允许下单、平仓、自动交易或任何 live execution。execution_allowed=false。

Unknown commands receive a short help line (deduped if the same text repeats within
~120 seconds).

Messages from a chat whose id is not in
`telegram.command_interface.allowed_chat_ids` (or the resolved
`${TELEGRAM_CHAT_ID}` env expansion) are **ignored**. The rejection is
still logged with a redacted chat id and a status of `unauthorized`.

## Configuration

`config/telegram.yaml`:

```yaml
telegram:
  command_interface:
    enabled: true
    allowed_chat_ids:
      - "${TELEGRAM_CHAT_ID}"     # or a literal chat id string
    language: zh
    polling_interval_seconds: 5
    reports_only: true
    execution_allowed: false       # ignored if set to true - hard-forced
    max_message_length: 3500
    log_dir: data/telegram_commands
```

Additional environment variables (from `.env`):

- `TELEGRAM_BOT_TOKEN` - used both for outbound messages and for
  long-polling `getUpdates`.
- `TELEGRAM_CHAT_ID` - expanded into `allowed_chat_ids` when the YAML
  lists `"${TELEGRAM_CHAT_ID}"`.

## CLI entry points

**Preferred listener** (persists `getUpdates` offset, optional JSON lines):

```bash
python -m bot.cli telegram-command-listener
python -m bot.cli telegram-command-listener --once --json
python -m bot.cli telegram-command-listener --dry-run --json   # no network
```

Foreground helper script: `bash scripts/run_telegram_command_listener.sh`

**macOS background (no Terminal):**

```bash
bash scripts/install_telegram_command_listener_launchd.sh
bash scripts/status_telegram_command_listener_launchd.sh
bash scripts/uninstall_telegram_command_listener_launchd.sh
```

Legacy alias (same underlying poll + state; fewer CLI flags):

```bash
python -m bot.cli telegram-listen
# Ctrl+C to stop. Add --iterations N for test harnesses.
```

Run a single command without polling (useful for acceptance tests and
for verifying the safety gate):

```bash
python -m bot.cli telegram-test-command --command /news
python -m bot.cli telegram-test-command --command /review
python -m bot.cli telegram-test-command --command "buy AAPL"
```

Exit codes:

- `0` - success (command ran and was dispatched to the CLI).
- `1` - the interface was disabled, the CLI failed, or no chat id was
  resolvable.
- `2` - the message was safety-rejected or came from an unauthorized
  chat (this is expected when testing the safety path).

## Long replies

Telegram caps messages at 4096 characters. `deliver_reply` splits the
Chinese reply on line boundaries below the configured
`max_message_length` (default 3500) and prefixes each part with
`(Part i/N)`. No message is silently truncated.

## Logging

Every incoming message is appended to
`data/telegram_commands/YYYY-MM-DD.jsonl`:

```json
{
  "timestamp": "2026-04-24T13:42:11+00:00",
  "chat_id_redacted": "12***89",
  "command": "/news",
  "status": "success",
  "execution_allowed": false,
  "details": "exit_code=0"
}
```

The chat id is redacted to `first 2 + *** + last 2` characters so the
log is safe to commit or paste into an issue.

## Scheduler integration

The daily scheduler (`config/schedule.yaml`, see
[`scheduler.md`](scheduler.md)) delivers the same Chinese full report
at 08:30 America/New_York (`/news` equivalent) and a Chinese SMC review
queue at 09:45 (`/opening` minus `market-regime`). Telegram commands
can be used to manually re-run either workflow outside those slots.
