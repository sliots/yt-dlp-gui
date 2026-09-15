from __future__ import annotations

import asyncio
import copy
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from app.download_manager import DownloadManager
from app.websocket_manager import LogBroadcaster

CHANNEL_A = "11111111-1111-4111-8111-111111111111"
CHANNEL_B = "22222222-2222-4222-8222-222222222222"


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
            "cookies_source": "browser",
            "cookies_file_path": "/app/config/cookies.txt",
        },
        "download_limits": {"normal_limit": 20, "first_run_limit": 99999},
        "channels": [
            {
                "channel_id": CHANNEL_A,
                "folder_name": "A",
                "youtube_id": "a",
                "vid_type": "videos",
                "dl_type": "audio",
                "enabled": True,
                "is_regex": False,
                "is_first": True,
            },
        ],
    }


@pytest.fixture
def broadcaster():
    return MagicMock(spec=LogBroadcaster)


class FakeEngine:
    def __init__(self, config, broadcaster, progress_callback=None):
        self.channels = copy.deepcopy(config["channels"])
        self.progress_callback = progress_callback
        self.stopped = False
        self.stats = {
            "total_channels": len(self.channels),
            "processed_channels": len(self.channels),
            "completed_channels": len(self.channels),
            "warning_channels": 0,
            "downloaded_files": 1,
            "archive_skipped": 0,
            "filtered_skipped": 0,
            "member_skipped": 0,
            "hard_errors": 0,
            "stopped": False,
        }

    def run_all(self):
        if self.progress_callback:
            self.progress_callback({
                "channel_index": 0,
                "channel_total": len(self.channels),
                "channel_label": "A (a/videos)",
                "percent": 75,
            })
        for channel in self.channels:
            channel["is_first"] = False
        return True

    def request_stop(self):
        self.stopped = True
        self.stats["stopped"] = True

    def get_run_stats(self):
        return dict(self.stats)

    def get_channel_state(self):
        return copy.deepcopy(self.channels)


async def wait_for_state(manager: DownloadManager, state: str) -> None:
    for _ in range(100):
        if manager.status["state"] == state:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"manager did not reach {state}")


class TestStatus:
    def test_initial_status(self, config, broadcaster, tmp_path):
        status = DownloadManager(config, broadcaster, tmp_path / "config.toml").status
        assert status["state"] == "idle"
        assert status["active"] is False
        assert status["running"] is False
        assert status["current_run"] is None
        assert status["last_run"] is None

    def test_waiting_status_has_countdown_but_no_progress(self, config, broadcaster, tmp_path):
        manager = DownloadManager(config, broadcaster, tmp_path / "config.toml")
        manager._state = "waiting"
        manager._mode = "loop"
        manager._countdown_start = time.time() - 1
        manager._countdown_duration = 60
        status = manager.status
        assert status["active"] is True
        assert status["running"] is False
        assert 0 < status["next_round_seconds"] < 60


class TestLifecycle:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["running", "waiting", "stopping"])
    async def test_start_rejects_every_active_state(self, config, broadcaster, tmp_path, state):
        manager = DownloadManager(config, broadcaster, tmp_path / "config.toml")
        manager._state = state
        assert (await manager.start_once())["ok"] is False

    @pytest.mark.asyncio
    async def test_normal_run_persists_summary_and_clears_progress(self, config, broadcaster, tmp_path):
        path = tmp_path / "config.toml"
        manager = DownloadManager(config, broadcaster, path)
        with patch("app.download_manager.YtDlpEngine", FakeEngine):
            assert (await manager.start_once())["ok"] is True
            task = manager._current_task
            await task

        status = manager.status
        assert status["state"] == "idle"
        assert status["total_channels"] == 0
        assert status["progress_percent"] == 0
        assert status["last_run"]["completed_channels"] == 1
        assert status["last_run"]["downloaded_files"] == 1
        assert config["channels"][0]["is_first"] is False
        assert DownloadManager(config, broadcaster, path).status["last_run"] == status["last_run"]

    @pytest.mark.asyncio
    async def test_loop_waiting_is_mutexed_and_stops_immediately(self, config, broadcaster, tmp_path):
        config_path = tmp_path / "config.toml"
        loop_state_path = tmp_path / "loop_state.json"
        manager = DownloadManager(config, broadcaster, config_path)
        with patch("app.download_manager.YtDlpEngine", FakeEngine):
            assert (await manager.start_loop())["ok"] is True
            task = manager._current_task
            await wait_for_state(manager, "waiting")
            assert loop_state_path.exists()
            loop_state = json.loads(loop_state_path.read_text(encoding="utf-8"))
            assert loop_state["state"] == "waiting"
            status = manager.status
            assert status["active"] is True
            assert status["current_run"] is None
            assert status["total_channels"] == 0
            assert (await manager.start_once())["ok"] is False
            assert (await manager.update_ytdlp())["ok"] is False
            assert (await manager.stop())["ok"] is True
            await asyncio.wait_for(task, timeout=1)
        assert manager.status["state"] == "idle"
        assert not loop_state_path.exists()

        with patch("app.download_manager.YtDlpEngine", FakeEngine):
            assert (await manager.start_loop())["ok"] is True
            assert manager.status["state"] == "running"
            task = manager._current_task
            await manager.stop()
            await asyncio.wait_for(task, timeout=1)

    @pytest.mark.asyncio
    async def test_auto_start_resumes_persisted_wait(self, config, broadcaster, tmp_path):
        config_path = tmp_path / "config.toml"
        manager = DownloadManager(config, broadcaster, config_path)
        manager._persist_loop_deadline(time.time() + 60)

        restored = DownloadManager(config, broadcaster, config_path)
        with patch("app.download_manager.YtDlpEngine", FakeEngine):
            assert (await restored.auto_start_loop())["ok"] is True
            status = restored.status
            assert status["state"] == "waiting"
            assert 0 < status["next_round_seconds"] <= 60
            assert status["last_run"] is None
            task = restored._current_task
            await restored.stop()
            await asyncio.wait_for(task, timeout=1)

    @pytest.mark.asyncio
    async def test_auto_start_without_valid_wait_runs_immediately(self, config, broadcaster, tmp_path):
        config_path = tmp_path / "config.toml"
        manager = DownloadManager(config, broadcaster, config_path)
        manager._persist_loop_deadline(time.time() - 1)

        with patch("app.download_manager.YtDlpEngine", FakeEngine):
            assert (await manager.auto_start_loop())["ok"] is True
            assert manager.status["state"] == "running"
            assert not (tmp_path / "loop_state.json").exists()
            task = manager._current_task
            await manager.stop()
            await asyncio.wait_for(task, timeout=1)

    @pytest.mark.asyncio
    async def test_shutdown_preserves_wait_deadline(self, config, broadcaster, tmp_path):
        config_path = tmp_path / "config.toml"
        manager = DownloadManager(config, broadcaster, config_path)
        with patch("app.download_manager.YtDlpEngine", FakeEngine):
            assert (await manager.start_loop())["ok"] is True
            await wait_for_state(manager, "waiting")
            task = manager._current_task
            deadline = manager._load_loop_deadline()
            assert deadline is not None
            await manager.shutdown()
            await asyncio.wait_for(task, timeout=1)

        assert manager._load_loop_deadline() == deadline

    @pytest.mark.asyncio
    async def test_cookie_validation_failure_cleans_up_without_success(self, config, broadcaster, tmp_path):
        config["general"].update({
            "cookies_source": "file",
            "cookies_file_path": str(tmp_path / "missing.txt"),
        })
        manager = DownloadManager(config, broadcaster, tmp_path / "config.toml")
        await manager.start_once()
        task = manager._current_task
        await task
        assert manager.status["state"] == "idle"
        assert manager.status["last_run"]["hard_errors"] == 1
        calls = broadcaster.broadcast_sync.call_args_list
        assert any(call.args[0] == "ERROR" and "配置校验失败" in call.args[1] for call in calls)
        assert not any(call.args[0] == "SUCCESS" and call.args[1] == "所有任务完成" for call in calls)

    @pytest.mark.asyncio
    async def test_engine_exception_cleans_up_as_error(self, config, broadcaster, tmp_path):
        class BrokenEngine(FakeEngine):
            def run_all(self):
                raise RuntimeError("boom")

        manager = DownloadManager(config, broadcaster, tmp_path / "config.toml")
        with patch("app.download_manager.YtDlpEngine", BrokenEngine):
            await manager.start_once()
            task = manager._current_task
            await task
        assert manager.status["state"] == "idle"
        assert manager.status["last_run"]["hard_errors"] == 1

    @pytest.mark.asyncio
    async def test_stop_calls_engine_and_marks_stopping(self, config, broadcaster, tmp_path):
        manager = DownloadManager(config, broadcaster, tmp_path / "config.toml")
        engine = MagicMock()
        manager._state = "running"
        manager._engine = engine
        manager._stop_event = asyncio.Event()
        assert (await manager.stop())["ok"] is True
        assert manager.status["state"] == "stopping"
        assert manager.status["running"] is True
        assert manager._stop_event.is_set()
        engine.request_stop.assert_called_once()


class TestChannelMerge:
    def test_is_first_merges_by_uuid_after_reorder(self, config, broadcaster, tmp_path):
        config["channels"].append({
            **config["channels"][0],
            "channel_id": CHANNEL_B,
            "folder_name": "B",
            "youtube_id": "b",
        })
        snapshot = copy.deepcopy(config)
        config["channels"].reverse()
        engine = MagicMock()
        engine.get_channel_state.return_value = [
            {**snapshot["channels"][0], "is_first": False},
            snapshot["channels"][1],
        ]
        manager = DownloadManager(config, broadcaster, tmp_path / "config.toml")
        manager._merge_is_first(snapshot, engine)
        by_id = {channel["channel_id"]: channel for channel in config["channels"]}
        assert by_id[CHANNEL_A]["is_first"] is False
        assert by_id[CHANNEL_B]["is_first"] is True

    def test_user_is_first_edit_prevents_snapshot_writeback(self, config, broadcaster, tmp_path):
        snapshot = copy.deepcopy(config)
        config["channels"][0]["is_first"] = False
        engine = MagicMock()
        engine.get_channel_state.return_value = [
            {**snapshot["channels"][0], "is_first": False},
        ]
        manager = DownloadManager(config, broadcaster, tmp_path / "config.toml")
        with patch("app.download_manager.save_config") as save:
            manager._merge_is_first(snapshot, engine)
        save.assert_not_called()


class TestUpdateYtDlp:
    @pytest.mark.asyncio
    async def test_update_rejects_while_waiting(self, config, broadcaster, tmp_path):
        manager = DownloadManager(config, broadcaster, tmp_path / "config.toml")
        manager._state = "waiting"
        result = await manager.update_ytdlp()
        assert result["ok"] is False
        assert "活跃" in result["reason"]
