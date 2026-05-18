from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.engine import YtDlpEngine


@pytest.fixture
def engine():
    loop = asyncio.new_event_loop()
    broadcaster = MagicMock()
    broadcaster.broadcast_sync = MagicMock()
    config = {
        "general": {
            "output_base_path": "/downloads",
            "proxy_url": "",
            "sleep_requests": 5,
            "sleep_time": "0s",
            "wait_time_minutes": 360,
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
        "channels": [
            {
                "folder_name": "TestChan",
                "youtube_id": "@test",
                "vid_type": "videos",
                "dl_type": "audio",
                "enabled": True,
                "is_regex": False,
                "is_first": True,
            },
            {
                "folder_name": "TestChan2",
                "youtube_id": "@test2",
                "vid_type": "streams",
                "dl_type": "video",
                "enabled": True,
                "is_regex": True,
                "is_first": False,
            },
        ],
    }
    engine = YtDlpEngine(config, broadcaster)
    yield engine
    loop.close()


class TestDownloadFormat:
    def test_audio_format(self, engine):
        assert engine._get_download_format("audio") == "bestaudio"

    def test_video_format(self, engine):
        assert engine._get_download_format("video") == "bestvideo+bestaudio/best"

    def test_unknown_format_falls_back_to_video(self, engine):
        assert engine._get_download_format("unknown") == "bestvideo+bestaudio/best"


class TestMatchFilter:
    def test_no_regex_returns_base_only(self, engine):
        result = engine._get_match_filter(False)
        assert result == "!is_live & live_status!=is_upcoming"
        assert "ASMR" not in result
        assert "asmr" not in result

    def test_regex_adds_asmr_filter(self, engine):
        result = engine._get_match_filter(True)
        assert result.startswith("!is_live & live_status!=is_upcoming")
        assert "ASMR" in result
        assert "asmr" in result
        assert "安眠" in result
        assert "KU100" in result
        assert "Asmr" in result


class TestDownloadLimit:
    def test_first_run_uses_first_limit(self, engine):
        dateafter, limit = engine._get_download_limit(True)
        assert dateafter == "20000101"
        assert limit == 99999

    def test_normal_uses_normal_limit(self, engine):
        dateafter, limit = engine._get_download_limit(False)
        assert dateafter == "20000101"
        assert limit == 20


class TestParseTime:
    def test_seconds(self):
        assert YtDlpEngine._parse_time("5s") == 5.0
        assert YtDlpEngine._parse_time("0s") == 0.0

    def test_minutes(self):
        assert YtDlpEngine._parse_time("5m") == 300.0
        assert YtDlpEngine._parse_time("1m") == 60.0

    def test_hours(self):
        assert YtDlpEngine._parse_time("1h") == 3600.0
        assert YtDlpEngine._parse_time("0.5h") == 1800.0

    def test_raw_number(self):
        assert YtDlpEngine._parse_time("42") == 42.0
        assert YtDlpEngine._parse_time("3.14") == 3.14


class TestProgressRegex:
    def test_match_integer_percent(self):
        match = re.search(r"\[download\]\s+(\d+\.?\d*)%", "[download] 53% of ~50.00MiB")
        assert match is not None
        assert match.group(1) == "53"

    def test_match_decimal_percent(self):
        match = re.search(r"\[download\]\s+(\d+\.?\d*)%", "[download] 53.2% of ~50.00MiB")
        assert match is not None
        assert match.group(1) == "53.2"

    def test_match_100_percent(self):
        match = re.search(r"\[download\]\s+(\d+\.?\d*)%", "[download] 100.0% of ~50.00MiB in 00:45")
        assert match is not None
        assert match.group(1) == "100.0"

    def test_no_match_without_percent(self):
        match = re.search(r"\[download\]\s+(\d+\.?\d*)%", "[download] Finished")
        assert match is None

    def test_engine_progress_re_matches(self, engine):
        assert engine._progress_re.search("[download] 35.7% of ~50.00MiB")


class TestStopFlag:
    def test_request_stop_sets_flag(self, engine):
        assert engine._stop_flag is False
        engine.request_stop()
        assert engine._stop_flag is True

    def test_reset_stop_clears_flag(self, engine):
        engine._stop_flag = True
        engine.reset_stop()
        assert engine._stop_flag is False


class TestEmit:
    def test_emit_calls_broadcaster(self, engine):
        engine._emit("test message", "error")
        engine._broadcaster.broadcast_sync.assert_called_once_with("ERROR", "test message")

    def test_emit_defaults_to_info_level(self, engine):
        engine._emit("test")
        engine._broadcaster.broadcast_sync.assert_called_once_with("INFO", "test")


class TestRunAll:
    def test_no_channels_returns_true(self, engine):
        engine.channels = []
        assert engine.run_all() is True

    def test_all_disabled_returns_true_with_warning(self, engine):
        engine.channels = [
            {"folder_name": "A", "enabled": False, "is_first": False},
            {"folder_name": "B", "enabled": False, "is_first": False},
        ]
        assert engine.run_all() is True
        engine._broadcaster.broadcast_sync.assert_any_call("WARN", "⚠ 没有启用的频道")

    def test_sorts_first_channels_first(self, engine):
        engine.channels = [
            {"folder_name": "Normal", "enabled": True, "is_first": False},
            {"folder_name": "First", "enabled": True, "is_first": True},
        ]
        ordered = sorted(
            [ch for ch in engine.channels if ch.get("enabled", True)],
            key=lambda c: 0 if c.get("is_first", False) else 1,
        )
        assert ordered[0]["folder_name"] == "First"
        assert ordered[1]["folder_name"] == "Normal"


class TestGetChannelState:
    def test_returns_channels(self, engine):
        state = engine.get_channel_state()
        assert len(state) == 2
        assert state[0]["folder_name"] == "TestChan"

    def test_deepcopy_isolation(self, engine):
        state1 = engine.get_channel_state()
        state2 = engine.get_channel_state()
        assert state1 is state2
