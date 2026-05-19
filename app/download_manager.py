from __future__ import annotations

import asyncio
import copy
import os
import subprocess
import threading
import time
from datetime import datetime
from typing import Any

from app.engine import YtDlpEngine
from app.websocket_manager import LogBroadcaster


class DownloadManager:
    def __init__(self, config: dict, broadcaster: LogBroadcaster):
        self._config = config
        self._broadcaster = broadcaster
        self._config_lock = threading.Lock()
        self._current_task: asyncio.Task | None = None
        self._engine: YtDlpEngine | None = None
        self._running = False
        self._mode: str | None = None
        self._current_channel_index: int = 0
        self._total_channels: int = 0
        self._current_channel_label: str = ""
        self._progress_percent: float = 0.0
        self._countdown_start: float = 0
        self._countdown_duration: float = 0

    @property
    def status(self) -> dict[str, Any]:
        next_round = 0
        if self._mode == "loop" and not self._running and self._countdown_start > 0:
            elapsed = time.time() - self._countdown_start
            remaining = max(0, self._countdown_duration - elapsed)
            next_round = int(remaining)
        return {
            "running": self._running,
            "mode": self._mode,
            "current_channel_index": self._current_channel_index,
            "total_channels": self._total_channels,
            "current_channel_label": self._current_channel_label,
            "progress_percent": self._progress_percent,
            "next_round_seconds": next_round,
        }

    def _update_progress(self, info: dict[str, Any]) -> None:
        self._current_channel_index = info.get("channel_index", 0)
        self._total_channels = info.get("channel_total", 0)
        self._current_channel_label = info.get("channel_label", "")
        self._progress_percent = info.get("percent", 0)

    def _wrap_progress(self, engine: YtDlpEngine) -> None:
        original = engine._broadcaster.broadcast_sync

        def _hooked(level: str, message: str) -> None:
            if level == "PROGRESS":
                import json
                try:
                    data = json.loads(message)
                    if data.get("type") == "progress":
                        self._update_progress(data)
                except (json.JSONDecodeError, KeyError):
                    pass
            original(level, message)

        engine._broadcaster.broadcast_sync = _hooked  # type: ignore[method-assign]

    async def _run_engine(self, engine: YtDlpEngine) -> bool:
        self._engine = engine
        self._wrap_progress(engine)
        return await asyncio.to_thread(engine.run_all)

    async def _run_download(self, mode: str) -> dict[str, Any]:
        self._mode = mode
        self._running = True
        self._progress_percent = 0.0
        self._broadcaster.broadcast_sync("INFO", f"启动模式：{mode}")

        cookies_source = self._config["general"].get("cookies_source", "file")
        if cookies_source == "file":
            cookies_path = self._config["general"].get("cookies_file_path", "/app/config/cookies.txt")
            if cookies_path and not os.path.exists(cookies_path):
                self._broadcaster.broadcast_sync(
                    "ERROR",
                    f"✖ cookies 文件不存在：{cookies_path}，请先通过 API 上传",
                )
                self._broadcaster.broadcast_sync("ERROR", "上传方式：设置 Tab → 上传 cookies.txt")
                return {"ok": False, "reason": "cookies 文件不存在，请先上传"}

        try:
            wait_minutes = self._config["general"].get("wait_time_minutes", 360)

            while True:
                with self._config_lock:
                    channels_copy = copy.deepcopy(self._config.get("channels", []))

                if mode == "first_only":
                    channels_copy = [ch for ch in channels_copy if ch.get("is_first", False)]
                    if not channels_copy:
                        self._broadcaster.broadcast_sync("WARNING", "⚠ 没有标记为首次下载的频道")
                        break

                if not any(ch.get("enabled", True) for ch in channels_copy):
                    self._broadcaster.broadcast_sync("WARNING", "⚠ 没有启用的频道")
                    break

                config_copy = copy.deepcopy(self._config)
                config_copy["channels"] = channels_copy
                engine = YtDlpEngine(config_copy, self._broadcaster)
                self._engine = engine

                ok = await self._run_engine(engine)

                with self._config_lock:
                    engine_state = engine.get_channel_state()
                    for i, ch_copy in enumerate(engine_state):
                        if i < len(self._config.get("channels", [])):
                            self._config["channels"][i]["is_first"] = ch_copy.get("is_first", False)

                if not ok or mode == "once" or mode == "first_only":
                    break

                self._broadcaster.broadcast_sync(
                    "INFO",
                    f"⏰ 下一轮将在 {wait_minutes} 分钟后开始",
                )
                self._countdown_start = time.time()
                self._countdown_duration = wait_minutes * 60
                self._running = False

                for _ in range(wait_minutes * 60):
                    if self._engine is not None and self._engine._stop_flag:
                        break
                    await asyncio.sleep(1)

                if self._engine is not None and self._engine._stop_flag:
                    break
                self._running = True
        finally:
            self._running = False
            self._mode = None
            self._engine = None
            self._countdown_start = 0
            self._broadcaster.broadcast_sync("SUCCESS", "所有任务完成")

        return {"ok": True}

    async def start_once(self) -> dict[str, Any]:
        if self._running:
            return {"ok": False, "reason": "已有下载任务运行中"}
        self._current_task = asyncio.create_task(self._run_download("once"))
        return {"ok": True}

    async def start_loop(self) -> dict[str, Any]:
        if self._running:
            return {"ok": False, "reason": "已有下载任务运行中"}
        self._current_task = asyncio.create_task(self._run_download("loop"))
        return {"ok": True}

    async def start_first_only(self) -> dict[str, Any]:
        if self._running:
            return {"ok": False, "reason": "已有下载任务运行中"}
        self._current_task = asyncio.create_task(self._run_download("first_only"))
        return {"ok": True}

    async def stop(self) -> dict[str, Any]:
        if self._engine is not None:
            self._engine.request_stop()
        return {"ok": True}

    async def update_ytdlp(self) -> dict[str, Any]:
        if self._running:
            return {"ok": False, "reason": "下载运行中，无法更新"}
        try:
            result = subprocess.run(
                ["/usr/local/bin/yt-dlp", "-U", "--update-to", "nightly"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = result.stdout.strip() or result.stderr.strip()
            self._broadcaster.broadcast_sync("INFO", f"yt-dlp 更新：{output}")
            return {"ok": True, "output": output}
        except FileNotFoundError:
            return {"ok": False, "reason": "找不到 yt-dlp"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "更新超时"}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def shutdown(self) -> None:
        if self._engine is not None:
            self._engine.request_stop()
