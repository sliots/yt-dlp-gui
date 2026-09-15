from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import WebSocket

_loop: asyncio.AbstractEventLoop | None = None
_TOKEN_RE = re.compile(
    r"(?i)([\"']?(?:poToken|integrityToken)[\"']?\s*[:=]\s*[\"']?)([^\"'\s,&}]+)"
)


def set_event_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    global _loop
    _loop = loop


def _get_loop() -> asyncio.AbstractEventLoop:
    if _loop is not None:
        return _loop
    return asyncio.get_event_loop()


def _redact(message: str) -> str:
    return _TOKEN_RE.sub(r"\1[REDACTED]", message)


class _SafeRotatingFileHandler(RotatingFileHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._reported_failure = False

    def handleError(self, record: logging.LogRecord) -> None:
        if not self._reported_failure:
            self._reported_failure = True
            logging.getLogger(__name__).error("download.log 写入失败", exc_info=True)


class LogBroadcaster:
    def __init__(
        self,
        max_history: int = 200,
        log_path: str | Path | None = None,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 3,
    ) -> None:
        self._clients: set[WebSocket] = set()
        self._history: deque[dict[str, str]] = deque(maxlen=max_history)
        self._lock = threading.Lock()
        self._handler: _WSLogHandler | None = None
        self._file_handler: _SafeRotatingFileHandler | None = None
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

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        with self._lock:
            self._clients.add(ws)
            history = list(self._history)
        for entry in history:
            await ws.send_json(entry)

    def disconnect(self, ws: WebSocket) -> None:
        with self._lock:
            self._clients.discard(ws)

    def broadcast_sync(self, level: str, message: str) -> None:
        level = level.upper()
        if level == "WARN":
            level = "WARNING"
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "msg": _redact(str(message)),
        }

        with self._lock:
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

        stale: list[WebSocket] = []
        for ws in clients:
            try:
                asyncio.run_coroutine_threadsafe(ws.send_json(entry), _get_loop())
            except Exception:
                stale.append(ws)
        if stale:
            with self._lock:
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
