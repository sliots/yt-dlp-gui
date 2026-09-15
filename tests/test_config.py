from __future__ import annotations

import os
import tempfile
from uuid import UUID

import pytest

from app.config import (
    DEFAULT_CHANNEL,
    DEFAULT_CONFIG,
    _deep_copy_config,
    _deep_merge,
    _ensure_channel_defaults,
    channel_unique_key,
    load_config,
    save_config,
)


class TestDeepMerge:
    def test_overlay_adds_new_keys(self):
        base = {"a": 1}
        _deep_merge(base, {"b": 2})
        assert base == {"a": 1, "b": 2}

    def test_overlay_overwrites_scalar(self):
        base = {"a": 1}
        _deep_merge(base, {"a": 2})
        assert base["a"] == 2

    def test_nested_dict_merge(self):
        base = {"general": {"x": 1, "y": 2}}
        _deep_merge(base, {"general": {"y": 99, "z": 3}})
        assert base["general"] == {"x": 1, "y": 99, "z": 3}

    def test_overlay_replaces_dict_with_non_dict(self):
        base = {"general": {"x": 1}}
        _deep_merge(base, {"general": "string"})
        assert base["general"] == "string"

    def test_empty_overlay_leaves_base_unchanged(self):
        base = {"a": 1, "b": {"c": 2}}
        _deep_merge(base, {})
        assert base == {"a": 1, "b": {"c": 2}}


class TestEnsureChannelDefaults:
    def test_fills_missing_fields(self):
        result = _ensure_channel_defaults({"folder_name": "test"})
        assert result["folder_name"] == "test"
        assert result["youtube_id"] == DEFAULT_CHANNEL["youtube_id"]
        assert result["enabled"] is True
        assert result["is_regex"] is False
        assert result["is_first"] is True
        assert result["youtube_id"] == ""

    def test_overwrites_existing_fields(self):
        result = _ensure_channel_defaults({"folder_name": "a", "is_first": False})
        assert result["folder_name"] == "a"
        assert result["is_first"] is False

    def test_returns_new_dict_not_mutating_input(self):
        input_ch = {"folder_name": "orig"}
        result = _ensure_channel_defaults(input_ch)
        assert "enabled" not in input_ch
        assert result["enabled"] is True


class TestDeepCopyConfig:
    def test_returns_independent_copy(self):
        original = {"a": {"b": 1}}
        copied = _deep_copy_config(original)
        copied["a"]["b"] = 99
        assert original["a"]["b"] == 1
        assert copied["a"]["b"] == 99

    def test_deepcopy_preserves_all_keys(self):
        copy_result = _deep_copy_config(DEFAULT_CONFIG)
        assert copy_result["general"]["output_base_path"] == "/downloads"
        assert copy_result["general"]["filename_format"] == "[%(upload_date>%Y-%m-%d)s]%(title).160B [%(id)s].%(ext)s"
        assert copy_result["download_limits"]["normal_limit"] == 20
        assert copy_result["channels"] == []


class TestSaveAndLoad:
    def test_old_config_gets_po_token_defaults(self, tmp_path):
        path = tmp_path / "config.toml"
        save_config({"general": {"cookies_source": "browser"}}, path)
        general = load_config(path)["general"]
        assert general["cookies_source"] == "browser"
        assert general["po_token_enabled"] is True
        assert general["po_token_base_url"] == "http://bgutil-provider:4416"

    def test_save_and_load_roundtrip(self):
        config = {
            "general": {"output_base_path": "/tmp"},
            "download_limits": {"normal_limit": 42},
            "channels": [
                {"folder_name": "ch1", "youtube_id": "@test"}
            ],
        }
        tmp = os.path.join(tempfile.gettempdir(), "test_config.toml")
        try:
            save_config(config, tmp)
            loaded = load_config(tmp)
            assert loaded["general"]["output_base_path"] == "/tmp"
            assert loaded["download_limits"]["normal_limit"] == 42
            assert len(loaded["channels"]) == 1
            assert loaded["channels"][0]["folder_name"] == "ch1"
            assert loaded["channels"][0]["youtube_id"] == "test"
            UUID(loaded["channels"][0]["channel_id"])
            assert loaded["channels"][0]["enabled"] is True
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_load_creates_file_when_missing(self):
        tmp = os.path.join(tempfile.gettempdir(), "nonexistent_config.toml")
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
            loaded = load_config(tmp)
            assert loaded["general"]["sleep_requests"] == 5
            assert loaded["channels"] == []
            assert os.path.exists(tmp)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_load_replaces_non_list_channels(self):
        config = {
            "general": {},
            "download_limits": {},
            "channels": "not_a_list",
        }
        tmp = os.path.join(tempfile.gettempdir(), "bad_channels.toml")
        try:
            save_config(config, tmp)
            loaded = load_config(tmp)
            assert loaded["channels"] == []
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_load_recovers_from_corrupted_file(self):
        tmp = os.path.join(tempfile.gettempdir(), "corrupted.toml")
        try:
            with open(tmp, "w") as f:
                f.write("this is not valid toml [[[")
            loaded = load_config(tmp)
            assert loaded["general"]["sleep_requests"] == 5
            assert loaded["channels"] == []
            assert os.path.exists(tmp + ".bak")
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
            if os.path.exists(tmp + ".bak"):
                os.unlink(tmp + ".bak")

    def test_load_preserves_user_values(self):
        config = {
            "general": {"output_base_path": "/custom/path", "proxy_url": "http://p"},
            "download_limits": {"normal_limit": 77},
            "channels": [
                {"folder_name": "ChA", "youtube_id": "@a", "is_regex": True}
            ],
        }
        tmp = os.path.join(tempfile.gettempdir(), "user_config.toml")
        try:
            save_config(config, tmp)
            loaded = load_config(tmp)
            assert loaded["general"]["output_base_path"] == "/custom/path"
            assert loaded["general"]["proxy_url"] == "http://p"
            assert loaded["general"]["sleep_requests"] == 5
            assert loaded["download_limits"]["normal_limit"] == 77
            assert loaded["channels"][0]["is_regex"] is True
            assert loaded["channels"][0]["enabled"] is True
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_save_creates_parent_dirs(self):
        tmp = os.path.join(tempfile.gettempdir(), "deep/nested/config.toml")
        try:
            save_config(DEFAULT_CONFIG, tmp)
            assert os.path.exists(tmp)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
            cleanup_root = os.path.abspath(os.path.join(tempfile.gettempdir(), "deep"))
            parent = os.path.abspath(os.path.dirname(tmp))
            while parent.startswith(cleanup_root):
                if os.path.exists(parent) and not os.listdir(parent):
                    os.rmdir(parent)
                if parent == cleanup_root:
                    break
                parent = os.path.dirname(parent)

    def test_migrates_duplicate_channels_once(self, tmp_path, caplog):
        path = tmp_path / "config.toml"
        save_config({
            "channels": [
                {
                    "folder_name": "Disabled",
                    "youtube_id": " @Example ",
                    "vid_type": "videos",
                    "enabled": False,
                    "is_first": True,
                },
                {
                    "folder_name": "Enabled",
                    "youtube_id": "example",
                    "vid_type": "videos",
                    "enabled": True,
                    "is_first": False,
                },
                {
                    "folder_name": "Streams",
                    "youtube_id": "EXAMPLE",
                    "vid_type": "streams",
                },
            ],
        }, path)

        with caplog.at_level("WARNING", logger="yt_dlp_gui"):
            loaded = load_config(path)

        assert len(loaded["channels"]) == 2
        videos = loaded["channels"][0]
        assert videos["folder_name"] == "Enabled"
        assert videos["enabled"] is True
        assert videos["is_first"] is True
        assert videos["youtube_id"] == "example"
        assert (tmp_path / "config.toml.pre-v2.0.14.bak").exists()
        assert "合并 1 条重复记录" in caplog.text

        ids = [channel["channel_id"] for channel in loaded["channels"]]
        reloaded = load_config(path)
        assert [channel["channel_id"] for channel in reloaded["channels"]] == ids

    def test_duplicate_channel_ids_are_replaced(self, tmp_path):
        path = tmp_path / "config.toml"
        duplicate_id = "c21974f5-2a9e-4c4e-b6ed-548b456d6301"
        save_config({
            "channels": [
                {"channel_id": duplicate_id, "folder_name": "A", "youtube_id": "a"},
                {"channel_id": duplicate_id, "folder_name": "B", "youtube_id": "b"},
            ],
        }, path)
        loaded = load_config(path)
        ids = [channel["channel_id"] for channel in loaded["channels"]]
        assert ids[0] == duplicate_id
        assert ids[1] != duplicate_id
        assert len(set(ids)) == 2


def test_channel_unique_key_normalizes_case_space_and_at():
    assert channel_unique_key({"youtube_id": " @TeSt ", "vid_type": "videos"}) == (
        "test", "videos",
    )
