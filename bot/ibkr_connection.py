"""Read-only IBKR connection helpers — client_id routing & collision retry.

Never places orders. Does not mutate :class:`bot.config.IBKREnv` on disk;
only builds derived :class:`bot.config.AppConfig` copies per connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import AppConfig
from .ibkr_client import IBKRClientError, LiveTradingBlocked
from .ibkr_client_ids import (
    BROKER_READ_ONLY,
    CANDLE_FETCH,
    EDGE_FETCH,
    RESEARCH_FETCH,
    WATCHLIST_FETCH,
)

_COLLISION_HINTS = (
    "326",
    "already in use",
    "client id",
    "clientid",
)


def ibkr_client_collision_message(exc: BaseException | None) -> bool:
    """Heuristic: TWS rejects duplicate Api client id — often Ib error 326."""
    blob = " ".join(
        filter(
            None,
            [
                repr(exc),
                str(exc),
                getattr(exc, "message", None) and str(getattr(exc, "message")),
            ],
        )
    ).lower()
    return any(h.lower() in blob for h in _COLLISION_HINTS)


PUBLIC_COLLISION_HINT = (
    "IBKR reached the gateway but client_id is already in use (often Error 326). "
    "Another process (supervisor, TWS-linked tool, previous CLI session) owns that "
    "client id — this is **not** a trading error. Try again shortly, close duplicate "
    "connections, or let this command pick the next routed id automatically."
)


@dataclass
class IbkrRoConnectOutcome:
    client: IBKRClient | None
    client_id_used: int | None
    attempted_client_ids: list[int] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    fatal_message: str | None = None
    live_blocked: LiveTradingBlocked | None = None


_ROSTER_MAP: dict[str, int] = {
    "broker_readonly": BROKER_READ_ONLY,
    "watchlist": WATCHLIST_FETCH,
    "candles": CANDLE_FETCH,
    "research": RESEARCH_FETCH,
    "edge": EDGE_FETCH,
}


def with_ibkr_client_id(cfg: AppConfig, client_id: int) -> AppConfig:
    ib = cfg.ibkr.model_copy(update={"client_id": int(client_id)})
    return cfg.model_copy(update={"ibkr": ib})


def connect_readonly_roster_retry(
    cfg: AppConfig,
    roster_key: str,
    *,
    max_fallbacks: int = 3,
    base_override: int | None = None,
    ib_connect_timeout: float = 10.0,
) -> IbkrRoConnectOutcome:
    """Try ``base``, ``base+1``, … on **client-id collision** signatures only."""
    root = (
        base_override if base_override is not None else _ROSTER_MAP.get(roster_key)
    )
    if root is None:
        return IbkrRoConnectOutcome(
            client=None,
            client_id_used=None,
            fatal_message=f"Unknown roster_key {roster_key!r}",
        )

    lines: list[str] = []
    attempted: list[int] = []
    last_fatal: BaseException | None = None

    from .ibkr_client import IBKRClient  # noqa: PLC0415 — late import for tests that patch

    for step in range(int(max_fallbacks)):
        cid = int(root) + step
        attempted.append(cid)
        try:
            sub = with_ibkr_client_id(cfg, cid)
            cl = IBKRClient(sub)
            cl.connect(readonly=True, timeout=float(ib_connect_timeout))
            lines.append(
                f"IBKR read-only OK with client_id={cid} roster={roster_key!r}; "
                f"attempted={attempted}"
            )
            return IbkrRoConnectOutcome(
                client=cl,
                client_id_used=cid,
                attempted_client_ids=list(attempted),
                log_lines=lines,
            )
        except LiveTradingBlocked as exc:
            return IbkrRoConnectOutcome(
                client=None,
                client_id_used=None,
                attempted_client_ids=list(attempted),
                log_lines=lines,
                live_blocked=exc,
            )
        except (IBKRClientError, TimeoutError, OSError, ConnectionError, RuntimeError) as exc:
            last_fatal = exc
            if ibkr_client_collision_message(exc) and step < max_fallbacks - 1:
                lines.append(
                    f"[watch] client_id={cid}: busy/collision ({type(exc).__name__}); retrying..."
                )
                continue
            break
        except Exception as exc:  # noqa: BLE001
            last_fatal = exc
            if ibkr_client_collision_message(exc) and step < max_fallbacks - 1:
                lines.append(
                    f"[watch] client_id={cid}: collision-like ({type(exc).__name__}); retrying..."
                )
                continue
            break

    msg = PUBLIC_COLLISION_HINT
    if last_fatal is not None:
        msg = (
            f"{PUBLIC_COLLISION_HINT}\nDetail: [{type(last_fatal).__name__}] {last_fatal!s}"
        )
    lines.append(msg)
    return IbkrRoConnectOutcome(
        client=None,
        client_id_used=None,
        attempted_client_ids=list(attempted),
        log_lines=lines,
        fatal_message=msg,
    )


__all__ = [
    "IbkrRoConnectOutcome",
    "PUBLIC_COLLISION_HINT",
    "with_ibkr_client_id",
    "connect_readonly_roster_retry",
    "ibkr_client_collision_message",
]
