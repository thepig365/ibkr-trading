"""Central IBKR client_id routing for read-only CLI paths."""

from __future__ import annotations

import pytest

from bot.ibkr_connection import IbkrRoConnectOutcome, PUBLIC_COLLISION_HINT
from bot.ibkr_connection import connect_readonly_roster_retry, ibkr_client_collision_message
from bot.ibkr_connection import with_ibkr_client_id
from bot.ibkr_client_ids import (
    BROKER_READ_ONLY,
    CANDLE_FETCH,
    EDGE_FETCH,
    RESEARCH_FETCH,
    WATCHLIST_FETCH,
)


def test_module_constants_distinct_buckets() -> None:
    buckets = sorted(
        {BROKER_READ_ONLY, WATCHLIST_FETCH, CANDLE_FETCH, RESEARCH_FETCH, EDGE_FETCH}
    )
    assert len(buckets) == len(
        [BROKER_READ_ONLY, WATCHLIST_FETCH, CANDLE_FETCH, RESEARCH_FETCH, EDGE_FETCH]
    )


def test_with_ibkr_client_id_overrides(tmp_project) -> None:
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)
    n = with_ibkr_client_id(cfg, 88)
    assert n.ibkr.client_id == 88


def test_ibkr_collision_message_heuristic() -> None:
    assert ibkr_client_collision_message(RuntimeError("Error 326: client id"))
    assert ibkr_client_collision_message(ConnectionError("client id already in use"))


def test_readonly_retries_on_collision(monkeypatch: pytest.MonkeyPatch, tmp_project) -> None:
    """Third attempt succeeds after two Error 326 style failures."""
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)

    attempts: list[int] = []

    class BoomClient:
        def __init__(self, c) -> None:
            self._cid = int(c.ibkr.client_id)
            attempts.append(self._cid)

        def connect(self, *a, **kwargs) -> None:
            # Fail roster base +0 and base+1, succeed at base+2
            if self._cid <= WATCHLIST_FETCH + 1:
                raise ConnectionError(
                    "Error 326: client id ... already in use"
                )

    import bot.ibkr_client as ibkr_mod

    monkeypatch.setattr(ibkr_mod, "IBKRClient", BoomClient)

    out: IbkrRoConnectOutcome = connect_readonly_roster_retry(cfg, "watchlist")
    assert out.client is not None
    assert attempts == [WATCHLIST_FETCH, WATCHLIST_FETCH + 1, WATCHLIST_FETCH + 2]
    assert out.client_id_used == WATCHLIST_FETCH + 2


def test_non_collision_error_does_not_advance(monkeypatch: pytest.MonkeyPatch, tmp_project) -> None:
    """Connection errors unrelated to duplicate client IDs do not loop."""
    from bot.config import load_config

    cfg = load_config(project_root=tmp_project)

    class NetDown:
        def __init__(self, _cfg) -> None:
            pass

        def connect(self, *a, **kwargs) -> None:
            raise OSError("no route to host")

    import bot.ibkr_client as ibkr_mod

    monkeypatch.setattr(ibkr_mod, "IBKRClient", NetDown)
    oc = connect_readonly_roster_retry(cfg, "watchlist")
    assert oc.client is None
    assert len(oc.attempted_client_ids) == 1


def test_public_collision_hint_nonempty() -> None:
    assert "326" in PUBLIC_COLLISION_HINT or "already in use" in PUBLIC_COLLISION_HINT
