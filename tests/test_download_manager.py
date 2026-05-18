from __future__ import annotations

import asyncio
import copy
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from app.download_manager import DownloadManager
from app.websocket_manager import LogBroadcaster


@pytest.fixture
def config():
    return {
        "general": {
            "output_base_path": "/downloads",
            "proxy_url": "",
            "sleep_requests": 5,
            "sleep_time": "0s",
            "wait_time_minutes": 1,
            "quiet_mode": False,
            "dateafter": "20000101",
            "download_archive": "archive.txt",
            "filename_format": "[%(title)s].%(ext)s",
            "first_run_timeout": 360,
            "normal_timeout": 60,
            "cookies_file_path": "/app/config/cookies.txt",
        },
        "download_limits": {
            "normal_limit": 20,
            "first_run_limit": 99999,
        },
        "channels": [],
    }


@pytest.fixture
def broadcaster():
    return MagicMock(spec=LogBroadcaster)


class TestStatus:
    def test_initial_status_all_defaults(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        status = mgr.status
        assert status["running"] is False
        assert status["mode"] is None
        assert status["current_channel_index"] == 0
        assert status["total_channels"] == 0
        assert status["progress_percent"] == 0.0
        assert status["next_round_seconds"] == 0

    def test_status_countdown_when_loop_waiting(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        mgr._mode = "loop"
        mgr._running = False
        mgr._countdown_start = time.time() - 30
        mgr._countdown_duration = 3600
        status = mgr.status
        assert status["running"] is False
        assert status["mode"] == "loop"
        assert status["next_round_seconds"] > 0
        assert status["next_round_seconds"] < 3600

    def test_status_no_countdown_when_running(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        mgr._mode = "loop"
        mgr._running = True
        mgr._countdown_start = time.time() - 30
        mgr._countdown_duration = 3600
        status = mgr.status
        assert status["next_round_seconds"] == 0


class TestUpdateProgress:
    def test_updates_all_fields(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        mgr._update_progress({
            "channel_index": 2,
            "channel_total": 5,
            "channel_label": "TestChan (@test)",
            "percent": 53.5,
        })
        assert mgr._current_channel_index == 2
        assert mgr._total_channels == 5
        assert mgr._current_channel_label == "TestChan (@test)"
        assert mgr._progress_percent == 53.5

    def test_missing_fields_use_defaults(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        mgr._update_progress({})
        assert mgr._current_channel_index == 0
        assert mgr._total_channels == 0
        assert mgr._current_channel_label == ""


class TestStartMutex:
    @pytest.mark.asyncio
    async def test_start_once_when_already_running_rejects(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        mgr._running = True
        result = await mgr.start_once()
        assert result["ok"] is False
        assert "运行中" in result["reason"]

    @pytest.mark.asyncio
    async def test_start_loop_when_already_running_rejects(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        mgr._running = True
        result = await mgr.start_loop()
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_start_first_only_when_already_running_rejects(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        mgr._running = True
        result = await mgr.start_first_only()
        assert result["ok"] is False


class TestStartValues:
    @pytest.mark.asyncio
    async def test_start_once_returns_ok(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        result = await mgr.start_once()
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_start_loop_returns_ok(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        result = await mgr.start_loop()
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_start_first_only_returns_ok(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        result = await mgr.start_first_only()
        assert result["ok"] is True


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_without_engine_returns_ok(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        result = await mgr.stop()
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_stop_calls_engine_request_stop(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        mock_engine = MagicMock()
        mgr._engine = mock_engine
        result = await mgr.stop()
        assert result["ok"] is True
        mock_engine.request_stop.assert_called_once()


class TestShutdown:
    def test_shutdown_without_engine_does_nothing(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        mgr.shutdown()

    def test_shutdown_calls_engine_stop(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        mock_engine = MagicMock()
        mgr._engine = mock_engine
        mgr.shutdown()
        mock_engine.request_stop.assert_called_once()


class TestDeepCopySafety:
    def test_config_shallow_isolated(self):
        config = {
            "general": {},
            "download_limits": {},
            "channels": [
                {"folder_name": "A", "is_first": True},
                {"folder_name": "B", "is_first": False},
            ],
        }
        copy1 = copy.deepcopy(config)
        copy2 = copy.deepcopy(config)
        copy1["channels"][0]["is_first"] = False
        assert config["channels"][0]["is_first"] is True
        assert copy2["channels"][0]["is_first"] is True


class TestUpdateYtDlp:
    @pytest.mark.asyncio
    async def test_update_rejects_when_running(self, config, broadcaster):
        mgr = DownloadManager(config, broadcaster)
        mgr._running = True
        result = await mgr.update_ytdlp()
        assert result["ok"] is False
        assert "运行中" in result["reason"]
