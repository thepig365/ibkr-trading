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

## Supported commands

| Command       | Maps to CLI                                                                                            | Chinese reply                             |
| ------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------- |
| `/help`       | _(in-process)_                                                                                         | Lists all supported commands              |
| `/news`       | `pre-open-news`                                                                                        | Full Chinese pre-open / intraday briefing |
| `/regime`     | `market-regime --ibkr`                                                                                 | Market regime + confidence + fallback     |
| `/watchlist`  | `build-watchlist --ibkr --limit 50` + `export-tws-watchlist --latest --telegram`                       | Dynamic watchlist + TWS CSV/TXT exported  |
| `/smc`        | `scan-smc-watchlist --source dynamic --timeframe daily --ibkr --chart --limit 20 --telegram`           | SMC research scan completed               |
| `/review`     | `smc-review-queue --telegram --markdown --top 10 --include-charts`                                     | SMC human-review queue ready              |
| `/opening`    | `market-regime → build-watchlist → export-tws-watchlist → scan-smc-watchlist → smc-review-queue`       | Opening review completed                  |
| `/status`     | _(inspects on-disk state)_                                                                             | Last reports + scheduler status           |

Every reply includes the literal line `execution_allowed=false`.

## Safety rules

The dispatcher runs a unified safety check **before** touching the CLI.
Any message matching these case-insensitive patterns is rejected:

```
buy / sell / trade / order / execute / enable trading / live /
place order / close position / short / options / long
```

The rejection reply is:

> 该 Telegram bot 当前只允许研究报告和人工复核，不允许下单、平仓、自动交易或任何 live execution。execution_allowed=false。

Unknown commands receive:

> 未知指令。请输入 /help 查看支持列表。

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

Start polling in the foreground:

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
