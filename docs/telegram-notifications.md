# Telegram notifications

The bot can push warnings and status events to a Telegram chat. The
notification adapter is **fail-safe**: if credentials are missing or
Telegram is unreachable, the bot writes the message to
`memory/DAILY-SUMMARY.md` and continues running.

## 1. Create a bot with @BotFather

1. In Telegram, open a chat with
   [**@BotFather**](https://t.me/BotFather).
2. Send `/newbot`.
3. Give it a display name (e.g. `My IBKR Bot`) and a username ending
   in `bot` (e.g. `my_ibkr_bot`).
4. BotFather replies with an HTTP API token that looks like
   `1234567890:ABCDEF_ghij-klmno_pqrstuvwxyz01234`. **Copy it** - this
   is your `TELEGRAM_BOT_TOKEN`. Treat it like a password.

### Privacy hardening (recommended)

In the BotFather chat, run:

```
/mybots → <your bot> → Bot Settings → Group Privacy → Turn ON
```

This prevents the bot from reading group messages that are not
directed at it. The foundation milestone only **sends** messages, so
leaving Privacy Mode on is strictly safer.

## 2. Get your chat ID

### Option A: private chat with the bot

1. Open your bot's chat (search its username in Telegram).
2. Send it any message, e.g. `hi`.
3. In a browser, visit
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
   Look for `"chat":{"id":123456789,...}`. That number is your
   `TELEGRAM_CHAT_ID`.

### Option B: group chat

1. Add the bot to the group.
2. In the group, send a message that starts with the bot's handle
   (e.g. `@my_ibkr_bot hi`).
3. Visit the same `getUpdates` URL. Group IDs are negative numbers,
   e.g. `-1001234567890`. Use the full value including the minus sign.

## 3. Add credentials

In the project root, copy the template and fill in the values:

```bash
cp .env.example .env
```

Edit `.env`:

```
TELEGRAM_BOT_TOKEN=1234567890:ABCDEF_ghij-klmno_pqrstuvwxyz01234
TELEGRAM_CHAT_ID=123456789
```

`.env` is git-ignored - the real token must **never** be committed.

## 4. Test the connection

```bash
python -m bot.cli test-telegram
```

Expected outcomes:

| Situation | Exit code | Side effect |
|---|---|---|
| Credentials valid, Telegram accepts payload | `0` | Message arrives in the chat |
| Credentials missing / empty | `4` | Message appended to `memory/DAILY-SUMMARY.md` |
| Credentials set but API rejects (e.g. wrong chat id, bot not started) | `4` | Message appended to `memory/DAILY-SUMMARY.md` |

> `test-telegram` **does not connect to IBKR** and never places
> orders, so it is safe to run at any time.

You can also pass a custom body:

```bash
python -m bot.cli test-telegram --text "hello from the bot"
```

## 5. What the bot will send

Notifications are produced via `notify_event`:

```python
notify_event(
    event_type="reconciliation.failed",
    title="Reconciliation Failed",
    body="positions_without_stops: ['AAPL']\n...",
    severity="warning",
)
```

The message is formatted as HTML with a bold title and a severity
prefix (`[INFO]`, `[WARN]`, or `[URGENT]`) so operators can triage at
a glance.

## 6. Privacy mode (default: ON)

With `notifications.telegram.privacy_mode: true` in
`config/settings.yaml`, outgoing messages have the following
redactions applied:

| Field | Example before | Example after |
|---|---|---|
| Account number (`DU…` / `DF…`) | `DU1234567` | `DU***67` |
| Dollar amounts | `$102,345.67` | `$***` |
| `NetLiquidation: 102345.67` etc. | bare value | `NetLiquidation=***` |
| Telegram bot token accidentally leaked | `123456:ABC…` | `***` |
| `api_key=…`, `token=…`, `secret=…` | any value | `api_key=***` |

**Keep `privacy_mode: true` until you have audited every notification
site.** Turning it off only affects Telegram - structured SQLite and
JSONL logs in `data/` still contain the exact values, regardless of
this flag.

## 7. Disabling Telegram

Set `notifications.telegram.enabled: false` in
`config/settings.yaml`. The adapter will quietly append every message
to `memory/DAILY-SUMMARY.md` without attempting the network call.

## 8. Parse mode

`notifications.telegram.parse_mode` controls how the Telegram API
interprets the message body. Allowed values: `HTML`, `Markdown`,
`MarkdownV2`, or empty (plain text). The default is `HTML` because
`notify_event` can safely `html.escape()` caller-supplied text and
still keep the bold / italic tags it owns.

If you switch to Markdown, remember that `notify_event` will stop
escaping special characters - sanitize your own input.
