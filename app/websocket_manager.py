"""
WebSocket connection manager for real-time frontend updates.
Broadcasts tick data, order updates, P&L, and system events.
"""
import asyncio
import json
from datetime import datetime
from fastapi import WebSocket
from core.logger import get_logger

logger = get_logger("websocket")


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts messages."""

    def __init__(self):
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)
        logger.info(f"WebSocket connected. Total: {len(self._connections)}")

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            if ws in self._connections:
                self._connections.remove(ws)
        logger.info(f"WebSocket disconnected. Total: {len(self._connections)}")

    async def broadcast(self, event: str, data: dict):
        """Send a message to all connected clients."""
        message = json.dumps({
            "event": event,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        })
        async with self._lock:
            dead = []
            for ws in self._connections:
                try:
                    await ws.send_text(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._connections.remove(ws)

    @property
    def active_count(self) -> int:
        return len(self._connections)


# Singleton
ws_manager = ConnectionManager()
