# TWS Watchlist Export (Prompt 9.3)

## Why this exists

The dynamic research watchlist lives in the bot's filesystem:

```text
data/watchlists/YYYY-MM-DD-dynamic-watchlist.json
```

That JSON is great for the scanner and for audit, but it is **not**
visible inside the **IBKR TWS Watchlist UI**. The bot deliberately
does **not** automate the TWS UI — TWS does not expose a safe,
documented Watchlist-import API that respects our
`execution_allowed=false` invariant, and scripting the UI would risk
crossing the read-only boundary.

Instead, the bot exports the watchlist to two TWS-friendly artefacts
that you import or paste manually:

| Artefact | Purpose |
| --- | --- |
| `YYYY-MM-DD-tws-watchlist.csv` | Rich, typed CSV for TWS's "Import Watchlist" dialog. |
| `latest-tws-watchlist.csv` | A stable alias always pointing at the most recent CSV. |
| `YYYY-MM-DD-tws-symbols.txt` | One symbol per line; copy/paste into a new TWS Watchlist. |
| `latest-tws-symbols.txt` | Stable alias for the TXT. |

> This is **watchlist export only**, not trading. The module never
> imports `bot.broker`, never calls `broker.place_order`, and never
> enables execution. The artefacts always carry
> `execution_allowed=false` / `research_only=true`.

## How to generate the files

The bot writes the export automatically at the end of
`build-watchlist`:

```bash
python -m bot.cli build-watchlist --ibkr --limit 50
```

You can also generate them on demand without rebuilding the dynamic
watchlist:

```bash
# Use today's latest dynamic watchlist
python -m bot.cli export-tws-watchlist --latest

# Pick a specific historical date
python -m bot.cli export-tws-watchlist --date 2026-04-24

# Also validate each contract via IBKR (read-only qualifyContracts)
python -m bot.cli export-tws-watchlist --latest --validate --ibkr
```

`--validate --ibkr` calls `qualifyContracts` only. That is a read-only
API that returns the IBKR `conId` and authoritative `primaryExchange`.
No orders are placed. When the IBKR connection is unavailable (TWS
offline, no market-data subscription, etc.), the exporter falls back
to the offline `PrimaryExchange` mapping and records
`ContractValidated=false` with a `ValidationWarning` on the row —
the file is always produced so you never lose an export because of
a transient outage.

### Opening-review integration

The 09:45 opening-review sequence (both the scheduler and the
Telegram `/opening` command) runs the export after building the
watchlist and before the SMC scan. The updated sequence:

1. `market-regime --ibkr`
2. `build-watchlist --ibkr --limit 50`
3. `export-tws-watchlist --latest --telegram`
4. `scan-smc-watchlist --source dynamic --timeframe daily --ibkr --chart --limit 20 --telegram`
5. `smc-review-queue --telegram --markdown --top 10 --include-charts`

Step 3 sends a Chinese Telegram message summarising the paths so the
operator knows exactly where to find the files:

```text
TWS 监视列表已导出（仅研究，不执行）：
- CSV：data/watchlists/latest-tws-watchlist.csv
- 符号列表：data/watchlists/latest-tws-symbols.txt
...
请在 TWS Watchlist 中导入 CSV，或复制 TXT symbols 到新的 Watchlist。
execution_allowed=false；research_only=true。
```

## CSV schema

```text
Symbol,SecType,Exchange,Currency,PrimaryExchange,Reason,
LatestPrice,CurrentDollarVolume,Avg20DDollarVolume,RelativeVolume,
VolumeActivity,ATRPercent,RealizedVol20D,RankScore,
ConId,ContractValidated,ValidationWarning
```

| Column | Meaning |
| --- | --- |
| `Symbol` | Ticker (uppercase). |
| `SecType` | Hard-coded `STK`. The bot only exports equities. |
| `Exchange` | Hard-coded `SMART`. TWS resolves routing at import time. |
| `Currency` | Hard-coded `USD`. |
| `PrimaryExchange` | From `qualifyContracts` when `--validate --ibkr`, else a conservative offline lookup (NASDAQ / NYSE / ARCA). Blank = unknown. |
| `Reason` | Pipe-joined bucket tags (`static_core`, `high_current_dollar_volume`, `high_relative_volume`, `high_avg_dollar_volume`, `high_volatility_proxy`). |
| `LatestPrice` | Most recent close the builder saw. |
| `CurrentDollarVolume` | Latest-session dollar volume proxy. |
| `Avg20DDollarVolume` | 20-day average dollar volume. |
| `RelativeVolume` | current / avg20d ratio. |
| `VolumeActivity` | `strong_activity` / `elevated_activity` / `normal_activity` / `unknown`. |
| `ATRPercent` | 20-day ATR as a percent of latest close. |
| `RealizedVol20D` | 20-day annualised realised vol (%). |
| `RankScore` | `[0, 1]` rank score the builder used for trimming. |
| `ConId` | IBKR contract id (only populated when `--validate --ibkr` succeeds). |
| `ContractValidated` | `true` when IBKR confirmed the symbol. |
| `ValidationWarning` | Non-empty when validation failed or the offline mapping was used. |

### Example row

```text
AAPL,STK,SMART,USD,NASDAQ,high_avg_dollar_volume|high_current_dollar_volume|static_core,
273.43,4262614290.31,5762269452.66,0.7397,normal_activity,
2.2949,25.8463,0.216817,
,false,primary_exchange_unknown_without_ibkr_validation
```

(Line breaks added for readability; the real CSV keeps each row on a
single line.)

## Symbol TXT schema

One symbol per line, nothing else:

```text
AAPL
MSFT
NVDA
TSLA
AMD
```

Blocked rows are excluded by default so a paste-in TWS watchlist does
not silently pick up symbols we filtered out.

## Opening the files

Open the CSV and TXT files directly from the repo:

```bash
open data/watchlists/latest-tws-watchlist.csv
open data/watchlists/latest-tws-symbols.txt
```

macOS opens the CSV in Numbers / Excel by default. Either works for
TWS imports; TWS does not read styling.

## Importing into TWS

### Option A — Import the CSV

1. In TWS, open **Watchlist → New Watchlist → Import**.
2. Choose `data/watchlists/latest-tws-watchlist.csv`.
3. TWS prompts for column mapping. The `Symbol`, `SecType`,
   `Exchange`, `Currency`, and `PrimaryExchange` columns map
   directly; the metrics columns are informational and TWS will
   ignore the ones it does not recognise.
4. Save the new watchlist.

### Option B — Paste the TXT

1. In TWS, open **Watchlist → New Watchlist**.
2. Open `data/watchlists/latest-tws-symbols.txt` in any text editor.
3. Copy all lines.
4. Paste into the TWS watchlist; TWS auto-resolves each symbol on
   `SMART` / `USD`.

If a symbol fails to resolve (rare; possible with new IPOs or thinly
traded names), check the corresponding CSV row's `ValidationWarning`.
Re-run the export with `--validate --ibkr` so the exporter can fetch
the authoritative `PrimaryExchange` for that symbol.

## Safety contract

* No orders are placed — the module never imports `bot.broker`.
* `--validate --ibkr` only calls `qualifyContracts` (read-only).
* All exported artefacts carry `execution_allowed=false` and
  `research_only=true` in the journal event (`tws_watchlist_export`).
* Export failures (missing JSON, bad CSV, IBKR offline) never take
  down `build-watchlist` — you always keep the dynamic watchlist JSON
  and the export is best-effort on top.
