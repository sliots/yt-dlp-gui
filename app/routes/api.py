from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fastapi import APIRouter, BackgroundTasks, File, UploadFile
from pydantic import BaseModel, Field
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
    log_max_history: int = Field(ge=10, le=9999, default=200)


def build_router(manager: DownloadManager, config: dict) -> APIRouter:
    router = APIRouter(prefix="/api")

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
        return {"channels": config.get("channels", [])}

    @router.post("/channels")
    def add_channel(body: ChannelCreate):
        channels = config.setdefault("channels", [])
        channels.append(body.model_dump())
        from app.config import save_config
        save_config(config)
        return {"ok": True, "channels": channels}

    @router.put("/channels/{index}")
    def edit_channel(index: int, body: ChannelCreate):
        channels = config.get("channels", [])
        if index < 0 or index >= len(channels):
            return {"ok": False, "reason": "频道索引无效"}
        channels[index] = body.model_dump()
        from app.config import save_config
        save_config(config)
        return {"ok": True, "channels": channels}

    @router.delete("/channels/{index}")
    def delete_channel(index: int):
        channels = config.get("channels", [])
        if index < 0 or index >= len(channels):
            return {"ok": False, "reason": "频道索引无效"}
        channels.pop(index)
        from app.config import save_config
        save_config(config)
        return {"ok": True, "channels": channels}

    @router.get("/config")
    def get_config():
        return config

    @router.put("/config")
    def update_config(body: ConfigUpdate):
        g = config.setdefault("general", {})
        for field in ("output_base_path", "proxy_url", "sleep_requests", "sleep_time",
                       "wait_time_minutes", "quiet_mode", "dateafter", "download_archive",
                       "filename_format", "first_run_timeout", "normal_timeout",
                       "cookies_file_path", "cookies_source", "cookies_browser",
                       "cookies_browser_profile", "cookies_browser_container",
                       "log_max_history"):
            g[field] = getattr(body, field)
        l = config.setdefault("download_limits", {})
        l["normal_limit"] = body.normal_limit
        l["first_run_limit"] = body.first_run_limit
        from app.config import save_config
        save_config(config)
        return {"ok": True}

    @router.get("/cookies/status")
    def cookie_status():
        cookies_path = config["general"].get("cookies_file_path", "/app/config/cookies.txt")
        exists = os.path.isfile(cookies_path)
        return {"ok": True, "exists": exists, "path": cookies_path}

    @router.post("/cookies")
    async def upload_cookies(file: UploadFile = File(...)):
        if not file.filename or not file.filename.endswith(".txt"):
            return {"ok": False, "reason": "请上传 .txt 格式的 cookies 文件"}
        content = await file.read()
        cookies_path = config["general"].get("cookies_file_path", "/app/config/cookies.txt")
        cookies_dir = os.path.dirname(cookies_path)
        if cookies_dir:
            os.makedirs(cookies_dir, exist_ok=True)
        with open(cookies_path, "wb") as f:
            f.write(content)
        return {"ok": True, "path": cookies_path}

    @router.post("/cookies/text")
    def save_cookies_text(body: CookiesText):
        cookies_path = config["general"].get("cookies_file_path", "/app/config/cookies.txt")
        cookies_dir = os.path.dirname(cookies_path)
        if cookies_dir:
            os.makedirs(cookies_dir, exist_ok=True)
        with open(cookies_path, "w", encoding="utf-8") as f:
            f.write(body.content)
        return {"ok": True, "path": cookies_path}

    return router
