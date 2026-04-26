"""IBKRClient connection defaults (RequestTimeout cap for blocking util.run)."""

from __future__ import annotations

from pathlib import Path


def test_connect_sets_request_timeout_on_ib_instance(tmp_project: Path, monkeypatch) -> None:
    """``ib_async.IB.RequestTimeout`` default 0 = infinite; we set a finite cap after connect."""
    instances: list = []

    class FakeIB:
        RequestTimeout = 0

        def __init__(self) -> None:
            self._connected = False
            instances.append(self)

        def connect(
            self,
            *,
            host: str,
            port: int,
            clientId: int,
            timeout: float,
            readonly: bool,
        ) -> None:
            self._connected = True

        def disconnect(self) -> None:
            self._connected = False

        def isConnected(self) -> bool:
            return self._connected

    monkeypatch.setattr("bot.ibkr_client._IB", FakeIB)
    monkeypatch.setattr("bot.ibkr_client._IB_BACKEND", "fake")

    from bot.config import load_config
    from bot.ibkr_client import IBKRClient

    cfg = load_config(project_root=tmp_project)
    client = IBKRClient(cfg)
    client.connect()
    assert instances, "FakeIB should have been instantiated"
    ib = instances[-1]
    assert ib.RequestTimeout == 60.0
    client.disconnect()
    assert client._ib is None


def test_ibkr_request_timeout_env_override(tmp_project: Path, monkeypatch) -> None:
    monkeypatch.setenv("IBKR_REQUEST_TIMEOUT", "45")
    instances: list = []

    class FakeIB:
        RequestTimeout = 0

        def __init__(self) -> None:
            self._connected = False
            instances.append(self)

        def connect(
            self,
            *,
            host: str,
            port: int,
            clientId: int,
            timeout: float,
            readonly: bool,
        ) -> None:
            self._connected = True

        def disconnect(self) -> None:
            self._connected = False

        def isConnected(self) -> bool:
            return self._connected

    monkeypatch.setattr("bot.ibkr_client._IB", FakeIB)
    monkeypatch.setattr("bot.ibkr_client._IB_BACKEND", "fake")

    from bot.config import load_config
    from bot.ibkr_client import IBKRClient

    cfg = load_config(project_root=tmp_project)
    client = IBKRClient(cfg)
    client.connect()
    assert instances[-1].RequestTimeout == 45.0
    client.disconnect()
