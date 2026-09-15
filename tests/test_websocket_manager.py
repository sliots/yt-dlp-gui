from __future__ import annotations

import asyncio
import json
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

    def test_warn_is_normalized_and_progress_is_not_historic(self):
        broadcaster = LogBroadcaster()
        broadcaster.broadcast_sync("WARN", "warning")
        broadcaster.broadcast_sync("PROGRESS", '{"percent": 50}')
        assert len(broadcaster._history) == 1
        assert broadcaster._history[0]["level"] == "WARNING"

    def test_jsonl_redacts_tokens_and_excludes_progress(self, tmp_path):
        path = tmp_path / "download.log"
        broadcaster = LogBroadcaster(log_path=path)
        broadcaster.broadcast_sync(
            "INFO", 'poToken=secret integrityToken:"also-secret"',
        )
        broadcaster.broadcast_sync("PROGRESS", "poToken=progress-secret")
        broadcaster._file_handler.close()

        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert len(entries) == 1
        assert entries[0]["msg"] == 'poToken=[REDACTED] integrityToken:"[REDACTED]"'
        assert "secret" not in entries[0]["msg"]
        assert broadcaster._history[0]["msg"] == entries[0]["msg"]

    def test_rotates_log_and_keeps_three_backups(self, tmp_path):
        path = tmp_path / "download.log"
        broadcaster = LogBroadcaster(log_path=path, max_bytes=200, backup_count=3)
        for index in range(20):
            broadcaster.broadcast_sync("INFO", f"{index}:" + "x" * 100)
        broadcaster._file_handler.close()
        backups = list(tmp_path.glob("download.log.*"))
        assert 1 <= len(backups) <= 3


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
