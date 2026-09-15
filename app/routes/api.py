from __future__ import annotations

import os
import copy
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from typing_extensions import Literal

if TYPE_CHECKING:
    from app.download_manager import DownloadManager

CHANNEL_VID_TYPE = Literal["videos", "streams", "shorts"]
CHANNEL_DL_TYPE = Literal["audio", "video"]


class ChannelCreate(BaseModel):
    folder_name: str = Field(min_length=1, max_length=200)
    youtube_id: str = Field(min_length=1, max_length=200)
    vid_type: CHANNEL_VID_TYPE = "videos"
    dl_type: CHANNEL_DL_TYPE = "audio"
    enabled: bool = True
    is_regex: bool = False
    is_first: bool = True

    @field_validator("youtube_id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        from app.config import normalize_youtube_id

        normalized = normalize_youtube_id(value)
        if not normalized:
            raise ValueError("YouTube ID 不能为空")
        return normalized


class CookiesText(BaseModel):
    content: str = Field(min_length=1, max_length=500_000)


def _delayed_exit() -> None:
    import time
    time.sleep(0.5)
    os._exit(0)


class ConfigUpdate(BaseModel):
    output_base_path: str = ""
    proxy_url: str = ""
    sleep_requests: int = Field(ge=0, le=120)
    sleep_time: str = "5s"
    wait_time_minutes: int = Field(ge=1, le=99999)
    quiet_mode: bool = False
    dateafter: str = Field(pattern=r"^\d{8}$")
    download_archive: str = "archive.txt"
    filename_format: str = Field(min_length=1)
    normal_limit: int = Field(ge=1, le=99999)
    first_run_limit: int = Field(ge=1, le=99999)
    first_run_timeout: int = Field(ge=1, le=99999, default=360)
    normal_timeout: int = Field(ge=1, le=99999, default=60)
    cookies_file_path: str = "/app/config/cookies.txt"
    cookies_source: str = "file"
    cookies_browser: str = "firefox"
    cookies_browser_profile: str = ""
    cookies_browser_container: str = ""
    po_token_enabled: bool = True
    po_token_base_url: str = Field(
        default="http://bgutil-provider:4416",
        pattern=r"^https?://[^\s;,?#]+$",
        max_length=2048,
    )
    log_max_history: int = Field(ge=10, le=9999, default=200)


def build_router(manager: DownloadManager, config: dict) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _save_locked() -> None:
        from app.config import save_config

        save_config(config, manager.config_path)

    def _channel_conflict(candidate: dict, exclude_id: str | None = None) -> None:
        from app.config import channel_unique_key

        key = channel_unique_key(candidate)
        for channel in config.get("channels", []):
            if channel.get("channel_id") == exclude_id:
                continue
            if channel_unique_key(channel) == key:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f'频道冲突：YouTube ID "{candidate["youtube_id"]}" '
                        f'的 {candidate["vid_type"]} 类型已存在'
                    ),
                )

    def _channel_payload(body: ChannelCreate, channel_id: str | None = None) -> dict:
        return {"channel_id": channel_id or str(uuid4()), **body.model_dump()}

    @router.post("/control/restart")
    async def restart(bg: BackgroundTasks):
        bg.add_task(_delayed_exit)
        return {"ok": True, "msg": "正在重启..."}

    @router.get("/health")
    def health():
        return {"status": "ok"}

    @router.get("/status")
    def get_status():
        return manager.status

    @router.post("/control/run-once")
    async def run_once():
        return await manager.start_once()

    @router.post("/control/run-loop")
    async def run_loop():
        return await manager.start_loop()

    @router.post("/control/run-first")
    async def run_first():
        return await manager.start_first_only()

    @router.post("/control/stop")
    async def stop():
        return await manager.stop()

    @router.post("/control/update-ytdlp")
    async def update_ytdlp():
        return await manager.update_ytdlp()

    @router.get("/channels")
    def list_channels():
        with manager.config_lock:
            return {"channels": copy.deepcopy(config.get("channels", []))}

    @router.post("/channels")
    def add_channel(body: ChannelCreate):
        channel = _channel_payload(body)
        with manager.config_lock:
            _channel_conflict(channel)
            channels = config.setdefault("channels", [])
            channels.append(channel)
            _save_locked()
            return {"ok": True, "channels": copy.deepcopy(channels)}

    @router.put("/channels/id/{channel_id}")
    def edit_channel_by_id(channel_id: str, body: ChannelCreate):
        with manager.config_lock:
            channels = config.get("channels", [])
            for index, existing in enumerate(channels):
                if existing.get("channel_id") == channel_id:
                    channel = _channel_payload(body, channel_id)
                    _channel_conflict(channel, exclude_id=channel_id)
                    channels[index] = channel
                    _save_locked()
                    return {"ok": True, "channels": copy.deepcopy(channels)}
        raise HTTPException(status_code=404, detail="频道 ID 不存在")

    @router.delete("/channels/id/{channel_id}")
    def delete_channel_by_id(channel_id: str):
        with manager.config_lock:
            channels = config.get("channels", [])
            for index, channel in enumerate(channels):
                if channel.get("channel_id") == channel_id:
                    channels.pop(index)
                    _save_locked()
                    return {"ok": True, "channels": copy.deepcopy(channels)}
        raise HTTPException(status_code=404, detail="频道 ID 不存在")

    @router.put("/channels/{index}")
    def edit_channel(index: int, body: ChannelCreate):
        with manager.config_lock:
            channels = config.get("channels", [])
            if index < 0 or index >= len(channels):
                return {"ok": False, "reason": "频道索引无效"}
            channel_id = channels[index].get("channel_id") or str(uuid4())
            channel = _channel_payload(body, channel_id)
            _channel_conflict(channel, exclude_id=channel_id)
            channels[index] = channel
            _save_locked()
            return {"ok": True, "channels": copy.deepcopy(channels)}

    @router.delete("/channels/{index}")
    def delete_channel(index: int):
        with manager.config_lock:
            channels = config.get("channels", [])
            if index < 0 or index >= len(channels):
                return {"ok": False, "reason": "频道索引无效"}
            channels.pop(index)
            _save_locked()
            return {"ok": True, "channels": copy.deepcopy(channels)}

    @router.get("/config")
    def get_config():
        with manager.config_lock:
            return copy.deepcopy(config)

    @router.put("/config")
    def update_config(body: ConfigUpdate):
        with manager.config_lock:
            g = config.setdefault("general", {})
            for field in ("output_base_path", "proxy_url", "sleep_requests", "sleep_time",
                           "wait_time_minutes", "quiet_mode", "dateafter", "download_archive",
                           "filename_format", "first_run_timeout", "normal_timeout",
                           "cookies_file_path", "cookies_source", "cookies_browser",
                           "cookies_browser_profile", "cookies_browser_container",
                           "po_token_enabled", "po_token_base_url",
                           "log_max_history"):
                g[field] = getattr(body, field)
            limits = config.setdefault("download_limits", {})
            limits["normal_limit"] = body.normal_limit
            limits["first_run_limit"] = body.first_run_limit
            _save_locked()
        return {"ok": True}

    @router.get("/cookies/status")
    def cookie_status():
        with manager.config_lock:
            cookies_path = config["general"].get("cookies_file_path", "/app/config/cookies.txt")
        exists = os.path.isfile(cookies_path)
        return {"ok": True, "exists": exists, "path": cookies_path}

    @router.post("/cookies")
    async def upload_cookies(file: UploadFile = File(...)):
        if not file.filename or not file.filename.endswith(".txt"):
            return {"ok": False, "reason": "请上传 .txt 格式的 cookies 文件"}
        content = await file.read()
        with manager.config_lock:
            cookies_path = config["general"].get("cookies_file_path", "/app/config/cookies.txt")
        cookies_dir = os.path.dirname(cookies_path)
        if cookies_dir:
            os.makedirs(cookies_dir, exist_ok=True)
        with open(cookies_path, "wb") as f:
            f.write(content)
        return {"ok": True, "path": cookies_path}

    @router.post("/cookies/text")
    def save_cookies_text(body: CookiesText):
        with manager.config_lock:
            cookies_path = config["general"].get("cookies_file_path", "/app/config/cookies.txt")
        cookies_dir = os.path.dirname(cookies_path)
        if cookies_dir:
            os.makedirs(cookies_dir, exist_ok=True)
        with open(cookies_path, "w", encoding="utf-8") as f:
            f.write(body.content)
        return {"ok": True, "path": cookies_path}

    return router
