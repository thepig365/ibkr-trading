"""Read-only Interactive Brokers client wrapper.

This module exposes a small, intentionally narrow surface around
`ib_async` (the maintained successor of `ib_insync`). Only read
operations are exposed. Order placement is NOT implemented here and
must never be added to this file - the safety layer in `broker.py`
must own any future write path.

Behaviour notes:
    * If `ib_async` (or `ib_insync`) is not installed, the module still
      imports cleanly. Calling `connect()` raises a clear error so unit
      tests that never connect can run without the dependency.
    * `connect()` refuses to connect when settings indicate a live
      account, regardless of which port the .env file points at.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from .config import AppConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

logger = logging.getLogger(__name__)

# Try ib_async first; fall back to ib_insync for older installs.
_IB_BACKEND: str | None = None
_IB = None
try:  # pragma: no cover - import side-effect
    from ib_async import IB  # type: ignore

    _IB = IB
    _IB_BACKEND = "ib_async"
except Exception:  # noqa: BLE001
    try:  # pragma: no cover - import side-effect
        from ib_insync import IB  # type: ignore

        _IB = IB
        _IB_BACKEND = "ib_insync"
    except Exception:  # noqa: BLE001
        _IB = None
        _IB_BACKEND = None


# Ports that IBKR documents as paper-trading endpoints.
PAPER_PORTS = {7497, 4002}

# ``ib_async.IB.RequestTimeout`` defaults to 0 = wait forever on blocking
# ``util.run`` calls. A finite cap prevents CLI commands (portfolio, reconcile,
# scans) from hanging indefinitely when TWS is slow; ``disconnect`` then runs.
# Not configurable via settings files (Prompt 13K.1) — override via env if needed.
_DEFAULT_BLOCKING_REQUEST_TIMEOUT = 60.0


def _blocking_request_timeout_sec() -> float:
    raw = (os.environ.get("IBKR_REQUEST_TIMEOUT") or "").strip()
    if not raw:
        return _DEFAULT_BLOCKING_REQUEST_TIMEOUT
    try:
        return max(5.0, min(600.0, float(raw)))
    except (TypeError, ValueError):
        return _DEFAULT_BLOCKING_REQUEST_TIMEOUT


@dataclass
class AccountSummary:
    account_id: str
    net_liquidation: float | None = None
    total_cash: float | None = None
    buying_power: float | None = None
    available_funds: float | None = None
    currency: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PositionRow:
    account: str
    symbol: str
    sec_type: str
    exchange: str
    currency: str
    position: float
    avg_cost: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OpenOrderRow:
    perm_id: int | None
    order_id: int | None
    account: str
    symbol: str
    sec_type: str
    action: str
    order_type: str
    total_quantity: float
    lmt_price: float | None
    aux_price: float | None
    tif: str | None
    status: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NewsHeadline:
    """A single headline, normalised across IBKR news providers."""

    symbol: str
    provider_code: str
    article_id: str
    headline: str
    time_utc: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionRow:
    exec_id: str
    perm_id: int | None
    order_id: int | None
    account: str
    symbol: str
    sec_type: str
    side: str
    shares: float
    price: float
    time: str | None
    exchange: str | None
    order_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IBKRClientError(RuntimeError):
    """Raised on configuration or connection problems."""


class LiveTradingBlocked(IBKRClientError):
    """Raised when the configuration would route us to a live account."""


class IBKRClient:
    """Thin, read-only wrapper around ib_async.IB.

    The wrapper deliberately exposes no order-placement methods. Any
    attempt to add one should be reviewed against docs/safety-rules.md.
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._ib: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def connect(
        self, timeout: float = 10.0, *, readonly: bool = True
    ) -> None:
        """Open a connection to TWS / IB Gateway.

        Refuses to connect if the configuration indicates a live account
        and `account.block_live_trading` is true (the default).

        Set ``readonly=False`` only for paper order submission paths
        (e.g. MTF bracket) — the default remains read-only for research.
        """
        self._enforce_paper_only()

        if _IB is None:
            raise IBKRClientError(
                "Neither ib_async nor ib_insync is installed. "
                "Run `pip install -r requirements.txt` before connecting."
            )

        ib = _IB()
        logger.info(
            "Connecting to IBKR via %s host=%s port=%s client_id=%s mode=%s",
            _IB_BACKEND,
            self.cfg.ibkr.host,
            self.cfg.ibkr.port,
            self.cfg.ibkr.client_id,
            self.cfg.ibkr.account_mode,
        )
        ib.connect(
            host=self.cfg.ibkr.host,
            port=self.cfg.ibkr.port,
            clientId=self.cfg.ibkr.client_id,
            timeout=timeout,
            readonly=readonly,
        )
        to = _blocking_request_timeout_sec()
        if to > 0 and hasattr(ib, "RequestTimeout"):
            ib.RequestTimeout = float(to)  # type: ignore[misc]
            logger.info(
                "ib_async/ib_insync RequestTimeout set to %ss (blocking util.run cap)",
                to,
            )
        self._ib = ib

    def disconnect(self) -> None:
        if self._ib is not None:
            try:
                self._ib.disconnect()
            finally:
                self._ib = None

    @property
    def is_connected(self) -> bool:
        return self._ib is not None and bool(getattr(self._ib, "isConnected", lambda: False)())

    @property
    def backend(self) -> str | None:
        return _IB_BACKEND

    def session_status_snapshot(self) -> dict[str, Any]:
        """Connection metadata after ``connect`` (read-only, no order traffic)."""
        self._require_connection()
        assert self._ib is not None
        ib = self._ib
        out: dict[str, Any] = {
            "connected": self.is_connected,
            "backend": _IB_BACKEND,
            "host": self.cfg.ibkr.host,
            "port": self.cfg.ibkr.port,
            "client_id": self.cfg.ibkr.client_id,
            "account_mode": self.cfg.ibkr.account_mode,
            "blocking_request_timeout_effective_sec": _blocking_request_timeout_sec(),
        }
        if hasattr(ib, "RequestTimeout"):
            out["ib_RequestTimeout"] = getattr(ib, "RequestTimeout", None)
        cl = getattr(ib, "client", None)
        if cl is not None:
            sv = getattr(cl, "serverVersion", None)
            try:
                if callable(sv):
                    out["server_version"] = int(sv())
                else:
                    out["server_version"] = sv
            except (TypeError, ValueError, OSError):
                out["server_version"] = None
        return out

    # ------------------------------------------------------------------
    # Read-only queries
    # ------------------------------------------------------------------
    def get_account_summary(self, account: str | None = None) -> list[AccountSummary]:
        self._require_connection()
        rows = self._ib.accountSummary(account or "")
        # Group by account.
        grouped: dict[str, AccountSummary] = {}
        for r in rows:
            acct = grouped.setdefault(
                r.account, AccountSummary(account_id=r.account, raw={})
            )
            acct.raw[r.tag] = {"value": r.value, "currency": r.currency}
            if acct.currency is None and r.currency:
                acct.currency = r.currency

            tag = r.tag
            try:
                val_f = float(r.value)
            except (TypeError, ValueError):
                continue
            if tag == "NetLiquidation":
                acct.net_liquidation = val_f
            elif tag == "TotalCashValue":
                acct.total_cash = val_f
            elif tag == "BuyingPower":
                acct.buying_power = val_f
            elif tag == "AvailableFunds":
                acct.available_funds = val_f
        return list(grouped.values())

    def get_positions(self) -> list[PositionRow]:
        self._require_connection()
        out: list[PositionRow] = []
        for p in self._ib.positions():
            c = p.contract
            out.append(
                PositionRow(
                    account=p.account,
                    symbol=getattr(c, "symbol", "") or "",
                    sec_type=getattr(c, "secType", "") or "",
                    exchange=getattr(c, "exchange", "") or "",
                    currency=getattr(c, "currency", "") or "",
                    position=float(p.position),
                    avg_cost=float(p.avgCost),
                )
            )
        return out

    def get_open_orders(self) -> list[OpenOrderRow]:
        self._require_connection()
        out: list[OpenOrderRow] = []
        # ib_async exposes openTrades(); each Trade has .contract, .order, .orderStatus
        trades = self._ib.openTrades() if hasattr(self._ib, "openTrades") else []
        for t in trades:
            c = t.contract
            o = t.order
            st = getattr(t, "orderStatus", None)
            out.append(
                OpenOrderRow(
                    perm_id=getattr(o, "permId", None),
                    order_id=getattr(o, "orderId", None),
                    account=getattr(o, "account", "") or "",
                    symbol=getattr(c, "symbol", "") or "",
                    sec_type=getattr(c, "secType", "") or "",
                    action=getattr(o, "action", "") or "",
                    order_type=getattr(o, "orderType", "") or "",
                    total_quantity=float(getattr(o, "totalQuantity", 0) or 0),
                    lmt_price=_as_optional_float(getattr(o, "lmtPrice", None)),
                    aux_price=_as_optional_float(getattr(o, "auxPrice", None)),
                    tif=getattr(o, "tif", None),
                    status=getattr(st, "status", None) if st is not None else None,
                )
            )
        return out

    # ------------------------------------------------------------------
    # Market data (read-only)
    # ------------------------------------------------------------------
    def _qualify(
        self, symbol: str, sec_type: str, exchange: str, currency: str
    ) -> Any | None:
        """Build and qualify a contract. Returns None if unavailable."""
        self._require_connection()
        try:
            from ib_async import Index, Stock  # type: ignore
        except Exception:  # pragma: no cover - ib_async required for live calls
            try:
                from ib_insync import Index, Stock  # type: ignore
            except Exception:
                return None
        if sec_type.upper() == "IND":
            contract = Index(symbol, exchange or "CBOE", currency or "USD")
        elif sec_type.upper() == "CASH":
            try:
                from ib_async import Contract  # type: ignore
            except Exception:  # pragma: no cover
                try:
                    from ib_insync import Contract  # type: ignore
                except Exception:
                    return None
            contract = Contract(
                symbol=str(symbol).upper(),
                secType="CASH",
                currency=str(currency or "").upper(),
                exchange=(exchange or "IDEALPRO").upper(),
            )
        else:
            contract = Stock(symbol, exchange or "SMART", currency or "USD")
        try:
            qualified = self._ib.qualifyContracts(contract)
        except Exception as exc:  # noqa: BLE001
            logger.debug("qualifyContracts failed for %s: %s", symbol, exc)
            return None
        return qualified[0] if qualified else None

    def get_daily_closes(
        self,
        symbol: str,
        sec_type: str = "STK",
        exchange: str = "SMART",
        currency: str = "USD",
        days: int = 300,
    ) -> list[float]:
        """Return the most recent daily closes, oldest first.

        Returns an empty list on any failure (no market-data subscription,
        unrecognised symbol, IBKR error, etc.).
        """
        contract = self._qualify(symbol, sec_type, exchange, currency)
        if contract is None:
            return []
        duration = f"{max(days, 1)} D"
        what = "MIDPOINT" if sec_type.upper() == "IND" else "TRADES"
        try:
            bars = self._ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting="1 day",
                whatToShow=what,
                useRTH=True,
                formatDate=1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("reqHistoricalData failed for %s: %s", symbol, exc)
            return []
        return [float(b.close) for b in (bars or []) if b.close is not None]

    def get_latest_close(
        self,
        symbol: str,
        sec_type: str = "STK",
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> float | None:
        closes = self.get_daily_closes(symbol, sec_type, exchange, currency, days=5)
        return closes[-1] if closes else None

    def get_simple_moving_average(
        self,
        symbol: str,
        window: int = 200,
        sec_type: str = "STK",
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> float | None:
        closes = self.get_daily_closes(
            symbol, sec_type, exchange, currency, days=max(window + 20, 220)
        )
        if len(closes) < window:
            return None
        window_closes = closes[-window:]
        return sum(window_closes) / float(window)

    def get_daily_bars(
        self,
        symbol: str,
        sec_type: str = "STK",
        exchange: str = "SMART",
        currency: str = "USD",
        days: int = 300,
        duration_str: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return daily OHLCV bars, oldest first.

        When ``duration_str`` is set (e.g. ``"1 Y"`` from
        :class:`bot.smc_timeframes.TimeframeSpec`), it is passed to
        ``reqHistoricalData`` as ``durationStr``; otherwise
        ``f"{days} D"`` is used for backward compatibility.

        Each item is a plain dict with keys
        ``timestamp / open / high / low / close / volume``. Returns an
        empty list on any failure (no subscription, unrecognised
        symbol, IBKR error). Read-only; never modifies broker state.
        """
        contract = self._qualify(symbol, sec_type, exchange, currency)
        if contract is None:
            return []
        if duration_str:
            duration = duration_str
        else:
            duration = f"{max(days, 1)} D"
        what = "MIDPOINT" if sec_type.upper() == "IND" else "TRADES"
        try:
            bars = self._ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting="1 day",
                whatToShow=what,
                useRTH=True,
                formatDate=1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("reqHistoricalData (bars) failed for %s: %s", symbol, exc)
            return []
        out: list[dict[str, Any]] = []
        for b in bars or []:
            ts = getattr(b, "date", None)
            if ts is None:
                continue
            out.append(
                {
                    "timestamp": str(ts),
                    "open": float(getattr(b, "open", 0.0) or 0.0),
                    "high": float(getattr(b, "high", 0.0) or 0.0),
                    "low": float(getattr(b, "low", 0.0) or 0.0),
                    "close": float(getattr(b, "close", 0.0) or 0.0),
                    "volume": float(getattr(b, "volume", 0.0) or 0.0),
                }
            )
        return out

    def get_intraday_bars(
        self,
        symbol: str,
        *,
        duration: str = "20 D",
        bar_size: str = "30 mins",
        what_to_show: str = "TRADES",
        use_rth: bool = True,
        sec_type: str = "STK",
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> list[dict[str, Any]]:
        """Return intraday OHLCV bars, oldest first.

        Thin, read-only wrapper around IBKR's historical-data request
        with the knobs the SMC 30min scanner needs. Returns an empty
        list on any failure (no subscription, unrecognised symbol,
        IBKR error). Never places orders, never mutates broker state.

        Parameters mirror IBKR's ``reqHistoricalData``:

        * ``duration`` — e.g. ``"20 D"``.
        * ``bar_size`` — e.g. ``"30 mins"``.
        * ``what_to_show`` — ``"TRADES"`` by default.
        * ``use_rth`` — regular trading hours only (True for 30min).
        """
        contract = self._qualify(symbol, sec_type, exchange, currency)
        if contract is None:
            return []
        try:
            bars = self._ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "reqHistoricalData (intraday %s) failed for %s: %s",
                bar_size, symbol, exc,
            )
            return []
        out: list[dict[str, Any]] = []
        for b in bars or []:
            ts = getattr(b, "date", None)
            if ts is None:
                continue
            out.append(
                {
                    "timestamp": str(ts),
                    "open": float(getattr(b, "open", 0.0) or 0.0),
                    "high": float(getattr(b, "high", 0.0) or 0.0),
                    "low": float(getattr(b, "low", 0.0) or 0.0),
                    "close": float(getattr(b, "close", 0.0) or 0.0),
                    "volume": float(getattr(b, "volume", 0.0) or 0.0),
                }
            )
        return out

    @staticmethod
    def _aggregate_1h_bars_to_4h(bars_1h: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Roll four consecutive 1H bars into one synthetic 4H bar (RTH list order)."""
        if len(bars_1h) < 4:
            return []
        out: list[dict[str, Any]] = []
        i = 0
        n = len(bars_1h)
        while i + 4 <= n:
            chunk = bars_1h[i : i + 4]
            out.append(
                {
                    "timestamp": str(chunk[0].get("timestamp", "")),
                    "open": float(chunk[0].get("open", 0.0) or 0.0),
                    "high": max(float(x.get("high", 0.0) or 0.0) for x in chunk),
                    "low": min(float(x.get("low", 0.0) or 0.0) for x in chunk),
                    "close": float(chunk[-1].get("close", 0.0) or 0.0),
                    "volume": sum(float(x.get("volume", 0.0) or 0.0) for x in chunk),
                }
            )
            i += 4
        return out

    def get_4h_bars_with_fallback(
        self,
        symbol: str,
        spec: Any,
        *,
        sec_type: str = "STK",
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Fetch 4H bars, or aggregate from 1H if native ``4 hours`` is empty/short.

        Returns ``(rows, warnings)``. Never places orders. Read-only.
        """
        warnings: list[str] = []
        min_need = int(getattr(spec, "min_bars", 80) or 80)
        native = self.get_intraday_bars(
            symbol,
            duration=str(getattr(spec, "duration", "60 D")),
            bar_size=str(getattr(spec, "bar_size", "4 hours")),
            what_to_show=str(getattr(spec, "what_to_show", "TRADES")),
            use_rth=bool(getattr(spec, "use_rth", True)),
            sec_type=sec_type,
            exchange=exchange,
            currency=currency,
        )
        mx = int(getattr(spec, "max_bars", 300) or 300)
        if len(native) >= min_need:
            out = native[-mx:] if len(native) > mx else list(native)
            return out, warnings
        h1 = self.get_intraday_bars(
            symbol,
            duration=str(getattr(spec, "duration", "60 D")),
            bar_size="1 hour",
            what_to_show=str(getattr(spec, "what_to_show", "TRADES")),
            use_rth=bool(getattr(spec, "use_rth", True)),
            sec_type=sec_type,
            exchange=exchange,
            currency=currency,
        )
        if not h1 and not native:
            warnings.append("4h: no native 4h data and no 1h data for fallback")
            return [], warnings
        agg = self._aggregate_1h_bars_to_4h(h1) if h1 else []
        if not agg and native:
            out = native[-mx:] if len(native) > mx else list(native)
            return out, warnings
        if agg:
            warnings.append(
                "4h bars aggregated from 1h due to IBKR barSize limitation "
                "or insufficient native 4h history"
            )
        use = agg or native
        if len(use) > mx:
            use = use[-mx:]
        return use, warnings

    def get_bars_for_timeframe(
        self,
        symbol: str,
        spec: Any,
        *,
        sec_type: str = "STK",
        exchange: str = "SMART",
        currency: str = "USD",
        out_warnings: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return bars for the :class:`bot.smc_timeframes.TimeframeSpec`.

        Dispatches to :meth:`get_daily_bars` or
        :meth:`get_intraday_bars` based on ``spec.is_intraday``.
        For ``spec.name == '4h'`` uses :meth:`get_4h_bars_with_fallback`.
        Optional ``out_warnings`` receives 4h fallback notices.
        Returns an empty list on any failure and logs a debug line.
        This path is read-only; execution remains disabled globally.
        """
        if getattr(spec, "name", None) == "4h":
            rows, w = self.get_4h_bars_with_fallback(
                symbol, spec, sec_type=sec_type, exchange=exchange, currency=currency
            )
            if out_warnings is not None:
                out_warnings.extend(w)
            return rows
        if getattr(spec, "is_intraday", False):
            out = self.get_intraday_bars(
                symbol,
                duration=getattr(spec, "duration", "20 D"),
                bar_size=getattr(spec, "bar_size", "30 mins"),
                what_to_show=getattr(spec, "what_to_show", "TRADES"),
                use_rth=bool(getattr(spec, "use_rth", True)),
                sec_type=sec_type,
                exchange=exchange,
                currency=currency,
            )
            mx = int(getattr(spec, "max_bars", 300) or 300)
            if len(out) > mx:
                out = out[-mx:]
            return out
        # Daily: use smc_timeframes duration (e.g. 1 Y), then cap rows.
        daily_rows = self.get_daily_bars(
            symbol,
            sec_type=sec_type,
            exchange=exchange,
            currency=currency,
            days=1,  # ignored when duration_str is set
            duration_str=str(getattr(spec, "duration", None) or "1 Y"),
        )
        mx = int(getattr(spec, "max_bars", 400) or 400)
        if len(daily_rows) > mx:
            daily_rows = daily_rows[-mx:]
        return daily_rows

    # ------------------------------------------------------------------
    # News (read-only)
    # ------------------------------------------------------------------
    def get_news_providers(self) -> list[str]:
        """Return subscribed IBKR news provider codes."""
        self._require_connection()
        try:
            providers = self._ib.reqNewsProviders()
        except Exception as exc:  # noqa: BLE001
            logger.info("reqNewsProviders unavailable: %s", exc)
            return []
        return [getattr(p, "code", "") for p in (providers or []) if getattr(p, "code", None)]

    def get_historical_news(
        self,
        symbol: str,
        provider_codes: list[str] | None = None,
        max_results: int = 5,
        sec_type: str = "STK",
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> list[NewsHeadline]:
        """Return the most recent headlines for ``symbol``.

        Silently returns an empty list when the account has no news
        subscription, when the symbol cannot be qualified, or when the
        API raises. Callers must not treat an empty list as an error.
        """
        contract = self._qualify(symbol, sec_type, exchange, currency)
        if contract is None:
            return []
        providers = provider_codes or self.get_news_providers()
        if not providers:
            return []

        con_id = int(getattr(contract, "conId", 0) or 0)
        if con_id <= 0:
            return []

        codes = "+".join(providers)
        try:
            items = self._ib.reqHistoricalNews(
                con_id, codes, "", "", max_results
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("reqHistoricalNews failed for %s: %s", symbol, exc)
            return []

        out: list[NewsHeadline] = []
        for it in items or []:
            out.append(
                NewsHeadline(
                    symbol=symbol,
                    provider_code=getattr(it, "providerCode", "") or "",
                    article_id=getattr(it, "articleId", "") or "",
                    headline=getattr(it, "headline", "") or "",
                    time_utc=str(getattr(it, "time", "")) or None,
                )
            )
        return out

    def get_executions(self) -> list[ExecutionRow]:
        self._require_connection()
        out: list[ExecutionRow] = []
        fills = self._ib.fills() if hasattr(self._ib, "fills") else []
        for f in fills:
            c = f.contract
            e = f.execution
            out.append(
                ExecutionRow(
                    exec_id=getattr(e, "execId", "") or "",
                    perm_id=getattr(e, "permId", None),
                    order_id=getattr(e, "orderId", None),
                    account=getattr(e, "acctNumber", "") or "",
                    symbol=getattr(c, "symbol", "") or "",
                    sec_type=getattr(c, "secType", "") or "",
                    side=getattr(e, "side", "") or "",
                    shares=float(getattr(e, "shares", 0) or 0),
                    price=float(getattr(e, "price", 0) or 0),
                    time=str(getattr(e, "time", "")) or None,
                    exchange=getattr(e, "exchange", None),
                    order_ref=(
                        str(getattr(e, "orderRef", "") or "").strip() or None
                    ),
                )
            )
        return out

    def fetch_stock_min_tick(
        self, symbol: str, *, exchange: str = "SMART", currency: str = "USD"
    ) -> dict[str, Any]:
        """Request ``minTick`` for a qualified US stock (read-only, Prompt 13J.1).

        Used only when building paper bracket orders. Does **not** place orders.
        If contract details are unavailable, returns ``0.01`` with
        ``min_tick_source='fallback_us_stock_0.01'`` and an error string suitable
        for audit (``min_tick_fetch_error``).
        """
        from decimal import Decimal

        out: dict[str, Any] = {
            "min_tick": Decimal("0.01"),
            "min_tick_source": "fallback_us_stock_0.01",
            "min_tick_fetch_error": None,
        }
        self._require_connection()
        contract = self._qualify(str(symbol).upper(), "STK", exchange, currency)
        if contract is None:
            out["min_tick_fetch_error"] = "qualify_failed"
            return out
        try:
            cds = self._ib.reqContractDetails(contract)
        except Exception as exc:  # noqa: BLE001
            logger.info("reqContractDetails failed for %s: %s", symbol, exc)
            out["min_tick_fetch_error"] = str(exc)
            return out
        if not cds:
            out["min_tick_fetch_error"] = "empty_contract_details"
            return out
        cd0 = cds[0]
        mt = getattr(cd0, "minTick", None)
        if mt is None or float(mt) <= 0:
            out["min_tick_fetch_error"] = "min_tick_missing_or_zero"
            return out
        try:
            out["min_tick"] = Decimal(str(float(mt)))
        except (TypeError, ValueError, ArithmeticError):
            out["min_tick_fetch_error"] = "min_tick_parse_failed"
            return out
        out["min_tick_source"] = "contract_details"
        out["min_tick_fetch_error"] = None
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _enforce_paper_only(self) -> None:
        acct_mode = self.cfg.ibkr.account_mode
        block_live = self.cfg.settings.account.block_live_trading
        cfg_mode = self.cfg.settings.account.mode

        if block_live and (acct_mode != "paper" or cfg_mode != "paper"):
            raise LiveTradingBlocked(
                f"Refusing to connect: account.block_live_trading=true but "
                f"settings.account.mode={cfg_mode!r}, env IBKR_ACCOUNT_MODE={acct_mode!r}."
            )

        if block_live and self.cfg.ibkr.port not in PAPER_PORTS:
            raise LiveTradingBlocked(
                f"Refusing to connect: IBKR_PORT={self.cfg.ibkr.port} is not a known "
                f"paper port {sorted(PAPER_PORTS)}. Set IBKR_PORT=7497 or 4002, "
                f"or disable block_live_trading explicitly (not recommended)."
            )

    def _require_connection(self) -> None:
        if not self.is_connected:
            raise IBKRClientError("Not connected. Call connect() first.")


def _as_optional_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # ib_async uses sentinel large values for "unset" prices.
    if f >= 1e308 or f <= -1e308:
        return None
    return f


# ----------------------------------------------------------------------
# Module-level convenience wrappers (match the spec's function names).
# ----------------------------------------------------------------------
def connect(cfg: AppConfig) -> IBKRClient:
    client = IBKRClient(cfg)
    client.connect()
    return client


def disconnect(client: IBKRClient) -> None:
    client.disconnect()


def get_account_summary(client: IBKRClient) -> list[AccountSummary]:
    return client.get_account_summary()


def get_positions(client: IBKRClient) -> list[PositionRow]:
    return client.get_positions()


def get_open_orders(client: IBKRClient) -> list[OpenOrderRow]:
    return client.get_open_orders()


def get_executions(client: IBKRClient) -> list[ExecutionRow]:
    return client.get_executions()
