from __future__ import annotations

import io
import os
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.download_manager import DownloadManager
from app.routes.api import ChannelCreate, ConfigUpdate, build_router
from app.websocket_manager import LogBroadcaster


@pytest.fixture
def base_config():
    return {
        "general": {
            "output_base_path": "/downloads",
            "proxy_url": "",
            "sleep_requests": 5,
            "sleep_time": "5s",
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
        "channels": [],
    }


@pytest.fixture
def manager(base_config, tmp_path):
    mgr = MagicMock(spec=DownloadManager)
    mgr.status = {
        "state": "idle",
        "active": False,
        "running": False,
        "mode": None,
        "current_channel_index": 0,
        "total_channels": 0,
        "current_channel_label": "",
        "progress_percent": 0.0,
        "next_round_seconds": 0,
        "current_run": None,
        "last_run": None,
    }
    mgr.config_lock = threading.RLock()
    mgr.config_path = tmp_path / "config.toml"
    mgr.start_once = AsyncMock(return_value={"ok": True})
    mgr.start_loop = AsyncMock(return_value={"ok": True})
    mgr.start_first_only = AsyncMock(return_value={"ok": True})
    mgr.stop = AsyncMock(return_value={"ok": True})
    mgr.update_ytdlp = AsyncMock(return_value={"ok": True, "output": "updated"})
    return mgr


@pytest.fixture
def client(manager, base_config):
    app = FastAPI()
    app.include_router(build_router(manager, base_config))
    return TestClient(app)


class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestStatus:
    def test_status_returns_manager_status(self, client):
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert "running" in data
        assert data["running"] is False


class TestControl:
    def test_run_once(self, client, manager):
        r = client.post("/api/control/run-once")
        assert r.status_code == 200
        assert manager.start_once.await_count == 1

    def test_run_loop(self, client, manager):
        r = client.post("/api/control/run-loop")
        assert r.status_code == 200
        assert manager.start_loop.await_count == 1

    def test_run_first(self, client, manager):
        r = client.post("/api/control/run-first")
        assert r.status_code == 200
        assert manager.start_first_only.await_count == 1

    def test_stop(self, client, manager):
        r = client.post("/api/control/stop")
        assert r.status_code == 200
        assert manager.stop.await_count == 1

    def test_update_ytdlp(self, client, manager):
        r = client.post("/api/control/update-ytdlp")
        assert r.status_code == 200
        assert manager.update_ytdlp.await_count == 1


class TestChannels:
    def test_list_empty(self, client):
        r = client.get("/api/channels")
        assert r.status_code == 200
        assert r.json()["channels"] == []

    def test_add_channel(self, client):
        r = client.post("/api/channels", json={
            "folder_name": "TestChannel",
            "youtube_id": "@test",
            "vid_type": "videos",
            "dl_type": "audio",
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["channels"][0]["youtube_id"] == "test"
        assert r.json()["channels"][0]["channel_id"]

    def test_client_cannot_set_channel_id(self, client):
        r = client.post("/api/channels", json={
            "channel_id": "client-controlled",
            "folder_name": "TestChannel",
            "youtube_id": "test",
        })
        assert r.json()["channels"][0]["channel_id"] != "client-controlled"

    @pytest.mark.parametrize("youtube_id", ["test", " TEST ", "@TeSt"])
    def test_duplicate_channel_returns_409(self, client, youtube_id):
        client.post("/api/channels", json={
            "folder_name": "First", "youtube_id": "@Test", "enabled": False,
        })
        r = client.post("/api/channels", json={
            "folder_name": "Second", "youtube_id": youtube_id, "enabled": True,
        })
        assert r.status_code == 409
        assert "videos" in r.json()["detail"]

    def test_same_channel_different_video_type_is_allowed(self, client):
        client.post("/api/channels", json={
            "folder_name": "Videos", "youtube_id": "same", "vid_type": "videos",
        })
        r = client.post("/api/channels", json={
            "folder_name": "Streams", "youtube_id": "@SAME", "vid_type": "streams",
        })
        assert r.status_code == 200

    def test_edit_and_delete_channel_by_id(self, client):
        added = client.post("/api/channels", json={
            "folder_name": "Before", "youtube_id": "test",
        }).json()["channels"][0]
        channel_id = added["channel_id"]
        edited = client.put(f"/api/channels/id/{channel_id}", json={
            "folder_name": "After", "youtube_id": "test",
        })
        assert edited.status_code == 200
        assert edited.json()["channels"][0]["folder_name"] == "After"
        assert edited.json()["channels"][0]["channel_id"] == channel_id
        assert client.delete(f"/api/channels/id/{channel_id}").json()["channels"] == []

    def test_add_channel_validates_required_fields(self, client):
        r = client.post("/api/channels", json={"folder_name": ""})
        assert r.status_code == 422

    def test_add_channel_validates_vid_type(self, client):
        r = client.post("/api/channels", json={
            "folder_name": "T",
            "youtube_id": "@t",
            "vid_type": "invalid",
        })
        assert r.status_code == 422

    def test_edit_channel_invalid_index(self, client):
        r = client.put("/api/channels/99", json={
            "folder_name": "T",
            "youtube_id": "@t",
        })
        assert r.status_code == 200
        assert r.json()["ok"] is False

    def test_edit_channel_negative_index(self, client):
        r = client.put("/api/channels/-1", json={
            "folder_name": "T",
            "youtube_id": "@t",
        })
        assert r.status_code == 200
        assert r.json()["ok"] is False

    def test_delete_channel_invalid_index(self, client):
        r = client.delete("/api/channels/99")
        assert r.status_code == 200
        assert r.json()["ok"] is False

    def test_add_channel_then_list(self, client, base_config):
        client.post("/api/channels", json={
            "folder_name": "Ch1",
            "youtube_id": "@ch1",
        })
        r = client.get("/api/channels")
        assert len(r.json()["channels"]) == 1
        assert r.json()["channels"][0]["folder_name"] == "Ch1"


class TestConfig:
    def test_po_token_settings_round_trip(self, client, base_config, tmp_path):
        body = {
            **base_config["general"],
            **base_config["download_limits"],
            "po_token_enabled": False,
            "po_token_base_url": "http://custom-provider:4416",
        }
        from app.config import load_config, save_config
        path = tmp_path / "config.toml"
        with patch("app.config.save_config", side_effect=lambda config, *_: save_config(config, path)):
            response = client.put("/api/config", json=body)
        assert response.status_code == 200
        general = client.get("/api/config").json()["general"]
        assert general["po_token_enabled"] is False
        assert general["po_token_base_url"] == "http://custom-provider:4416"
        assert load_config(path)["general"]["po_token_base_url"] == general["po_token_base_url"]

    @pytest.mark.parametrize("url", [
        "file:///tmp/provider", "http://host;other=value",
        "http://host,other", "http://host\nother",
    ])
    def test_invalid_po_token_url(self, client, base_config, url):
        body = {
            **base_config["general"],
            **base_config["download_limits"],
            "po_token_base_url": url,
        }
        assert client.put("/api/config", json=body).status_code == 422

    def test_get_config(self, client):
        r = client.get("/api/config")
        assert r.status_code == 200
        data = r.json()
        assert "general" in data
        assert "download_limits" in data

    def test_update_config(self, client):
        r = client.put("/api/config", json={
            "output_base_path": "/new/path",
            "proxy_url": "http://test",
            "sleep_requests": 10,
            "sleep_time": "30s",
            "wait_time_minutes": 120,
            "quiet_mode": True,
            "dateafter": "20230101",
            "download_archive": "arch.txt",
            "filename_format": "[%(id)s].%(ext)s",
            "normal_limit": 30,
            "first_run_limit": 500,
            "first_run_timeout": 180,
            "normal_timeout": 30,
            "cookies_file_path": "/app/cookies.txt",
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_update_config_validates_dateafter_pattern(self, client):
        r = client.put("/api/config", json={
            "output_base_path": "/",
            "proxy_url": "",
            "sleep_requests": 5,
            "sleep_time": "5s",
            "wait_time_minutes": 60,
            "quiet_mode": False,
            "dateafter": "invalid",
            "download_archive": "a.txt",
            "filename_format": "[%(title)s]",
            "normal_limit": 10,
            "first_run_limit": 10,
            "first_run_timeout": 60,
            "normal_timeout": 30,
            "cookies_file_path": "/a",
        })
        assert r.status_code == 422

    def test_update_config_validates_dateafter_8digits(self, client):
        r = client.put("/api/config", json={
            "output_base_path": "/",
            "proxy_url": "",
            "sleep_requests": 5,
            "sleep_time": "5s",
            "wait_time_minutes": 60,
            "quiet_mode": False,
            "dateafter": "20000101",
            "download_archive": "a.txt",
            "filename_format": "[%(title)s]",
            "normal_limit": 10,
            "first_run_limit": 10,
            "first_run_timeout": 60,
            "normal_timeout": 30,
            "cookies_file_path": "/a",
        })
        assert r.status_code == 200

    def test_update_config_rejects_negative_sleep_requests(self, client):
        r = client.put("/api/config", json={
            "output_base_path": "/",
            "proxy_url": "",
            "sleep_requests": -1,
            "sleep_time": "5s",
            "wait_time_minutes": 60,
            "quiet_mode": False,
            "dateafter": "20000101",
            "download_archive": "a.txt",
            "filename_format": "[%(title)s]",
            "normal_limit": 10,
            "first_run_limit": 10,
            "first_run_timeout": 60,
            "normal_timeout": 30,
            "cookies_file_path": "/a",
        })
        assert r.status_code == 422

    def test_update_config_rejects_empty_filename_format(self, client):
        r = client.put("/api/config", json={
            "output_base_path": "/",
            "proxy_url": "",
            "sleep_requests": 5,
            "sleep_time": "5s",
            "wait_time_minutes": 60,
            "quiet_mode": False,
            "dateafter": "20000101",
            "download_archive": "a.txt",
            "filename_format": "",
            "normal_limit": 10,
            "first_run_limit": 10,
            "first_run_timeout": 60,
            "normal_timeout": 30,
            "cookies_file_path": "/a",
        })
        assert r.status_code == 422


class TestCookies:
    def test_upload_non_txt_rejected(self, client):
        r = client.post("/api/cookies", files={"file": ("test.json", io.BytesIO(b"{}"), "application/json")})
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert "txt" in r.json()["reason"]

    def test_upload_txt_file_accepted(self, client, base_config):
        content = b"# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tname\tvalue\n"
        tmp_dir = tempfile.mkdtemp()
        base_config["general"]["cookies_file_path"] = os.path.join(tmp_dir, "test_cookies.txt")
        try:
            r = client.post("/api/cookies", files={"file": ("cookies.txt", io.BytesIO(content), "text/plain")})
            assert r.status_code == 200
            assert r.json()["ok"] is True
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestPydanticModels:
    def test_channel_create_required_fields(self):
        with pytest.raises(Exception):
            ChannelCreate()

    def test_channel_create_defaults(self):
        ch = ChannelCreate(folder_name="A", youtube_id="@a")
        assert ch.vid_type == "videos"
        assert ch.dl_type == "audio"
        assert ch.enabled is True
        assert ch.is_regex is False
        assert ch.is_first is True

    def test_config_update_dateafter_regex(self):
        with pytest.raises(Exception):
            ConfigUpdate(
                output_base_path="/",
                proxy_url="",
                sleep_requests=5,
                sleep_time="5s",
                wait_time_minutes=60,
                quiet_mode=False,
                dateafter="bad",
                download_archive="a.txt",
                filename_format="[%(title)s]",
                normal_limit=10,
                first_run_limit=10,
            )

    @pytest.mark.asyncio
    async def test_control_returns_mutex_rejection(self, client, manager):
        manager.start_once.return_value = {"ok": False, "reason": "已有下载任务运行中"}
        r = client.post("/api/control/run-once")
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert "运行中" in r.json()["reason"]


class AsyncMock(MagicMock):
    async def __call__(self, *args, **kwargs):
        return super().__call__(*args, **kwargs)
