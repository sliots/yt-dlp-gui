from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone

from fastapi import WebSocket

_loop: asyncio.AbstractEventLoop | None = None


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def _get_loop() -> asyncio.AbstractEventLoop:
    if _loop is not None:
        return _loop
    return asyncio.get_event_loop()


class LogBroadcaster:
    def __init__(self, max_history: int = 200) -> None:
        self._clients: set[WebSocket] = set()
        self._history: deque[dict[str, str]] = deque(maxlen=max_history)
        self._handler: _WSLogHandler | None = None

    def install_log_handler(self, logger_name: str = "yt_dlp_gui") -> None:
        self._handler = _WSLogHandler(self)
        self._handler.setLevel(logging.INFO)
        logging.getLogger(logger_name).addHandler(self._handler)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        for entry in self._history:
            await ws.send_json(entry)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    def broadcast_sync(self, level: str, message: str) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "msg": message,
        }
        self._history.append(entry)
        stale: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                asyncio.run_coroutine_threadsafe(ws.send_json(entry), _get_loop())
            except Exception:
                stale.append(ws)
        for ws in stale:
            self._clients.discard(ws)


class _WSLogHandler(logging.Handler):
    def __init__(self, broadcaster: LogBroadcaster) -> None:
        super().__init__()
        self._broadcaster = broadcaster

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._broadcaster.broadcast_sync(record.levelname, self.format(record))
        except Exception:
            pass
