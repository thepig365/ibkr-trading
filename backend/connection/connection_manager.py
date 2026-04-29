"""IBKR TWS/Gateway connection lifecycle management.

ConnectionManager owns the ib_insync IB instance, starts the 30-minute safety
session timer, monitors heartbeats, exposes reconnect behavior, and broadcasts
state changes for the dashboard.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Union

import pytz
from ib_insync import IB

from backend.config import AppConfig

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")

StatusBroadcast = Callable[[dict[str, Any]], Awaitable[None]]
ErrorNotifier = Callable[[str], Union[Awaitable[None], None]]


class ConnectionState(str, Enum):
    """IBKR connection states shown by the dashboard."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    AUTO_PAUSED = "AUTO_PAUSED"
    ERROR = "ERROR"


class ConnectionManager:
    """Manage IBKR connect, disconnect, heartbeat, and reconnect workflows."""

    def __init__(
        self,
        config: AppConfig,
        *,
        ib: Optional[IB] = None,
        status_broadcast: Optional[StatusBroadcast] = None,
        error_notifier: Optional[ErrorNotifier] = None,
    ) -> None:
        self.config = config
        self.ib = ib or IB()
        self.state = ConnectionState.DISCONNECTED
        self.last_error: Optional[str] = None
        self.connected_at: Optional[datetime] = None
        self.auto_disconnect_at: Optional[datetime] = None
        self.last_heartbeat_at: Optional[datetime] = None
        self._status_broadcast = status_broadcast
        self._error_notifier = error_notifier
        self._auto_disconnect_task: Optional[asyncio.Task[None]] = None
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._lock = asyncio.Lock()

    @property
    def time_remaining(self) -> int:
        """Return seconds left before auto-disconnect."""

        if self.state != ConnectionState.CONNECTED or self.auto_disconnect_at is None:
            return 0
        remaining = int((self.auto_disconnect_at - datetime.now(NY)).total_seconds())
        return max(0, remaining)

    async def connect(self) -> None:
        """Connect to IB Gateway/TWS and start lifecycle tasks."""

        async with self._lock:
            await self._cancel_lifecycle_tasks()
            self.state = ConnectionState.CONNECTING
            self.last_error = None
            await self._broadcast_state()

            if (
                self.config.ibkr.port == 7496
                and not self.config.ibkr.allow_live_trading
            ):
                self.state = ConnectionState.ERROR
                self.last_error = (
                    "Refusing TWS live port 7496; set ibkr.allow_live_trading "
                    "to true in config.yaml to override (not recommended)."
                )
                logger.error(self.last_error)
                await self._broadcast_state()
                return

            try:
                logger.info(
                    "Connecting to IBKR %s:%s client_id=%s",
                    self.config.ibkr.host,
                    self.config.ibkr.port,
                    self.config.ibkr.client_id,
                )
                await asyncio.wait_for(
                    self.ib.connectAsync(
                        self.config.ibkr.host,
                        self.config.ibkr.port,
                        clientId=self.config.ibkr.client_id,
                        timeout=self.config.connection.reconnect_timeout_sec,
                    ),
                    timeout=self.config.connection.reconnect_timeout_sec + 2,
                )

                now = datetime.now(NY)
                self.connected_at = now
                self.auto_disconnect_at = now + timedelta(
                    minutes=self.config.connection.auto_disconnect_minutes
                )
                self.last_heartbeat_at = now
                self.state = ConnectionState.CONNECTED
                logger.info("Connected to IBKR Paper Gateway")

                self._auto_disconnect_task = asyncio.create_task(
                    self._auto_disconnect_after_timeout(),
                    name="ibkr-auto-disconnect",
                )
                self._heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(),
                    name="ibkr-heartbeat",
                )
                await self._broadcast_state()
            except Exception as exc:  # noqa: BLE001 - connection errors vary by platform
                self.state = ConnectionState.ERROR
                self.last_error = str(exc)
                logger.exception("IBKR connection failed")
                await self._notify_error(f"IBKR connection failed: {exc}")
                await self._broadcast_state()

    async def reconnect(self) -> None:
        """Reconnect after AUTO_PAUSED, ERROR, or manual dashboard action."""

        async with self._lock:
            await self._cancel_lifecycle_tasks()
            await self._disconnect_ib()
            self.state = ConnectionState.DISCONNECTED
            self.connected_at = None
            self.auto_disconnect_at = None
            await self._broadcast_state()

        await self.connect()

    async def disconnect(
        self,
        *,
        reason: str = "manual",
        final_state: ConnectionState = ConnectionState.DISCONNECTED,
    ) -> None:
        """Disconnect from IBKR and update state."""

        async with self._lock:
            await self._cancel_lifecycle_tasks()
            await self._disconnect_ib()
            self.state = final_state
            self.connected_at = None
            self.auto_disconnect_at = None
            logger.info("IBKR disconnected: %s", reason)
            await self._broadcast_state()

    def status_dict(self) -> dict[str, Any]:
        """Return the current connection status payload for REST and WebSocket."""

        return {
            "state": self.state.value,
            "connected": bool(self.ib.isConnected()),
            "time_remaining": self.time_remaining,
            "auto_disconnect_minutes": self.config.connection.auto_disconnect_minutes,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "auto_disconnect_at": (
                self.auto_disconnect_at.isoformat() if self.auto_disconnect_at else None
            ),
            "last_heartbeat_at": (
                self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None
            ),
            "last_error": self.last_error,
            "host": self.config.ibkr.host,
            "port": self.config.ibkr.port,
            "account": self.config.ibkr.account,
        }

    async def _auto_disconnect_after_timeout(self) -> None:
        delay = self.config.connection.auto_disconnect_minutes * 60
        try:
            await asyncio.sleep(delay)
            async with self._lock:
                if self.state != ConnectionState.CONNECTED:
                    return
                await self._disconnect_ib()
                self.state = ConnectionState.AUTO_PAUSED
                self.auto_disconnect_at = None
                logger.warning("IBKR auto-disconnected after %s minutes", delay // 60)
                await self._broadcast_state()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Auto-disconnect failed")
            self.state = ConnectionState.ERROR
            self.last_error = str(exc)
            await self._notify_error(f"IBKR auto-disconnect failed: {exc}")
            await self._broadcast_state()

    async def _heartbeat_loop(self) -> None:
        interval = self.config.connection.heartbeat_interval_sec
        while True:
            try:
                await asyncio.sleep(interval)
                if self.state != ConnectionState.CONNECTED:
                    continue

                if not self.ib.isConnected():
                    raise ConnectionError("IBKR socket reports disconnected")

                await asyncio.wait_for(
                    self.ib.reqCurrentTimeAsync(),
                    timeout=max(5, min(interval, 10)),
                )
                self.last_heartbeat_at = datetime.now(NY)
                await self._broadcast_state()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.state = ConnectionState.ERROR
                self.last_error = str(exc)
                logger.exception("IBKR heartbeat failed")
                await self._notify_error(f"IBKR heartbeat failed: {exc}")
                await self._broadcast_state()
                return

    async def _disconnect_ib(self) -> None:
        if self.ib.isConnected():
            await asyncio.to_thread(self.ib.disconnect)

    async def _cancel_lifecycle_tasks(self) -> None:
        tasks = [self._auto_disconnect_task, self._heartbeat_task]
        self._auto_disconnect_task = None
        self._heartbeat_task = None
        for task in tasks:
            if task and not task.done():
                task.cancel()
        for task in tasks:
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _broadcast_state(self) -> None:
        if not self._status_broadcast:
            return
        try:
            await self._status_broadcast(self.status_dict())
        except Exception:  # noqa: BLE001
            logger.exception("Failed to broadcast connection state")

    async def _notify_error(self, message: str) -> None:
        if not self._error_notifier:
            return
        try:
            result = self._error_notifier(message)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send error notification")
