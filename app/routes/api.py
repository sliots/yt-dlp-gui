from __future__ import annotations

import copy
import os
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator, model_validator

from app.auth import TokenAuth
from app.config import channel_unique_key, save_config
from app.models import (
    ASMR_TITLE_FILTER,
    AppConfig,
    Channel,
    ChannelDownloadType,
    ChannelVideoType,
    parse_channel_input,
    validate_filename_template,
    validate_safe_folder_name,
)

if TYPE_CHECKING:
    from app.download_manager import DownloadManager

MAX_COOKIE_SIZE = 1024 * 1024


class ChannelInput(BaseModel):
    folder_name: str
    youtube_id: str
    vid_type: ChannelVideoType = "videos"
    dl_type: ChannelDownloadType = "audio"
    enabled: bool = True
    is_first: bool = True
    filter_enabled: bool = False
    title_filter: str = ""
    youtube_channel_id: str | None = None
    channel_title: str | None = None
    resolved_at: datetime | None = None

    @field_validator("folder_name")
    @classmethod
    def validate_folder(cls, value: str) -> str:
        return validate_safe_folder_name(value)

    @field_validator("youtube_id")
    @classmethod
    def normalize_input(cls, value: str) -> str:
        parse_channel_input(value)
        return value.strip()

    @model_validator(mode="after")
    def validate_filter(self):
        if self.filter_enabled and not self.title_filter:
            self.title_filter = ASMR_TITLE_FILTER
        if self.title_filter:
            Channel.model_validate(
                {
                    "folder_name": self.folder_name,
                    "youtube_id": self.youtube_id,
                    "vid_type": self.vid_type,
                    "dl_type": self.dl_type,
                    "filter_enabled": self.filter_enabled,
                    "title_filter": self.title_filter,
                }
            )
        return self


class ChannelPatch(BaseModel):
    folder_name: str | None = None
    youtube_id: str | None = None
    vid_type: ChannelVideoType | None = None
    dl_type: ChannelDownloadType | None = None
    enabled: bool | None = None
    is_first: bool | None = None
    filter_enabled: bool | None = None
    title_filter: str | None = None

    @field_validator("folder_name")
    @classmethod
    def validate_folder(cls, value: str | None) -> str | None:
        return validate_safe_folder_name(value) if value is not None else None

    @field_validator("youtube_id")
    @classmethod
    def normalize_input(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parse_channel_input(value)
        return value.strip()

    @field_validator("title_filter")
    @classmethod
    def normalize_filter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) > 1000:
            raise ValueError("标题过滤规则不能超过 1000 个字符")
        return value.strip()


class OrderRequest(BaseModel):
    channel_ids: list[str]


class BulkAction(BaseModel):
    action: Literal[
        "enable",
        "disable",
        "delete",
        "set_vid_type",
        "set_dl_type",
        "set_is_first",
        "set_filter",
        "run",
    ]
    channel_ids: list[str]
    enabled: bool | None = None
    vid_type: ChannelVideoType | None = None
    dl_type: ChannelDownloadType | None = None
    is_first: bool | None = None
    filter_enabled: bool | None = None
    title_filter: str | None = None

    @model_validator(mode="after")
    def validate_action_fields(self):
        if not self.channel_ids:
            raise ValueError("channel_ids 不能为空")
        if len(self.channel_ids) != len(set(self.channel_ids)):
            raise ValueError("channel_ids 不能重复")
        required = {
            "enable": self.enabled,
            "disable": self.enabled,
            "set_vid_type": self.vid_type,
            "set_dl_type": self.dl_type,
            "set_is_first": self.is_first,
            "set_filter": self.filter_enabled,
        }
        if self.action in required and required[self.action] is None:
            raise ValueError(f"{self.action} 缺少目标值")
        return self


class ConfigPatch(BaseModel):
    proxy_url: str | None = None
    sleep_requests: int | None = Field(default=None, ge=0, le=120)
    sleep_time: str | None = None
    wait_time_minutes: int | None = Field(default=None, ge=1, le=99999)
    quiet_mode: bool | None = None
    dateafter: str | None = Field(default=None, pattern=r"^\d{8}$")
    filename_format: str | None = None
    first_run_timeout: int | None = Field(default=None, ge=1, le=99999)
    normal_timeout: int | None = Field(default=None, ge=1, le=99999)
    cookies_source: Literal["file", "browser"] | None = None
    cookies_browser: str | None = None
    cookies_browser_profile: str | None = None
    cookies_browser_container: str | None = None
    po_token_enabled: bool | None = None
    po_token_base_url: str | None = Field(
        default=None,
        pattern=r"^https?://[^\s;,?#]+$",
        max_length=2048,
    )
    log_max_history: int | None = Field(default=None, ge=10, le=9999)
    normal_limit: int | None = Field(default=None, ge=1, le=99999)
    first_run_limit: int | None = Field(default=None, ge=1, le=99999)

    @field_validator("filename_format")
    @classmethod
    def validate_filename(cls, value: str | None) -> str | None:
        return validate_filename_template(value) if value is not None else None


class CookiesText(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_COOKIE_SIZE)


def _delayed_exit() -> None:
    time.sleep(0.5)
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except OSError:
        os._exit(0)


def _cookie_content_valid(content: bytes) -> bool:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return False
    return any(len(line.split("\t")) >= 7 for line in lines)


def _public_channel(channel: dict) -> dict:
    value = copy.deepcopy(channel)
    model = Channel.model_validate(value)
    value["channel_url"] = model.channel_url
    return value


def build_router(
    manager: DownloadManager,
    config: dict,
    auth: TokenAuth,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1",
        dependencies=[Depends(auth.require_header)],
    )

    def _save_locked() -> None:
        save_config(config, manager.config_path)

    def _channels() -> list[dict]:
        return config.setdefault("channels", [])

    def _find(channel_id: str) -> tuple[int, dict]:
        for index, channel in enumerate(_channels()):
            if channel.get("channel_id") == channel_id:
                return index, channel
        raise HTTPException(status_code=404, detail="频道 ID 不存在")

    def _conflict(candidate: dict, exclude_id: str | None = None) -> None:
        key = channel_unique_key(candidate)
        for channel in _channels():
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

    def _folder_conflicts(channels: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for channel in channels:
            folder = str(channel.get("folder_name", ""))
            counts[folder] = counts.get(folder, 0) + 1
        return {folder: count for folder, count in counts.items() if count > 1}

    def _channels_response() -> dict:
        channels = _channels()
        items = []
        for channel in channels:
            channel_id = str(channel.get("channel_id"))
            items.append(
                {
                    **_public_channel(channel),
                    "runtime": manager.channel_runtime(channel_id),
                    "output_path": str(
                        Path(os.getenv("DOWNLOAD_ROOT", "/downloads"))
                        / str(channel.get("folder_name", ""))
                    ),
                }
            )
        return {
            "items": items,
            "summary": {
                "total": len(items),
                "enabled": sum(bool(item.get("enabled")) for item in items),
                "running": sum(
                    item["runtime"].get("state") == "running" for item in items
                ),
                "warnings": sum(
                    item["runtime"].get("state") in {"warning", "error"}
                    for item in items
                ),
                "folder_conflicts": _folder_conflicts(channels),
            },
        }

    def _new_channel(body: ChannelInput, channel_id: str | None = None) -> dict:
        parsed = parse_channel_input(body.youtube_id)
        if body.youtube_channel_id:
            parsed["youtube_channel_id"] = body.youtube_channel_id
        model = Channel(
            channel_id=channel_id or str(uuid4()),
            folder_name=body.folder_name,
            youtube_id=str(parsed["youtube_id"]),
            youtube_channel_id=parsed["youtube_channel_id"],
            channel_title=body.channel_title,
            resolved_at=body.resolved_at,
            source_url=parsed["source_url"],
            vid_type=body.vid_type,
            dl_type=body.dl_type,
            enabled=body.enabled,
            is_first=body.is_first,
            filter_enabled=body.filter_enabled,
            title_filter=body.title_filter,
        )
        return model.model_dump(mode="json")

    @router.get("/auth/check")
    def auth_check():
        return {"ok": True}

    @router.get("/status")
    def get_status():
        return manager.status

    @router.post("/control/restart")
    async def restart(bg: BackgroundTasks):
        bg.add_task(_delayed_exit)
        return {"ok": True, "msg": "正在重启..."}

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
            return _channels_response()

    @router.post("/channels")
    def add_channel(body: ChannelInput):
        with manager.config_lock:
            channel = _new_channel(body)
            _conflict(channel)
            _channels().append(channel)
            _save_locked()
        return {"ok": True, **_channels_response()}

    @router.post("/channels/resolve")
    async def resolve_channel_input(body: ChannelInput):
        candidate = _new_channel(body)
        return await manager.resolve_channel(candidate)

    @router.put("/channels/order")
    def reorder_channels(body: OrderRequest):
        if len(body.channel_ids) != len(set(body.channel_ids)):
            raise HTTPException(status_code=422, detail="频道排序包含重复 ID")
        with manager.config_lock:
            by_id = {str(channel["channel_id"]): channel for channel in _channels()}
            if set(body.channel_ids) != set(by_id):
                raise HTTPException(status_code=409, detail="排序必须包含全部频道且不能遗漏")
            config["channels"] = [by_id[channel_id] for channel_id in body.channel_ids]
            _save_locked()
        return {"ok": True, **_channels_response()}

    @router.post("/channels/bulk-actions")
    async def bulk_actions(body: BulkAction):
        with manager.config_lock:
            by_id = {str(channel["channel_id"]): channel for channel in _channels()}
            missing = [channel_id for channel_id in body.channel_ids if channel_id not in by_id]
            if missing:
                raise HTTPException(status_code=404, detail=f"频道不存在：{', '.join(missing)}")
            if body.action == "run":
                disabled = any(
                    not by_id[channel_id].get("enabled", True)
                    for channel_id in body.channel_ids
                )
                if disabled:
                    raise HTTPException(status_code=409, detail="不能运行已禁用频道")
                return await manager.start_selected(body.channel_ids)

            staged = copy.deepcopy(config)
            staged_by_id = {
                str(channel["channel_id"]): channel for channel in staged["channels"]
            }
            if body.action == "delete":
                staged["channels"] = [
                    channel
                    for channel in staged["channels"]
                    if str(channel["channel_id"]) not in set(body.channel_ids)
                ]
            else:
                for channel_id in body.channel_ids:
                    channel = staged_by_id[channel_id]
                    if body.action == "enable":
                        channel["enabled"] = True
                    elif body.action == "disable":
                        channel["enabled"] = False
                    elif body.action == "set_vid_type":
                        channel["vid_type"] = body.vid_type
                    elif body.action == "set_dl_type":
                        channel["dl_type"] = body.dl_type
                    elif body.action == "set_is_first":
                        channel["is_first"] = body.is_first
                    elif body.action == "set_filter":
                        channel["filter_enabled"] = body.filter_enabled
                        channel["title_filter"] = (
                            body.title_filter or ASMR_TITLE_FILTER
                            if body.filter_enabled
                            else ""
                        )
                AppConfig.model_validate(staged)
            config.clear()
            config.update(staged)
            _save_locked()
        return {"ok": True, **_channels_response()}

    @router.patch("/channels/{channel_id}")
    def edit_channel(channel_id: str, body: ChannelPatch):
        with manager.config_lock:
            index, existing = _find(channel_id)
            changes = body.model_dump(exclude_unset=True)
            if "youtube_id" in changes:
                parsed = parse_channel_input(str(changes["youtube_id"]))
                changes["youtube_id"] = parsed["youtube_id"]
                changes["youtube_channel_id"] = parsed["youtube_channel_id"]
                changes["source_url"] = parsed["source_url"]
                if parsed["youtube_channel_id"] and parsed["youtube_channel_id"] != existing.get(
                    "youtube_channel_id"
                ):
                    changes["channel_title"] = None
                    changes["resolved_at"] = None
            candidate = Channel.model_validate({**existing, **changes}).model_dump(mode="json")
            _conflict(candidate, exclude_id=channel_id)
            channels = copy.deepcopy(_channels())
            channels[index] = candidate
            config["channels"] = channels
            _save_locked()
        return {"ok": True, **_channels_response()}

    @router.delete("/channels/{channel_id}")
    def delete_channel(channel_id: str):
        with manager.config_lock:
            _find(channel_id)
            config["channels"] = [
                channel
                for channel in _channels()
                if channel.get("channel_id") != channel_id
            ]
            _save_locked()
        return {"ok": True, **_channels_response()}

    @router.post("/channels/{channel_id}/resolve")
    async def resolve_channel(channel_id: str):
        with manager.config_lock:
            _, channel = _find(channel_id)
            candidate = copy.deepcopy(channel)
        result = await manager.resolve_channel(candidate)
        if not result.get("ok"):
            return result
        with manager.config_lock:
            index, existing = _find(channel_id)
            updated = {
                **existing,
                "youtube_channel_id": result["youtube_channel_id"],
                "channel_title": result.get("channel_title"),
                "resolved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _conflict(updated, exclude_id=channel_id)
            config["channels"][index] = updated
            _save_locked()
        return {"ok": True, **result, "channel": _public_channel(updated)}

    @router.post("/channels/{channel_id}/test")
    async def test_channel(channel_id: str):
        with manager.config_lock:
            _, channel = _find(channel_id)
            candidate = copy.deepcopy(channel)
        result = await manager.test_channel(candidate)
        return result

    @router.get("/config")
    def get_config():
        with manager.config_lock:
            value = copy.deepcopy(config)
        value["general"]["output_base_path"] = os.getenv("DOWNLOAD_ROOT", "/downloads")
        value["general"]["download_archive"] = os.getenv("DOWNLOAD_ARCHIVE", "archive.txt")
        value["general"]["cookies_file_path"] = os.getenv(
            "COOKIES_FILE",
            str(manager.config_path.parent / "cookies.txt"),
        )
        return value

    @router.patch("/config")
    def update_config(body: ConfigPatch):
        with manager.config_lock:
            staged = copy.deepcopy(config)
            updates = body.model_dump(exclude_unset=True)
            limits = staged.setdefault("download_limits", {})
            for field in ("normal_limit", "first_run_limit"):
                if field in updates:
                    limits[field] = updates.pop(field)
            staged.setdefault("general", {}).update(updates)
            AppConfig.model_validate(staged)
            config.clear()
            config.update(staged)
            _save_locked()
        return {"ok": True}

    @router.get("/cookies/status")
    def cookie_status():
        cookies_path = Path(
            os.getenv(
                "COOKIES_FILE",
                str(manager.config_path.parent / "cookies.txt"),
            )
        )
        return {
            "ok": True,
            "exists": cookies_path.is_file(),
            "path": str(cookies_path),
        }

    @router.post("/cookies")
    async def upload_cookies(file: UploadFile = File(...)):
        if not file.filename or not file.filename.lower().endswith(".txt"):
            raise HTTPException(status_code=422, detail="请上传 .txt 格式的 cookies 文件")
        content = bytearray()
        while chunk := await file.read(64 * 1024):
            content.extend(chunk)
            if len(content) > MAX_COOKIE_SIZE:
                raise HTTPException(status_code=413, detail="cookies 文件不能超过 1 MiB")
        if not _cookie_content_valid(bytes(content)):
            raise HTTPException(status_code=422, detail="cookies 文件不是有效的 Netscape 格式")
        cookies_path = Path(
            os.getenv(
                "COOKIES_FILE",
                str(manager.config_path.parent / "cookies.txt"),
            )
        )
        cookies_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cookies_path.with_suffix(".txt.tmp")
        temp_path.write_bytes(content)
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        os.replace(temp_path, cookies_path)
        return {"ok": True, "path": str(cookies_path)}

    @router.post("/cookies/text")
    def save_cookies_text(body: CookiesText):
        content = body.content.encode("utf-8")
        if len(content) > MAX_COOKIE_SIZE:
            raise HTTPException(status_code=413, detail="cookies 内容不能超过 1 MiB")
        if not _cookie_content_valid(content):
            raise HTTPException(status_code=422, detail="cookies 内容不是有效的 Netscape 格式")
        cookies_path = Path(
            os.getenv(
                "COOKIES_FILE",
                str(manager.config_path.parent / "cookies.txt"),
            )
        )
        cookies_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cookies_path.with_suffix(".txt.tmp")
        temp_path.write_bytes(content)
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        os.replace(temp_path, cookies_path)
        return {"ok": True, "path": str(cookies_path)}

    return router
