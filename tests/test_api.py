from __future__ import annotations

import io
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth import TokenAuth
from app.download_manager import DownloadManager
from app.main import create_app
from app.routes.api import build_router
from app.websocket_manager import LogBroadcaster

TOKEN = "t" * 32
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
CHANNEL_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def base_config():
    return {
        "schema_version": 3,
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
            "cookies_source": "file",
            "cookies_file_path": "/app/config/cookies.txt",
            "cookies_browser": "firefox",
            "cookies_browser_profile": "",
            "cookies_browser_container": "",
            "po_token_enabled": True,
            "po_token_base_url": "http://bgutil-provider:4416",
            "log_max_history": 200,
        },
        "download_limits": {"normal_limit": 20, "first_run_limit": 99999},
        "channels": [],
    }


@pytest.fixture
def manager(tmp_path):
    value = MagicMock(spec=DownloadManager)
    value.status = {"state": "idle", "active": False}
    value.config_lock = threading.RLock()
    value.config_path = tmp_path / "config.toml"
    value.channel_runtimes.return_value = {}
    value.channel_runtime.side_effect = lambda channel_id: {
        "channel_id": channel_id,
        "state": "idle",
        "percent": 0.0,
    }
    value.start_once = AsyncMock(return_value={"ok": True})
    value.start_loop = AsyncMock(return_value={"ok": True})
    value.start_first_only = AsyncMock(return_value={"ok": True})
    value.start_selected = AsyncMock(return_value={"ok": True})
    value.stop = AsyncMock(return_value={"ok": True})
    value.update_ytdlp = AsyncMock(return_value={"ok": True})
    value.resolve_channel = AsyncMock(return_value={"ok": True})
    value.test_channel = AsyncMock(return_value={"ok": True, "message": "ok"})
    return value


@pytest.fixture
def client(manager, base_config):
    app = FastAPI()
    app.include_router(build_router(manager, base_config, TokenAuth(TOKEN)))
    return TestClient(app)


def add_channel(client, **overrides):
    body = {
        "folder_name": "Example",
        "youtube_id": "@example",
        "vid_type": "videos",
        "dl_type": "audio",
        **overrides,
    }
    return client.post("/api/v1/channels", json=body, headers=HEADERS)


def test_api_requires_bearer_token(client):
    response = client.get("/api/v1/channels")
    assert response.status_code == 401


def test_auth_check(client):
    response = client.get("/api/v1/auth/check", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_add_list_patch_and_delete_channel(client):
    added = add_channel(client)
    assert added.status_code == 200
    item = added.json()["items"][0]
    assert item["youtube_id"] == "example"
    assert item["runtime"]["state"] == "idle"

    patched = client.patch(
        f"/api/v1/channels/{item['channel_id']}",
        json={"folder_name": "Renamed", "filter_enabled": True},
        headers=HEADERS,
    )
    assert patched.status_code == 200
    assert patched.json()["items"][0]["folder_name"] == "Renamed"
    assert patched.json()["items"][0]["filter_enabled"] is True

    deleted = client.delete(
        f"/api/v1/channels/{item['channel_id']}",
        headers=HEADERS,
    )
    assert deleted.status_code == 200
    assert deleted.json()["items"] == []


def test_channel_input_accepts_url(client):
    response = add_channel(
        client,
        youtube_id="https://www.youtube.com/@Example/videos",
    )
    item = response.json()["items"][0]
    assert item["youtube_id"] == "Example"
    assert item["source_url"] == "https://www.youtube.com/@Example"


def test_channel_input_preserves_verified_metadata(client):
    response = add_channel(
        client,
        youtube_id="UC12345678901234567890",
        youtube_channel_id="UC12345678901234567890",
        channel_title="Verified Channel",
        resolved_at="2026-09-17T12:00:00Z",
    )
    item = response.json()["items"][0]
    assert item["channel_title"] == "Verified Channel"
    assert item["resolved_at"] == "2026-09-17T12:00:00Z"


@pytest.mark.parametrize("folder", ["../escape", "/absolute", "a/b", "a\\b"])
def test_channel_rejects_path_escape(client, folder):
    response = add_channel(client, folder_name=folder)
    assert response.status_code == 422


def test_duplicate_channel_returns_conflict(client):
    assert add_channel(client).status_code == 200
    response = add_channel(client, folder_name="Other")
    assert response.status_code == 409


def test_handle_and_url_are_duplicates(client):
    assert add_channel(client, youtube_id="@Example").status_code == 200
    response = add_channel(
        client,
        folder_name="Other",
        youtube_id="https://www.youtube.com/@Example/videos",
    )
    assert response.status_code == 409


def test_reorder_requires_complete_unique_permutation(client, base_config):
    first = add_channel(client).json()["items"][0]
    second = add_channel(
        client,
        folder_name="Second",
        youtube_id="@second",
    ).json()["items"][1]

    invalid = client.put(
        "/api/v1/channels/order",
        json={"channel_ids": [first["channel_id"]]},
        headers=HEADERS,
    )
    assert invalid.status_code == 409

    valid = client.put(
        "/api/v1/channels/order",
        json={"channel_ids": [second["channel_id"], first["channel_id"]]},
        headers=HEADERS,
    )
    assert valid.status_code == 200
    assert [item["channel_id"] for item in valid.json()["items"]] == [
        second["channel_id"],
        first["channel_id"],
    ]


def test_bulk_actions_are_atomic(client, manager):
    first = add_channel(client).json()["items"][0]
    second = add_channel(
        client,
        folder_name="Second",
        youtube_id="@second",
    ).json()["items"][1]

    response = client.post(
        "/api/v1/channels/bulk-actions",
        json={
            "action": "set_dl_type",
            "channel_ids": [first["channel_id"], second["channel_id"]],
            "dl_type": "video",
        },
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert all(item["dl_type"] == "video" for item in response.json()["items"])

    missing = client.post(
        "/api/v1/channels/bulk-actions",
        json={
            "action": "enable",
            "channel_ids": [CHANNEL_ID],
            "enabled": True,
        },
        headers=HEADERS,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_bulk_run_rejects_disabled(client, manager):
    item = add_channel(client, enabled=False).json()["items"][0]
    response = client.post(
        "/api/v1/channels/bulk-actions",
        json={"action": "run", "channel_ids": [item["channel_id"]]},
        headers=HEADERS,
    )
    assert response.status_code == 409


def test_config_patch_only_updates_provided_fields(client, base_config):
    response = client.patch(
        "/api/v1/config",
        json={"normal_limit": 7, "proxy_url": "http://proxy:8080"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert base_config["download_limits"]["normal_limit"] == 7
    assert base_config["general"]["proxy_url"] == "http://proxy:8080"
    assert base_config["general"]["sleep_time"] == "5s"


def test_cookie_upload_validates_format_and_limit(client, tmp_path, monkeypatch):
    cookie_path = tmp_path / "cookies.txt"
    monkeypatch.setenv("COOKIES_FILE", str(cookie_path))

    invalid = client.post(
        "/api/v1/cookies",
        files={"file": ("cookies.txt", io.BytesIO(b"not a cookie"), "text/plain")},
        headers=HEADERS,
    )
    assert invalid.status_code == 422

    too_large = client.post(
        "/api/v1/cookies",
        files={"file": ("cookies.txt", io.BytesIO(b"x" * (1024 * 1024 + 1)), "text/plain")},
        headers=HEADERS,
    )
    assert too_large.status_code == 413

    line = b".example.com\tTRUE\t/\tFALSE\t0\tname\tvalue\n"
    valid = client.post(
        "/api/v1/cookies",
        files={"file": ("cookies.txt", io.BytesIO(line), "text/plain")},
        headers=HEADERS,
    )
    assert valid.status_code == 200
    assert cookie_path.exists()


def test_unknown_channel_returns_404(client):
    response = client.patch(
        f"/api/v1/channels/{CHANNEL_ID}",
        json={"enabled": False},
        headers=HEADERS,
    )
    assert response.status_code == 404


def test_websocket_requires_token(manager, base_config):
    app = create_app(
        manager,
        LogBroadcaster(),
        base_config,
        TokenAuth(TOKEN),
    )
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/logs?token=wrong"):
                pass
        assert exc.value.code == 1008
        with client.websocket_connect(f"/ws/logs?token={TOKEN}") as socket:
            socket.close()


def test_security_headers(manager, base_config):
    app = create_app(manager, LogBroadcaster(), base_config, TokenAuth(TOKEN))
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
