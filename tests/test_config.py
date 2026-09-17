from __future__ import annotations

import json

import pytest
import tomli_w
from pydantic import ValidationError

from app.config import (
    DEFAULT_CONFIG,
    SCHEMA_VERSION,
    channel_unique_key,
    load_config,
    save_config,
)
from app.models import ASMR_TITLE_FILTER, Channel


def test_default_config_is_v3(tmp_path, monkeypatch):
    monkeypatch.delenv("COOKIES_FILE", raising=False)
    path = tmp_path / "config.toml"
    loaded = load_config(path)
    assert loaded["schema_version"] == SCHEMA_VERSION
    assert loaded["general"]["cookies_file_path"] == str(tmp_path / "cookies.txt")
    assert loaded["channels"] == []


def test_save_config_is_atomic_and_omits_none(tmp_path):
    path = tmp_path / "config.toml"
    save_config(DEFAULT_CONFIG, path)
    content = path.read_text(encoding="utf-8")
    assert "channel_title" not in content
    assert not list(tmp_path.glob("*.tmp"))


def test_old_enum_migrates_to_title_filter(tmp_path):
    path = tmp_path / "config.toml"
    with path.open("wb") as file:
        tomli_w.dump(
            {
                "general": {},
                "download_limits": {},
                "channels": [
                    {
                        "folder_name": "A",
                        "youtube_id": "@example",
                        "vid_type": "videos",
                        "is_regex": True,
                    }
                ],
            },
            file,
        )
    channel = load_config(path)["channels"][0]
    assert channel["filter_enabled"] is True
    assert channel["title_filter"] == ASMR_TITLE_FILTER
    assert "is_regex" not in channel


def test_channel_url_input_is_normalized(tmp_path):
    path = tmp_path / "config.toml"
    save_config(
        {
            "channels": [
                {
                    "folder_name": "Example",
                    "youtube_id": "https://www.youtube.com/@Example/videos",
                }
            ]
        },
        path,
    )
    channel = load_config(path)["channels"][0]
    assert channel["youtube_id"] == "Example"
    assert channel["source_url"] == "https://www.youtube.com/@Example"


def test_duplicate_channels_are_merged(tmp_path):
    path = tmp_path / "config.toml"
    save_config(
        {
            "channels": [
                {"folder_name": "A", "youtube_id": "@Example", "enabled": False},
                {"folder_name": "B", "youtube_id": "example", "enabled": True},
                {
                    "folder_name": "Streams",
                    "youtube_id": "example",
                    "vid_type": "streams",
                },
            ]
        },
        path,
    )
    loaded = load_config(path)
    assert len(loaded["channels"]) == 2
    assert loaded["channels"][0]["folder_name"] == "B"
    assert loaded["channels"][0]["enabled"] is True


def test_corrupt_file_is_backed_up_and_rebuilt(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("not = [valid", encoding="utf-8")
    loaded = load_config(path)
    assert loaded["schema_version"] == SCHEMA_VERSION
    assert list(tmp_path.glob("config.toml.corrupt-*.bak"))


def test_runtime_paths_come_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_ROOT", "/mnt/media")
    monkeypatch.setenv("COOKIES_FILE", "/mnt/secret/cookies.txt")
    monkeypatch.setenv("DOWNLOAD_ARCHIVE", "custom-archive.txt")
    loaded = load_config(tmp_path / "config.toml")
    assert loaded["general"]["output_base_path"] == "/mnt/media"
    assert loaded["general"]["cookies_file_path"] == "/mnt/secret/cookies.txt"
    assert loaded["general"]["download_archive"] == "custom-archive.txt"


@pytest.mark.parametrize("folder", ["../escape", "/absolute", "a/b", "a\\b"])
def test_channel_rejects_path_escape(folder):
    with pytest.raises(ValidationError):
        Channel(folder_name=folder, youtube_id="@example")


def test_channel_url_uses_canonical_id():
    channel = Channel(
        folder_name="A",
        youtube_id="UC12345678901234567890",
        youtube_channel_id="UC12345678901234567890",
    )
    assert channel.channel_url == (
        "https://www.youtube.com/channel/UC12345678901234567890/videos"
    )


def test_unique_key_prefers_canonical_id():
    left = {"youtube_channel_id": "UC12345678901234567890", "vid_type": "videos"}
    right = {
        "youtube_id": "different",
        "youtube_channel_id": "uc12345678901234567890",
        "vid_type": "videos",
    }
    assert channel_unique_key(left) == channel_unique_key(right)


def test_saved_config_is_valid_json_compatible(tmp_path):
    path = tmp_path / "config.toml"
    save_config(DEFAULT_CONFIG, path)
    assert path.exists()
    assert path.read_bytes()
    json.dumps(DEFAULT_CONFIG)
