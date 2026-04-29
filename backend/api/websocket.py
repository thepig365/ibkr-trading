"""WebSocket endpoints and broadcaster for real-time engine status updates."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter()
StatusProvider = Callable[[], dict[str, Any]]


class EngineStatusBroadcaster:
    """Track dashboard WebSocket clients and broadcast engine status payloads."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a WebSocket client."""

        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        """Unregister a WebSocket client."""

        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        """Broadcast a JSON payload to all connected dashboard clients."""

        async with self._lock:
            clients = list(self._clients)

        disconnected: list[WebSocket] = []
        for websocket in clients:
            try:
                await websocket.send_json(payload)
            except Exception:  # noqa: BLE001
                disconnected.append(websocket)

        if disconnected:
            async with self._lock:
                for websocket in disconnected:
                    self._clients.discard(websocket)

    async def run_periodic(self, provider: StatusProvider) -> None:
        """Broadcast engine status every second."""

        while True:
            try:
                await self.broadcast(provider())
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Periodic engine status broadcast failed")
                await asyncio.sleep(1)


@router.websocket("/ws/engine-status")
async def engine_status_ws(websocket: WebSocket) -> None:
    """Stream connection state and countdown data to the dashboard."""

    broadcaster: EngineStatusBroadcaster = websocket.app.state.status_broadcaster
    connection_manager = websocket.app.state.connection_manager

    await broadcaster.connect(websocket)
    try:
        await websocket.send_json(connection_manager.status_dict())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await broadcaster.disconnect(websocket)
    except Exception:  # noqa: BLE001
        logger.exception("Engine status WebSocket failed")
        await broadcaster.disconnect(websocket)
