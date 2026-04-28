"""Watchlist builder delegates IBKR connects to the watchlist roster bucket."""

from __future__ import annotations

import pytest

from bot.config import load_config
from bot.ibkr_connection import IbkrRoConnectOutcome


def test_build_universe_candidates_requests_watchlist_roster(
    tmp_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bot.cli as cli_mod

    roster_keys: list[str] = []

    def _fake_roster(cfg, key: str, **kwargs: object) -> IbkrRoConnectOutcome:
        roster_keys.append(key)
        return IbkrRoConnectOutcome(
            client=None,
            client_id_used=None,
            attempted_client_ids=[],
            log_lines=["skipped for unit test"],
            fatal_message="stub: no TCP in tests",
        )

    monkeypatch.setattr(cli_mod, "connect_readonly_roster_retry", _fake_roster)
    from bot.cli import _build_universe_candidates

    cfg = load_config(project_root=tmp_project)
    cands, _, notes = _build_universe_candidates(
        cfg, ["SPY"], use_ibkr=True, ibkr_days=5
    )
    assert roster_keys == ["watchlist"]
    assert cands and "bars" in cands[0].missing_fields
    assert any("stub" in n.lower() for n in notes)


def test_watchlist_fetch_id_constant_not_one() -> None:
    from bot.ibkr_client_ids import PAPER_ENGINE_DEFAULT, WATCHLIST_FETCH

    assert WATCHLIST_FETCH != PAPER_ENGINE_DEFAULT
