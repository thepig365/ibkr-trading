# News report

Rolling append-only record of pre-open major-news briefings. Each
entry is written by ``python -m bot.cli pre-open-news`` (or the
scheduled ``pre_open_news`` job at 08:30 America/New_York on US
trading weekdays).

Structured copies of each report are saved under
`data/pre_open_news/YYYY-MM-DD.json`. See
[`docs/pre-open-news-report.md`](../docs/pre-open-news-report.md) for
the schema and risk rules.

---
