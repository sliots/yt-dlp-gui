from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

DEFAULT_CONFIG_PATH = "config/config.toml"

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
        "filename_format": "[%(upload_date>%Y-%m-%d)s]%(title)s [%(id)s].%(ext)s",
        "first_run_timeout": 360,
        "normal_timeout": 60,
        "cookies_file_path": "/app/config/cookies.txt",
        "cookies_source": "file",
        "cookies_browser": "firefox",
        "cookies_browser_profile": "",
        "cookies_browser_container": "",
        "log_max_history": 200,
    },
    "download_limits": {
        "normal_limit": 20,
        "first_run_limit": 99999,
    },
    "channels": [],
}

DEFAULT_CHANNEL: dict[str, Any] = {
    "folder_name": "",
    "youtube_id": "",
    "vid_type": "videos",
    "dl_type": "audio",
    "enabled": True,
    "is_regex": False,
    "is_first": True,
}


def _deep_merge(base: dict, overlay: dict) -> None:
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _ensure_channel_defaults(channel: dict) -> dict:
    result = dict(DEFAULT_CHANNEL)
    result.update(channel)
    return result


def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    config_path = Path(path)

    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        save_config(DEFAULT_CONFIG, path)
        return _deep_copy_config(DEFAULT_CONFIG)

    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)

        merged = _deep_copy_config(DEFAULT_CONFIG)
        if isinstance(raw, dict):
            _deep_merge(merged, raw)

        if not isinstance(merged.get("channels"), list):
            merged["channels"] = []

        merged["channels"] = [
            _ensure_channel_defaults(ch) for ch in merged["channels"]
        ]

        return merged
    except Exception:
        backup = config_path.with_suffix(".toml.bak")
        shutil.copy2(config_path, backup)
        save_config(DEFAULT_CONFIG, path)
        return _deep_copy_config(DEFAULT_CONFIG)


def save_config(config: dict, path: str = DEFAULT_CONFIG_PATH) -> None:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "wb") as f:
        tomli_w.dump(config, f)


def _deep_copy_config(config: dict) -> dict:
    import copy
    return copy.deepcopy(config)
