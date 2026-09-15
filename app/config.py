from __future__ import annotations

import shutil
import logging
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

DEFAULT_CONFIG_PATH = "config/config.toml"
MIGRATION_BACKUP_SUFFIX = ".pre-v2.0.14.bak"

logger = logging.getLogger("yt_dlp_gui")

DEFAULT_CONFIG: dict[str, Any] = {
    "general": {
        "output_base_path": "/downloads",
        "proxy_url": "",
        "sleep_requests": 5,
        "sleep_time": "5s",
        "wait_time_minutes": 360,
        "quiet_mode": False,
        "dateafter": "20000101",
        "download_archive": "archive.txt",
        "filename_format": "[%(upload_date>%Y-%m-%d)s]%(title).160B [%(id)s].%(ext)s",
        "first_run_timeout": 360,
        "normal_timeout": 60,
        "cookies_file_path": "/app/config/cookies.txt",
        "cookies_source": "file",
        "cookies_browser": "firefox",
        "cookies_browser_profile": "",
        "cookies_browser_container": "",
        "po_token_enabled": True,
        "po_token_base_url": "http://bgutil-provider:4416",
        "log_max_history": 200,
    },
    "download_limits": {
        "normal_limit": 20,
        "first_run_limit": 99999,
    },
    "channels": [],
}

DEFAULT_CHANNEL: dict[str, Any] = {
    "channel_id": "",
    "folder_name": "",
    "youtube_id": "",
    "vid_type": "videos",
    "dl_type": "audio",
    "enabled": True,
    "is_regex": False,
    "is_first": True,
}


def normalize_youtube_id(value: str) -> str:
    value = value.strip()
    return value[1:] if value.startswith("@") else value


def channel_unique_key(channel: dict) -> tuple[str, str]:
    return (
        normalize_youtube_id(str(channel.get("youtube_id", ""))).casefold(),
        str(channel.get("vid_type", "videos")),
    )


def _valid_channel_id(value: Any) -> bool:
    try:
        UUID(str(value))
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def _deep_merge(base: dict, overlay: dict) -> None:
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _ensure_channel_defaults(channel: dict) -> dict:
    result = dict(DEFAULT_CHANNEL)
    result.update(channel)
    result["youtube_id"] = normalize_youtube_id(str(result["youtube_id"]))
    return result


def _migrate_channels(channels: list[dict]) -> tuple[list[dict], bool, int]:
    normalized = [_ensure_channel_defaults(ch) for ch in channels if isinstance(ch, dict)]
    changed = len(normalized) != len(channels) or normalized != channels

    grouped: dict[tuple[str, str], list[dict]] = {}
    key_order: list[tuple[str, str]] = []
    for channel in normalized:
        key = channel_unique_key(channel)
        if key not in grouped:
            grouped[key] = []
            key_order.append(key)
        grouped[key].append(channel)

    merged: list[dict] = []
    removed = 0
    for key in key_order:
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
        if not _valid_channel_id(channel_id) or channel_id in seen_ids:
            channel_id = str(uuid4())
            channel["channel_id"] = channel_id
            changed = True
        seen_ids.add(channel_id)

    return merged, changed, removed


def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    config_path = Path(path)

    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        save_config(DEFAULT_CONFIG, path)
        return _deep_copy_config(DEFAULT_CONFIG)

    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
    except Exception:
        backup = config_path.with_suffix(".toml.bak")
        shutil.copy2(config_path, backup)
        save_config(DEFAULT_CONFIG, path)
        return _deep_copy_config(DEFAULT_CONFIG)

    merged = _deep_copy_config(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        _deep_merge(merged, raw)

    if not isinstance(merged.get("channels"), list):
        merged["channels"] = []

    channels, migrated, removed = _migrate_channels(merged["channels"])
    merged["channels"] = channels

    if migrated:
        backup = Path(str(config_path) + MIGRATION_BACKUP_SUFFIX)
        if not backup.exists():
            shutil.copy2(config_path, backup)
        save_config(merged, path)
        if removed:
            logger.warning("频道配置迁移已合并 %d 条重复记录", removed)

    return merged


def save_config(config: dict, path: str = DEFAULT_CONFIG_PATH) -> None:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "wb") as f:
        tomli_w.dump(config, f)


def _deep_copy_config(config: dict) -> dict:
    import copy
    return copy.deepcopy(config)
