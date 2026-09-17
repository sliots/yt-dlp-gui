from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

ASMR_TITLE_FILTER = r"(?i)(ASMR|安眠|KU100|asmr|Asmr)"

ChannelVideoType = Literal["videos", "streams", "shorts"]
ChannelDownloadType = Literal["audio", "video"]
ChannelRuntimeState = Literal[
    "idle",
    "queued",
    "running",
    "success",
    "warning",
    "error",
    "stopped",
    "disabled",
]
RunOutcome = Literal["success", "partial", "failed", "stopped"]


def validate_safe_folder_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("文件夹名称不能为空")
    if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError("文件夹名称必须是单一目录名")
    if len(value) > 200:
        raise ValueError("文件夹名称不能超过 200 个字符")
    return value


def validate_filename_template(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("文件名格式不能为空")
    if "/" in value or "\\" in value or "\x00" in value:
        raise ValueError("文件名格式不能包含目录分隔符")
    if len(value) > 500:
        raise ValueError("文件名格式不能超过 500 个字符")
    return value


def normalize_youtube_input(value: str) -> str:
    return value.strip().removeprefix("@")


def parse_channel_input(value: str) -> dict[str, str | None]:
    raw = value.strip()
    if not raw:
        raise ValueError("YouTube 频道不能为空")

    result: dict[str, str | None] = {
        "youtube_id": normalize_youtube_input(raw),
        "youtube_channel_id": None,
        "source_url": None,
    }

    candidate = raw if "://" in raw else f"https://{raw}" if raw.startswith("www.") else ""
    if candidate:
        parsed = urlparse(candidate)
        host = parsed.netloc.lower().removeprefix("www.")
        if host not in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
            raise ValueError("仅支持 YouTube 频道地址")
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            raise ValueError("频道地址缺少频道标识")

        if (
            parts[0] == "channel"
            and len(parts) >= 2
            and re.fullmatch(r"UC[A-Za-z0-9_-]{20,}", parts[1])
        ):
            result["youtube_id"] = parts[1]
            result["youtube_channel_id"] = parts[1]
            result["source_url"] = f"https://www.youtube.com/channel/{parts[1]}"
        elif parts[0].startswith("@"):
            handle = normalize_youtube_input(parts[0])
            result["youtube_id"] = handle
            result["source_url"] = f"https://www.youtube.com/@{handle}"
        elif parts[0] in {"c", "user"} and len(parts) >= 2:
            result["youtube_id"] = parts[1]
            result["source_url"] = f"https://www.youtube.com/{parts[0]}/{parts[1]}"
        else:
            raise ValueError("无法识别 YouTube 频道地址")
    else:
        youtube_id = str(result["youtube_id"] or "")
        if re.fullmatch(r"UC[A-Za-z0-9_-]{20,}", youtube_id):
            result["youtube_channel_id"] = youtube_id
            result["source_url"] = f"https://www.youtube.com/channel/{youtube_id}"
        elif not re.fullmatch(r"[\w.-]+", youtube_id):
            raise ValueError("频道 Handle 只能包含字母、数字、点、下划线和连字符")

    return result


class Channel(BaseModel):
    channel_id: str = Field(default_factory=lambda: str(uuid4()))
    folder_name: str
    youtube_id: str
    youtube_channel_id: str | None = None
    channel_title: str | None = None
    resolved_at: datetime | None = None
    source_url: str | None = None
    vid_type: ChannelVideoType = "videos"
    dl_type: ChannelDownloadType = "audio"
    enabled: bool = True
    is_first: bool = True
    filter_enabled: bool = False
    title_filter: str = ""

    @field_validator("channel_id")
    @classmethod
    def validate_channel_id(cls, value: str) -> str:
        try:
            UUID(value)
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("channel_id 必须是 UUID") from exc
        return value

    @field_validator("folder_name")
    @classmethod
    def validate_folder(cls, value: str) -> str:
        return validate_safe_folder_name(value)

    @field_validator("youtube_id")
    @classmethod
    def validate_youtube_id(cls, value: str) -> str:
        normalized = normalize_youtube_input(value)
        if not normalized:
            raise ValueError("YouTube 频道不能为空")
        return normalized

    @field_validator("title_filter")
    @classmethod
    def validate_title_filter(cls, value: str) -> str:
        value = value.strip()
        if len(value) > 1000:
            raise ValueError("标题过滤规则不能超过 1000 个字符")
        if value:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"标题过滤正则无效: {exc}") from exc
        return value

    @model_validator(mode="after")
    def validate_filter(self):
        if self.filter_enabled and not self.title_filter:
            self.title_filter = ASMR_TITLE_FILTER
        return self

    @property
    def channel_url(self) -> str:
        base = self.source_url
        if not base:
            if self.youtube_channel_id:
                base = f"https://www.youtube.com/channel/{self.youtube_channel_id}"
            else:
                base = f"https://www.youtube.com/@{self.youtube_id}"
        return f"{base.rstrip('/')}/{self.vid_type}"

    @property
    def output_folder(self) -> Path:
        return Path(self.folder_name)


class DownloadLimits(BaseModel):
    normal_limit: int = Field(default=20, ge=1, le=99999)
    first_run_limit: int = Field(default=99999, ge=1, le=99999)


class GeneralSettings(BaseModel):
    output_base_path: str = "/downloads"
    proxy_url: str = ""
    sleep_requests: int = Field(default=5, ge=0, le=120)
    sleep_time: str = "5s"
    wait_time_minutes: int = Field(default=360, ge=1, le=99999)
    quiet_mode: bool = False
    dateafter: str = Field(default="20000101", pattern=r"^\d{8}$")
    download_archive: str = "archive.txt"
    filename_format: str = (
        "[%(upload_date>%Y-%m-%d)s]%(title).160B [%(id)s].%(ext)s"
    )
    first_run_timeout: int = Field(default=360, ge=1, le=99999)
    normal_timeout: int = Field(default=60, ge=1, le=99999)
    cookies_source: Literal["file", "browser"] = "file"
    cookies_file_path: str = "/app/config/cookies.txt"
    cookies_browser: str = "firefox"
    cookies_browser_profile: str = ""
    cookies_browser_container: str = ""
    po_token_enabled: bool = True
    po_token_base_url: str = Field(
        default="http://bgutil-provider:4416",
        pattern=r"^https?://[^\s;,?#]+$",
        max_length=2048,
    )
    log_max_history: int = Field(default=200, ge=10, le=9999)

    @field_validator("filename_format")
    @classmethod
    def validate_filename_format(cls, value: str) -> str:
        return validate_filename_template(value)

    @field_validator("sleep_time")
    @classmethod
    def validate_sleep_time(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"\d+(?:\.\d+)?[smh]?", value):
            raise ValueError("休眠时间格式应为 5、5s、1m 或 0.5h")
        return value


class AppConfig(BaseModel):
    schema_version: int = 3
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    download_limits: DownloadLimits = Field(default_factory=DownloadLimits)
    channels: list[Channel] = Field(default_factory=list)


class ChannelRuntime(BaseModel):
    channel_id: str
    state: ChannelRuntimeState = "idle"
    percent: float = 0.0
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_outcome: str | None = None
    last_error: str = ""
    downloaded_files: int = 0
    archive_skipped: int = 0
    filtered_skipped: int = 0
    member_skipped: int = 0
    duration_seconds: float = 0.0
    last_test_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_message: str = ""


class RunSummary(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float = 0.0
    mode: str
    total_channels: int = 0
    processed_channels: int = 0
    completed_channels: int = 0
    warning_channels: int = 0
    downloaded_files: int = 0
    archive_skipped: int = 0
    filtered_skipped: int = 0
    member_skipped: int = 0
    hard_errors: int = 0
    stopped: bool = False
    outcome: RunOutcome = "success"
