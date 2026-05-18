from __future__ import annotations

import os
import tempfile

import pytest

from app.config import (
    DEFAULT_CHANNEL,
    DEFAULT_CONFIG,
    _deep_copy_config,
    _deep_merge,
    _ensure_channel_defaults,
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
        assert copy_result["download_limits"]["normal_limit"] == 20
        assert copy_result["channels"] == []


class TestSaveAndLoad:
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
            parent = os.path.dirname(tmp)
            while parent and parent != "/":
                if os.path.exists(parent) and not os.listdir(parent):
                    os.rmdir(parent)
                parent = os.path.dirname(parent)
