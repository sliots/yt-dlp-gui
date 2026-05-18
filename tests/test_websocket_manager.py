from __future__ import annotations

import asyncio
import logging

import pytest

from app.websocket_manager import (
    LogBroadcaster,
    _get_loop,
    _WSLogHandler,
    set_event_loop,
)


class TestEventLoop:
    def test_set_and_get_loop(self):
        loop = asyncio.new_event_loop()
        try:
            set_event_loop(loop)
            assert _get_loop() is loop
        finally:
            loop.close()

    def test_get_loop_falls_back_to_running_loop(self):
        set_event_loop(None)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            assert _get_loop() is loop
        finally:
            loop.close()
            asyncio.set_event_loop(None)


class TestLogBroadcasterInit:
    def test_default_max_history(self):
        broadcaster = LogBroadcaster()
        assert len(broadcaster._history) == 0
        assert broadcaster._history.maxlen == 200

    def test_custom_max_history(self):
        broadcaster = LogBroadcaster(max_history=50)
        assert broadcaster._history.maxlen == 50

    def test_no_clients_initially(self):
        broadcaster = LogBroadcaster()
        assert len(broadcaster._clients) == 0


class TestBroadcastSync:
    def test_appends_to_history(self):
        broadcaster = LogBroadcaster(max_history=10)
        broadcaster.broadcast_sync("INFO", "test message")
        assert len(broadcaster._history) == 1
        entry = broadcaster._history[0]
        assert entry["level"] == "INFO"
        assert entry["msg"] == "test message"
        assert "ts" in entry

    def test_enforces_history_limit(self):
        broadcaster = LogBroadcaster(max_history=3)
        for i in range(5):
            broadcaster.broadcast_sync("INFO", f"msg {i}")
        assert len(broadcaster._history) == 3
        assert broadcaster._history[0]["msg"] == "msg 2"
        assert broadcaster._history[-1]["msg"] == "msg 4"

    def test_no_clients_does_not_raise(self):
        broadcaster = LogBroadcaster()
        broadcaster.broadcast_sync("ERROR", "test")


class TestWSLogHandler:
    def test_emit_sends_to_broadcaster(self):
        broadcaster = LogBroadcaster()
        handler = _WSLogHandler(broadcaster)
        handler.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=1,
            msg="hello world", args=(), exc_info=None
        )
        handler.emit(record)
        assert len(broadcaster._history) == 1
        assert broadcaster._history[0]["msg"] == "hello world"
        assert broadcaster._history[0]["level"] == "INFO"


class TestInstallLogHandler:
    def test_installs_handler_on_logger(self):
        broadcaster = LogBroadcaster()
        broadcaster.install_log_handler("test_yt_dlp_gui_logger")
        logger = logging.getLogger("test_yt_dlp_gui_logger")
        handlers = [h for h in logger.handlers if isinstance(h, _WSLogHandler)]
        assert len(handlers) == 1
        logger.removeHandler(handlers[0])
