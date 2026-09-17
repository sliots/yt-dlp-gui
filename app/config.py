from __future__ import annotations

import copy
import logging
import os
import re
import shutil
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import tomli_w
from pydantic import ValidationError

from app.models import ASMR_TITLE_FILTER, AppConfig, parse_channel_input

DEFAULT_CONFIG_PATH = "config/config.toml"
SCHEMA_VERSION = 3

logger = logging.getLogger("yt_dlp_gui")

DEFAULT_CONFIG: dict[str, Any] = AppConfig().model_dump(mode="json")
DEFAULT_CHANNEL: dict[str, Any] = {
    "channel_id": "",
    "folder_name": "",
    "youtube_id": "",
    "youtube_channel_id": None,
    "channel_title": None,
    "resolved_at": None,
    "source_url": None,
    "vid_type": "videos",
    "dl_type": "audio",
    "enabled": True,
    "is_first": True,
    "filter_enabled": False,
    "title_filter": "",
}


def normalize_youtube_id(value: str) -> str:
    return parse_channel_input(value)["youtube_id"] or ""


def channel_unique_key(channel: dict) -> tuple[str, str]:
    canonical = str(channel.get("youtube_channel_id") or "").strip().casefold()
    if canonical:
        identity = f"uc:{canonical}"
    else:
        source_url = str(channel.get("source_url") or "").strip().rstrip("/").casefold()
        if source_url and any(part in source_url for part in ("/c/", "/user/")):
            identity = f"url:{source_url}"
        else:
            youtube_id = normalize_youtube_id(str(channel.get("youtube_id", "")))
            identity = f"handle:{youtube_id.casefold()}"
    return identity, str(channel.get("vid_type", "videos"))


def _deep_merge(base: dict, overlay: dict) -> None:
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _valid_channel_id(value: Any) -> bool:
    try:
        UUID(str(value))
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def _migrate_channel(raw: dict) -> dict:
    channel = copy.deepcopy(raw)
    source = str(channel.get("youtube_id") or channel.get("folder_name") or "")
    try:
        parsed = parse_channel_input(source)
    except ValueError:
        safe = re.sub(r"[^\w.-]+", "-", source.removeprefix("@")).strip(".-")
        parsed = {
            "youtube_id": safe or f"channel-{str(uuid4())[:8]}",
            "youtube_channel_id": None,
            "source_url": None,
        }
    for key in ("youtube_channel_id", "source_url", "channel_title", "resolved_at"):
        if channel.get(key):
            parsed[key] = channel[key]
    channel.update({key: value for key, value in parsed.items() if value is not None})

    if channel.pop("is_regex", False):
        channel["filter_enabled"] = True
        channel["title_filter"] = channel.get("title_filter") or ASMR_TITLE_FILTER
    channel.setdefault("filter_enabled", False)
    channel.setdefault("title_filter", "")

    channel_id = str(channel.get("channel_id", ""))
    if not _valid_channel_id(channel_id):
        channel["channel_id"] = str(uuid4())
    channel.setdefault("folder_name", "")
    channel.setdefault("vid_type", "videos")
    channel.setdefault("dl_type", "audio")
    channel.setdefault("enabled", True)
    channel.setdefault("is_first", True)
    return channel


def _migrate_channels(raw_channels: list[Any]) -> tuple[list[dict], bool, int]:
    normalized = [_migrate_channel(ch) for ch in raw_channels if isinstance(ch, dict)]
    changed = len(normalized) != len(raw_channels) or normalized != raw_channels
    grouped: dict[tuple[str, str], list[dict]] = {}
    order: list[tuple[str, str]] = []

    for channel in normalized:
        key = channel_unique_key(channel)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(channel)

    merged: list[dict] = []
    removed = 0
    for key in order:
        group = grouped[key]
        survivor = next((ch for ch in group if ch.get("enabled", True)), group[0]).copy()
        if len(group) > 1:
            survivor["enabled"] = any(ch.get("enabled", True) for ch in group)
            survivor["is_first"] = any(ch.get("is_first", False) for ch in group)
            removed += len(group) - 1
            changed = True
        merged.append(survivor)

    seen_ids: set[str] = set()
    for channel in merged:
        channel_id = str(channel.get("channel_id", ""))
        if channel_id in seen_ids or not _valid_channel_id(channel_id):
            channel["channel_id"] = str(uuid4())
            changed = True
        seen_ids.add(str(channel["channel_id"]))

    return merged, changed, removed


def _backup_path(path: Path, label: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return path.with_name(f"{path.name}.{label}-{stamp}.bak")


def _copy_backup(source: Path, target: Path) -> None:
    shutil.copyfile(source, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def _apply_runtime_paths(config: dict, path: Path) -> dict:
    general = config.setdefault("general", {})
    general["output_base_path"] = os.getenv("DOWNLOAD_ROOT", "/downloads")
    general["download_archive"] = os.getenv("DOWNLOAD_ARCHIVE", "archive.txt")
    general["cookies_file_path"] = os.getenv(
        "COOKIES_FILE",
        str(path.parent / "cookies.txt"),
    )
    return config


def _validated_dict(config: dict, path: Path) -> dict:
    validated = AppConfig.model_validate(config).model_dump(mode="json")
    validated["schema_version"] = SCHEMA_VERSION
    return _apply_runtime_paths(validated, path)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = _validated_dict(copy.deepcopy(DEFAULT_CONFIG), config_path)
        save_config(config, config_path)
        return config

    try:
        with open(config_path, "rb") as file:
            raw = tomllib.load(file)
        if not isinstance(raw, dict):
            raise ValueError("配置根节点必须是 TOML 表")
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        backup = _backup_path(config_path, "corrupt")
        _copy_backup(config_path, backup)
        logger.error("配置读取失败，已备份到 %s: %s", backup, exc)
        config = _validated_dict(copy.deepcopy(DEFAULT_CONFIG), config_path)
        save_config(config, config_path)
        return config

    merged = copy.deepcopy(DEFAULT_CONFIG)
    _deep_merge(merged, raw)
    if not isinstance(merged.get("channels"), list):
        merged["channels"] = []

    channels, migrated, removed = _migrate_channels(merged["channels"])
    merged["channels"] = channels

    try:
        validated = _validated_dict(merged, config_path)
    except ValidationError as exc:
        backup = _backup_path(config_path, "invalid")
        _copy_backup(config_path, backup)
        logger.error("配置校验失败，已备份到 %s: %s", backup, exc)
        validated = _validated_dict(copy.deepcopy(DEFAULT_CONFIG), config_path)
        save_config(validated, config_path)
        return validated

    changed = migrated or raw.get("schema_version") != SCHEMA_VERSION
    if changed:
        backup = _backup_path(config_path, "pre-v3")
        _copy_backup(config_path, backup)
        save_config(validated, config_path)
        if removed:
            logger.warning("频道配置迁移已合并 %d 条重复记录", removed)

    return validated


def save_config(config: dict | AppConfig, path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    value = config.model_dump(mode="json") if isinstance(config, AppConfig) else config
    value = _without_none(AppConfig.model_validate(value).model_dump(mode="json"))
    value["schema_version"] = SCHEMA_VERSION

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.",
        suffix=".tmp",
        dir=config_path.parent,
    )
    try:
        with os.fdopen(fd, "wb") as file:
            tomli_w.dump(value, file)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.chmod(temp_name, 0o600)
        except OSError:
            pass
        os.replace(temp_name, config_path)
        try:
            directory_fd = os.open(config_path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _deep_copy_config(config: dict) -> dict:
    return copy.deepcopy(config)


def _without_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_none(item) for item in value]
    return value
