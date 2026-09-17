from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import WebSocket

_loop: asyncio.AbstractEventLoop | None = None
_TOKEN_RE = re.compile(
    r"(?i)([\"']?(?:poToken|integrityToken|token|APP_TOKEN)[\"']?\s*[:=]\s*[\"']?)"
    r"([^\"'\s,&}]+)"
)


def set_event_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    global _loop
    _loop = loop


def _get_loop() -> asyncio.AbstractEventLoop:
    if _loop is not None:
        return _loop
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.get_event_loop()


def _redact(message: str) -> str:
    message = _TOKEN_RE.sub(r"\1[REDACTED]", message)
    return re.sub(r"(?i)([?&]token=)[^&\s]+", r"\1[REDACTED]", message)


class _SafeRotatingFileHandler(RotatingFileHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._reported_failure = False

    def _open(self):
        stream = super()._open()
        try:
            Path(self.baseFilename).chmod(0o600)
        except OSError:
            pass
        return stream

    def handleError(self, record: logging.LogRecord) -> None:
        if not self._reported_failure:
            self._reported_failure = True
            logging.getLogger(__name__).exception("download.log 写入失败")


@dataclass(eq=False)
class _Client:
    websocket: WebSocket
    queue: asyncio.Queue[dict] = field(default_factory=lambda: asyncio.Queue(maxsize=200))
    writer: asyncio.Task | None = None
    closed: bool = False


class LogBroadcaster:
    def __init__(
        self,
        max_history: int = 200,
        log_path: str | Path | None = None,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 3,
    ) -> None:
        self._clients: set[_Client] = set()
        self._history: deque[dict] = deque(maxlen=max_history)
        self._lock = threading.Lock()
        self._handler: _WSLogHandler | None = None
        self._file_handler: _SafeRotatingFileHandler | None = None
        self._sequence = 0
        self._last_progress_at = 0.0
        if log_path is not None:
            path = Path(log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file_handler = _SafeRotatingFileHandler(
                path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
                delay=True,
            )
            self._file_handler.setFormatter(logging.Formatter("%(message)s"))

    def install_log_handler(self, logger_name: str = "yt_dlp_gui") -> None:
        self._handler = _WSLogHandler(self)
        self._handler.setLevel(logging.INFO)
        logging.getLogger(logger_name).addHandler(self._handler)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        client = _Client(websocket=websocket)
        with self._lock:
            self._clients.add(client)
            history = list(self._history)
            sequence = self._sequence
        client.writer = asyncio.create_task(self._writer(client), name="log-writer")
        for entry in history:
            self._enqueue(client, entry)
        self._enqueue(
            client,
            {
                "seq": sequence,
                "ts": datetime.now(UTC).isoformat(),
                "level": "SYNC",
                "msg": "",
            },
        )

    def disconnect(self, websocket: WebSocket) -> None:
        client = None
        with self._lock:
            client = next((item for item in self._clients if item.websocket is websocket), None)
            if client is not None:
                client.closed = True
                self._clients.discard(client)
        if client is not None and client.writer is not None:
            client.writer.cancel()

    async def _writer(self, client: _Client) -> None:
        try:
            while not client.closed:
                try:
                    entry = await asyncio.wait_for(client.queue.get(), timeout=20)
                except TimeoutError:
                    await client.websocket.send_json(
                        {
                            "seq": self._sequence,
                            "ts": datetime.now(UTC).isoformat(),
                            "level": "PING",
                            "msg": "",
                        }
                    )
                    continue
                await client.websocket.send_json(entry)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.disconnect(client.websocket)

    def _enqueue(self, client: _Client, entry: dict) -> None:
        if client.closed:
            return
        try:
            client.queue.put_nowait(entry)
        except asyncio.QueueFull:
            try:
                client.queue.get_nowait()
                client.queue.task_done()
            except asyncio.QueueEmpty:
                pass
            try:
                client.queue.put_nowait(entry)
            except asyncio.QueueFull:
                pass

    def broadcast_sync(self, level: str, message: str) -> None:
        level = level.upper()
        if level == "WARN":
            level = "WARNING"
        if level == "PROGRESS":
            now = time.monotonic()
            if now - self._last_progress_at < 0.2 and '"percent": 100' not in message:
                return
            self._last_progress_at = now

        with self._lock:
            self._sequence += 1
            entry = {
                "seq": self._sequence,
                "ts": datetime.now(UTC).isoformat(),
                "level": level,
                "msg": _redact(str(message)),
            }
            if level != "PROGRESS":
                self._history.append(entry)
                if self._file_handler is not None:
                    record = logging.LogRecord(
                        name="yt_dlp_gui.download",
                        level=logging.INFO,
                        pathname="",
                        lineno=0,
                        msg=json.dumps(entry, ensure_ascii=False),
                        args=(),
                        exc_info=None,
                    )
                    self._file_handler.emit(record)
            clients = list(self._clients)

        try:
            loop = _get_loop()
        except RuntimeError:
            return
        stale: list[_Client] = []
        for client in clients:
            try:
                loop.call_soon_threadsafe(self._enqueue, client, entry)
            except RuntimeError:
                stale.append(client)
        for client in stale:
            self.disconnect(client.websocket)


class _WSLogHandler(logging.Handler):
    def __init__(self, broadcaster: LogBroadcaster) -> None:
        super().__init__()
        self._broadcaster = broadcaster

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._broadcaster.broadcast_sync(record.levelname, self.format(record))
        except Exception:
            self.handleError(record)
