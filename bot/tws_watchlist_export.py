"""TWS watchlist export (Prompt 9.3).

Given a saved :class:`~bot.watchlist_builder.DynamicWatchlist` JSON
file under ``data/watchlists/YYYY-MM-DD-dynamic-watchlist.json``, this
module produces TWS-importable artefacts next to it:

* ``YYYY-MM-DD-tws-watchlist.csv`` — richly-typed CSV with contract
  metadata (``Symbol, SecType, Exchange, Currency, PrimaryExchange``)
  and the research metrics that came out of the builder.
* ``YYYY-MM-DD-tws-symbols.txt`` — one symbol per line for operators
  who prefer to paste into a new TWS watchlist.
* ``latest-tws-watchlist.csv`` and ``latest-tws-symbols.txt`` — a
  convenience pointer that always reflects the most recently exported
  day.

Hard safety rules (identical to every other module in this project):

* NEVER imports :mod:`bot.broker`.
* NEVER calls ``Broker.place_order``.
* NEVER enables execution. All artefacts carry
  ``execution_allowed=false`` / ``research_only=true``.
* NEVER mutates the IBKR account; the optional ``--validate`` path
  only calls ``qualifyContracts`` which is read-only.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import AppConfig
from .watchlist_builder import (
    DynamicWatchlist,
    WatchlistCandidate,
    load_dynamic_watchlist,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Columns & exchange inference
# ---------------------------------------------------------------------------
TWS_CSV_COLUMNS: tuple[str, ...] = (
    "Symbol",
    "SecType",
    "Exchange",
    "Currency",
    "PrimaryExchange",
    "Reason",
    "LatestPrice",
    "CurrentDollarVolume",
    "Avg20DDollarVolume",
    "RelativeVolume",
    "VolumeActivity",
    "ATRPercent",
    "RealizedVol20D",
    "RankScore",
    "ConId",
    "ContractValidated",
    "ValidationWarning",
)

DEFAULT_SEC_TYPE = "STK"
DEFAULT_EXCHANGE = "SMART"
DEFAULT_CURRENCY = "USD"

# Offline fallback mapping for the PrimaryExchange column. This is only
# used when IBKR contract validation is not requested or fails. It is
# intentionally conservative: symbols we do not know about get an
# empty string and a ``validation_warning`` so the operator can fill
# it in manually inside TWS if needed.
NASDAQ_SYMBOLS: frozenset[str] = frozenset({
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA",
    "AMD", "ARM", "PLTR", "SMCI", "MU", "ORCL", "CRWV", "CRM", "ADBE",
    "AVGO", "COST", "NFLX", "INTC", "QCOM", "PYPL", "ADI", "TXN",
    "SBUX", "MAR", "BKNG", "PEP", "CSCO",
})

NYSE_SYMBOLS: frozenset[str] = frozenset({
    "BRK.B", "BAC", "JPM", "WFC", "GS", "MS", "V", "MA", "UNH", "JNJ",
    "PG", "KO", "XOM", "CVX", "HD", "LOW", "WMT", "DIS", "NKE", "MCD",
    "CAT", "BA", "GE", "IBM",
})

ETF_ARCA_SYMBOLS: frozenset[str] = frozenset({
    "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "SCHD", "XLF", "XLK",
    "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLC", "XLRE",
    "EFA", "EEM", "TLT", "IEF", "SHY", "HYG", "LQD", "GLD", "SLV",
    "USO", "UNG", "ARKK", "ARKG", "SMH", "SOXX",
})

NYSE_MKT_ETF_SYMBOLS: frozenset[str] = frozenset({
    # NYSE listed ETFs. Most single-ticker ETFs on IBKR resolve via
    # ARCA; we keep this separate in case a symbol is ever moved.
})

TSM_LIKE_NASDAQ = frozenset({"TSM", "NIO", "BIDU", "PDD", "JD", "BABA"})


def infer_primary_exchange(symbol: str) -> tuple[str, str]:
    """Guess the PrimaryExchange for a symbol without an IBKR connection.

    Returns ``(primary_exchange, warning)``. ``primary_exchange`` may
    be an empty string when we cannot guess safely; ``warning`` is a
    human-readable hint that should be surfaced in the CSV so the
    operator can fill the field in manually inside TWS.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return "", "empty_symbol"
    if sym in NASDAQ_SYMBOLS or sym in TSM_LIKE_NASDAQ:
        return "NASDAQ", ""
    if sym in ETF_ARCA_SYMBOLS:
        return "ARCA", ""
    if sym in NYSE_SYMBOLS:
        return "NYSE", ""
    return "", "primary_exchange_unknown_without_ibkr_validation"


# ---------------------------------------------------------------------------
# Contract validation (optional, read-only)
# ---------------------------------------------------------------------------
@dataclass
class ValidatedContract:
    """Outcome of contract validation for one symbol."""

    symbol: str
    con_id: int | None = None
    primary_exchange: str = ""
    contract_validated: bool = False
    validation_warning: str = ""

    def to_csv_fields(self) -> dict[str, Any]:
        return {
            "PrimaryExchange": self.primary_exchange or "",
            "ConId": self.con_id if self.con_id else "",
            "ContractValidated": "true" if self.contract_validated else "false",
            "ValidationWarning": self.validation_warning or "",
        }


def _validate_single_contract(client: Any, symbol: str) -> ValidatedContract:
    """Call ``qualifyContracts`` once for ``symbol`` and map the result.

    ``client`` can be either a live :class:`bot.ibkr_client.IBKRClient`
    instance or a mock exposing a ``_ib.qualifyContracts`` method
    (tests use the mock). We never raise — every failure mode is
    reported via ``validation_warning`` so the CSV row is still
    produced.
    """
    sym = (symbol or "").strip().upper()
    fallback_primary, fallback_warning = infer_primary_exchange(sym)
    result = ValidatedContract(
        symbol=sym,
        primary_exchange=fallback_primary,
        validation_warning=fallback_warning,
    )
    if not sym:
        return result

    ib = getattr(client, "_ib", None)
    if ib is None:
        result.validation_warning = (
            result.validation_warning
            or "ibkr_client_not_connected"
        )
        return result

    try:
        try:
            from ib_async import Stock  # type: ignore
        except Exception:  # pragma: no cover - fallback for older envs
            from ib_insync import Stock  # type: ignore
        contract = Stock(sym, DEFAULT_EXCHANGE, DEFAULT_CURRENCY)
    except Exception as exc:  # noqa: BLE001
        result.validation_warning = f"stock_contract_build_failed: {exc!r}"[:200]
        return result

    try:
        qualified = ib.qualifyContracts(contract)
    except Exception as exc:  # noqa: BLE001
        result.validation_warning = f"qualifyContracts_failed: {exc!r}"[:200]
        return result

    if not qualified:
        # Keep the offline fallback but flag the failure explicitly.
        result.validation_warning = (
            "qualifyContracts_returned_empty; "
            + (result.validation_warning or "using_offline_primary_exchange")
        )[:300]
        return result

    q = qualified[0]
    con_id = int(getattr(q, "conId", 0) or 0) or None
    primary = str(getattr(q, "primaryExchange", "") or "")
    result.con_id = con_id
    if primary:
        result.primary_exchange = primary
    result.contract_validated = True
    result.validation_warning = ""
    return result


def validate_contracts(
    client: Any,
    symbols: Iterable[str],
) -> dict[str, ValidatedContract]:
    """Validate each symbol via ``qualifyContracts``; never raises."""
    out: dict[str, ValidatedContract] = {}
    for sym in symbols:
        out[sym.upper()] = _validate_single_contract(client, sym)
    return out


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------
def _candidate_validation(
    candidate: WatchlistCandidate,
    validations: dict[str, ValidatedContract] | None,
) -> ValidatedContract:
    sym = candidate.symbol.upper()
    if validations is not None and sym in validations:
        return validations[sym]
    primary, warn = infer_primary_exchange(sym)
    return ValidatedContract(
        symbol=sym,
        primary_exchange=primary,
        contract_validated=False,
        validation_warning=warn,
    )


def _fmt_number(value: float | int | None, *, ndigits: int = 4) -> str:
    if value is None:
        return ""
    try:
        return str(round(float(value), ndigits))
    except (TypeError, ValueError):
        return ""


def build_tws_rows(
    watchlist: DynamicWatchlist,
    validations: dict[str, ValidatedContract] | None = None,
    *,
    include_blocked: bool = False,
) -> list[dict[str, Any]]:
    """Produce the CSV row dicts for every kept candidate.

    Blocked rows are omitted by default so imports into TWS do not
    silently include symbols we explicitly filtered out. Pass
    ``include_blocked=True`` to retain them (they still get a
    ``Reason`` prefix that makes the block visible).
    """
    rows: list[dict[str, Any]] = []
    for c in watchlist.symbols:
        if c.blocked and not include_blocked:
            continue
        validation = _candidate_validation(c, validations)
        reason_tokens = sorted({r for r in (c.reason or []) if r})
        if c.blocked and c.block_reason:
            reason_tokens.append(f"BLOCKED:{c.block_reason}")
        rows.append(
            {
                "Symbol": c.symbol,
                "SecType": DEFAULT_SEC_TYPE,
                "Exchange": DEFAULT_EXCHANGE,
                "Currency": DEFAULT_CURRENCY,
                "PrimaryExchange": validation.primary_exchange,
                "Reason": "|".join(reason_tokens),
                "LatestPrice": _fmt_number(c.latest_price, ndigits=4),
                "CurrentDollarVolume": _fmt_number(c.current_dollar_volume, ndigits=2),
                "Avg20DDollarVolume": _fmt_number(c.avg_20d_dollar_volume, ndigits=2),
                "RelativeVolume": _fmt_number(c.relative_volume, ndigits=4),
                "VolumeActivity": c.volume_activity or "unknown",
                "ATRPercent": _fmt_number(c.atr_pct, ndigits=4),
                "RealizedVol20D": _fmt_number(c.realized_vol_20d, ndigits=4),
                "RankScore": _fmt_number(c.volume_rank_score, ndigits=6),
                "ConId": validation.con_id or "",
                "ContractValidated": (
                    "true" if validation.contract_validated else "false"
                ),
                "ValidationWarning": validation.validation_warning or "",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------
def _output_dir(cfg: AppConfig, directory: str = "data/watchlists") -> Path:
    out = cfg.absolute(directory)
    out.mkdir(parents=True, exist_ok=True)
    return out


def _write_csv_rows(path: Path, rows: Sequence[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=list(TWS_CSV_COLUMNS), extrasaction="ignore"
        )
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in TWS_CSV_COLUMNS})
    return path


def _write_symbols_txt(path: Path, rows: Sequence[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            sym = (r.get("Symbol") or "").strip()
            if sym:
                f.write(f"{sym}\n")
    return path


@dataclass
class ExportPaths:
    """Return value describing which files were written."""

    dated_csv: Path
    latest_csv: Path
    dated_txt: Path
    latest_txt: Path
    source_json: Path | None
    row_count: int
    validated_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "dated_csv": str(self.dated_csv),
            "latest_csv": str(self.latest_csv),
            "dated_txt": str(self.dated_txt),
            "latest_txt": str(self.latest_txt),
            "source_json": str(self.source_json) if self.source_json else None,
            "row_count": self.row_count,
            "validated_count": self.validated_count,
            "execution_allowed": False,
            "research_only": True,
        }


def export_tws_watchlist(
    cfg: AppConfig,
    watchlist: DynamicWatchlist,
    *,
    validations: dict[str, ValidatedContract] | None = None,
    source_json: Path | None = None,
    directory: str = "data/watchlists",
    include_blocked: bool = False,
) -> ExportPaths:
    """Write the four TWS artefacts for ``watchlist`` and return paths.

    The dated artefacts (``<date>-tws-watchlist.csv`` and
    ``<date>-tws-symbols.txt``) are canonical; the ``latest-*`` files
    are overwritten every time so the scheduler / CLI can always
    point at the same stable path.
    """
    out = _output_dir(cfg, directory)
    date = watchlist.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = build_tws_rows(watchlist, validations, include_blocked=include_blocked)

    dated_csv = out / f"{date}-tws-watchlist.csv"
    latest_csv = out / "latest-tws-watchlist.csv"
    dated_txt = out / f"{date}-tws-symbols.txt"
    latest_txt = out / "latest-tws-symbols.txt"

    _write_csv_rows(dated_csv, rows)
    _write_csv_rows(latest_csv, rows)
    _write_symbols_txt(dated_txt, rows)
    _write_symbols_txt(latest_txt, rows)

    validated_count = sum(
        1
        for r in rows
        if str(r.get("ContractValidated", "false")).lower() == "true"
    )
    return ExportPaths(
        dated_csv=dated_csv,
        latest_csv=latest_csv,
        dated_txt=dated_txt,
        latest_txt=latest_txt,
        source_json=source_json,
        row_count=len(rows),
        validated_count=validated_count,
    )


# ---------------------------------------------------------------------------
# High-level helpers used by the CLI
# ---------------------------------------------------------------------------
def _find_latest_dynamic_path(
    cfg: AppConfig, directory: str = "data/watchlists"
) -> Path | None:
    """Return the most recently modified dynamic watchlist JSON."""
    out = cfg.absolute(directory)
    if not out.is_dir():
        return None
    candidates = sorted(out.glob("*-dynamic-watchlist.json"))
    return candidates[-1] if candidates else None


def load_watchlist_by_date_or_latest(
    cfg: AppConfig,
    *,
    date: str | None = None,
    latest: bool = False,
    directory: str = "data/watchlists",
) -> tuple[DynamicWatchlist | None, Path | None]:
    """Return ``(DynamicWatchlist, source_json_path)`` or ``(None, None)``."""
    if latest and not date:
        path = _find_latest_dynamic_path(cfg, directory)
        if path is None:
            return None, None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, path
        date_from_payload = str(payload.get("date") or "")
        if not date_from_payload:
            # Fall back to filename prefix before ``-dynamic-watchlist.json``.
            date_from_payload = path.name.split("-dynamic-watchlist")[0]
        wl = load_dynamic_watchlist(
            cfg, date=date_from_payload, directory=directory
        )
        return wl, path

    if date:
        wl = load_dynamic_watchlist(cfg, date=date, directory=directory)
        if wl is None:
            return None, None
        path = cfg.absolute(directory) / f"{date}-dynamic-watchlist.json"
        return wl, path if path.exists() else None

    return None, None


__all__ = [
    "DEFAULT_CURRENCY",
    "DEFAULT_EXCHANGE",
    "DEFAULT_SEC_TYPE",
    "ExportPaths",
    "TWS_CSV_COLUMNS",
    "ValidatedContract",
    "build_tws_rows",
    "export_tws_watchlist",
    "infer_primary_exchange",
    "load_watchlist_by_date_or_latest",
    "validate_contracts",
]
