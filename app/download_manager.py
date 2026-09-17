from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import DEFAULT_CONFIG_PATH, save_config
from app.engine import YtDlpEngine
from app.models import ChannelRuntime
from app.websocket_manager import LogBroadcaster

RUN_STAT_FIELDS = (
    "total_channels",
    "processed_channels",
    "completed_channels",
    "warning_channels",
    "downloaded_files",
    "archive_skipped",
    "filtered_skipped",
    "member_skipped",
    "hard_errors",
    "stopped",
)


class DownloadManager:
    def __init__(
        self,
        config: dict,
        broadcaster: LogBroadcaster,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
    ):
        self._config = config
        self._config_path = Path(config_path)
        self._last_run_path = self._config_path.parent / "last_run.json"
        self._loop_state_path = self._config_path.parent / "loop_state.json"
        self._broadcaster = broadcaster
        self._config_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._current_task: asyncio.Task | None = None
        self._engine: YtDlpEngine | None = None
        self._stop_event: asyncio.Event | None = None
        self._state = "idle"
        self._mode: str | None = None
        self._selected_channel_ids: list[str] = []
        self._current_channel_index = 0
        self._total_channels = 0
        self._current_channel_label = ""
        self._progress_percent = 0.0
        self._countdown_start = 0.0
        self._countdown_duration = 0.0
        self._current_run: dict[str, Any] | None = None
        self._run_started_monotonic = 0.0
        self._last_run = self._load_last_run()
        self._channel_runtime = self._runtime_from_last_run(self._last_run)
        self._shutdown_requested = False

    @property
    def config_lock(self) -> threading.RLock:
        return self._config_lock

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def active(self) -> bool:
        with self._state_lock:
            return self._state != "idle"

    @property
    def status(self) -> dict[str, Any]:
        with self._state_lock:
            state = self._state
            current_run = copy.deepcopy(self._current_run)
            last_run = copy.deepcopy(self._last_run)
            status = {
                "state": state,
                "active": state != "idle",
                "running": state in ("running", "stopping"),
                "mode": self._mode,
                "current_channel_index": self._current_channel_index,
                "total_channels": self._total_channels,
                "current_channel_label": self._current_channel_label,
                "progress_percent": self._progress_percent,
                "next_round_seconds": 0,
            }
            countdown_start = self._countdown_start
            countdown_duration = self._countdown_duration
            started_monotonic = self._run_started_monotonic
            engine = self._engine

        if state == "waiting" and countdown_start > 0:
            elapsed = time.time() - countdown_start
            status["next_round_seconds"] = int(max(0, countdown_duration - elapsed))

        if current_run is not None:
            if engine is not None:
                current_run.update(engine.get_run_stats())
            if started_monotonic:
                current_run["duration_seconds"] = round(time.monotonic() - started_monotonic, 1)
        status["current_run"] = current_run
        status["last_run"] = last_run
        return status

    def _load_last_run(self) -> dict[str, Any] | None:
        try:
            with open(self._last_run_path, encoding="utf-8") as file:
                value = json.load(file)
            return value if isinstance(value, dict) else None
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _runtime_from_last_run(last_run: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        if not last_run:
            return {}
        result: dict[str, dict[str, Any]] = {}
        for item in last_run.get("channel_results", []):
            if isinstance(item, dict) and item.get("channel_id"):
                try:
                    runtime = ChannelRuntime.model_validate(item)
                    result[runtime.channel_id] = runtime.model_dump(mode="json")
                except Exception:
                    continue
        return result

    def channel_runtimes(self) -> dict[str, dict[str, Any]]:
        with self._state_lock:
            result = copy.deepcopy(self._channel_runtime)
            engine = self._engine
        if engine is not None:
            for channel_id, runtime in engine.get_channel_results().items():
                result[channel_id] = {**result.get(channel_id, {}), **runtime}
        return result

    def channel_runtime(self, channel_id: str) -> dict[str, Any]:
        runtime = self.channel_runtimes().get(channel_id, {})
        return {
            "channel_id": channel_id,
            "state": "idle",
            "percent": 0.0,
            **runtime,
        }

    def _persist_last_run(self, summary: dict[str, Any]) -> None:
        try:
            self._last_run_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._last_run_path.with_suffix(".json.tmp")
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(summary, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            os.replace(temp_path, self._last_run_path)
        except OSError as exc:
            self._broadcaster.broadcast_sync("WARNING", f"上一轮摘要保存失败：{exc}")

    def _load_loop_deadline(self) -> float | None:
        try:
            with open(self._loop_state_path, encoding="utf-8") as file:
                value = json.load(file)
            next_run_at = datetime.fromisoformat(value["next_run_at"])
            if next_run_at.tzinfo is None:
                next_run_at = next_run_at.replace(tzinfo=UTC)
            return next_run_at.timestamp()
        except FileNotFoundError:
            return None
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
            self._broadcaster.broadcast_sync("WARNING", f"循环等待状态读取失败：{exc}")
            return None

    def _persist_loop_deadline(self, deadline: float) -> None:
        value = {
            "mode": "loop",
            "state": "waiting",
            "next_run_at": datetime.fromtimestamp(deadline, UTC).isoformat(),
        }
        try:
            self._loop_state_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._loop_state_path.with_suffix(".json.tmp")
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(value, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self._loop_state_path)
        except OSError as exc:
            self._broadcaster.broadcast_sync("WARNING", f"循环等待状态保存失败：{exc}")

    def _clear_loop_deadline(self) -> None:
        try:
            self._loop_state_path.unlink(missing_ok=True)
        except OSError as exc:
            self._broadcaster.broadcast_sync("WARNING", f"循环等待状态清理失败：{exc}")

    def _clear_progress(self) -> None:
        with self._state_lock:
            self._current_channel_index = 0
            self._total_channels = 0
            self._current_channel_label = ""
            self._progress_percent = 0.0

    def _update_progress(self, info: dict[str, Any]) -> None:
        channel_id = str(info.get("channel_id") or "")
        with self._state_lock:
            self._current_channel_index = info.get("channel_index", 0)
            self._total_channels = info.get("channel_total", 0)
            self._current_channel_label = info.get("channel_label", "")
            self._progress_percent = info.get("percent", 0.0)
            if channel_id:
                current = self._channel_runtime.setdefault(channel_id, {"channel_id": channel_id})
                current.update(
                    {
                        "state": "running",
                        "percent": info.get("percent", 0.0),
                        "last_started_at": current.get("last_started_at")
                        or datetime.now(UTC).isoformat(),
                    }
                )

    def _begin_run(self, mode: str, total_channels: int, channel_ids: list[str]) -> None:
        now = datetime.now(UTC).isoformat()
        with self._state_lock:
            self._state = "running"
            self._current_run = {
                "started_at": now,
                "finished_at": None,
                "duration_seconds": 0.0,
                "mode": mode,
                "outcome": None,
                "total_channels": total_channels,
                "processed_channels": 0,
                "completed_channels": 0,
                "warning_channels": 0,
                "downloaded_files": 0,
                "archive_skipped": 0,
                "filtered_skipped": 0,
                "member_skipped": 0,
                "hard_errors": 0,
                "stopped": False,
            }
            self._run_started_monotonic = time.monotonic()
            for channel_id in channel_ids:
                current = self._channel_runtime.setdefault(channel_id, {"channel_id": channel_id})
                current.update({"state": "queued", "percent": 0.0})

    def _mark_selected_error(self, message: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._state_lock:
            for channel_id in self._selected_channel_ids:
                current = self._channel_runtime.setdefault(
                    channel_id,
                    {"channel_id": channel_id},
                )
                current.update(
                    {
                        "state": "error",
                        "last_outcome": "error",
                        "last_error": message,
                        "last_finished_at": now,
                    }
                )

    def _finish_run(
        self,
        engine_stats: dict[str, Any] | None = None,
        engine_results: dict[str, dict[str, Any]] | None = None,
        *,
        stopped: bool = False,
        hard_errors: int = 0,
        outcome: str = "success",
    ) -> None:
        with self._state_lock:
            if self._current_run is None:
                return
            summary = copy.deepcopy(self._current_run)
            if engine_stats:
                for field in RUN_STAT_FIELDS:
                    if field in engine_stats:
                        summary[field] = engine_stats[field]
            summary["hard_errors"] += hard_errors
            summary["stopped"] = bool(summary["stopped"] or stopped)
            summary["outcome"] = (
                "stopped" if summary["stopped"] else outcome
            )
            summary["finished_at"] = datetime.now(UTC).isoformat()
            summary["duration_seconds"] = round(
                time.monotonic() - self._run_started_monotonic,
                1,
            )
            if engine_results:
                self._channel_runtime.update(copy.deepcopy(engine_results))
            summary["channel_results"] = list(self._channel_runtime.values())
            self._last_run = summary
            self._current_run = None
            self._run_started_monotonic = 0.0
        self._persist_last_run(summary)

    def _snapshot_config(
        self,
        mode: str,
        channel_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._config_lock:
            snapshot = copy.deepcopy(self._config)
        channels = snapshot.get("channels", [])
        if mode == "first_only":
            channels = [channel for channel in channels if channel.get("is_first", False)]
        elif mode == "selected":
            wanted = set(channel_ids or [])
            channels = [
                {**channel, "is_first": False}
                for channel in channels
                if channel.get("channel_id") in wanted and channel.get("enabled", True)
            ]
        snapshot["channels"] = channels
        return snapshot

    def _merge_is_first(self, snapshot: dict, engine: YtDlpEngine) -> None:
        original = {
            channel.get("channel_id"): channel.get("is_first", False)
            for channel in snapshot.get("channels", [])
        }
        changed = False
        with self._config_lock:
            live_by_id = {
                channel.get("channel_id"): channel
                for channel in self._config.get("channels", [])
            }
            for result in engine.get_channel_state():
                channel_id = result.get("channel_id")
                if channel_id not in original or channel_id not in live_by_id:
                    continue
                old_value = original[channel_id]
                new_value = result.get("is_first", False)
                live = live_by_id[channel_id]
                if new_value != old_value and live.get("is_first", False) == old_value:
                    live["is_first"] = new_value
                    changed = True
            if changed:
                save_config(self._config, self._config_path)

    async def _wait_for_loop_deadline(
        self,
        deadline: float,
        *,
        resumed: bool = False,
    ) -> bool:
        remaining = max(0.0, deadline - time.time())
        if remaining <= 0:
            self._clear_loop_deadline()
            return False
        if resumed:
            self._broadcaster.broadcast_sync(
                "INFO",
                f"恢复循环等待，距离下一轮约 {int(remaining)} 秒",
            )
        with self._state_lock:
            self._state = "waiting"
            self._countdown_start = time.time()
            self._countdown_duration = remaining
        self._clear_progress()
        stop_event = self._stop_event
        if stop_event is None:
            return True
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=remaining)
            return True
        except TimeoutError:
            self._clear_loop_deadline()
            with self._state_lock:
                self._countdown_start = 0.0
                self._countdown_duration = 0.0
            return False

    async def _run_download(
        self,
        mode: str,
        initial_wait_until: float | None = None,
        channel_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        outcome = "success"
        self._broadcaster.broadcast_sync("INFO", f"启动模式：{mode}")

        try:
            should_run = True
            if initial_wait_until is not None:
                should_run = not await self._wait_for_loop_deadline(
                    initial_wait_until,
                    resumed=True,
                )

            while (
                should_run
                and self._stop_event is not None
                and not self._stop_event.is_set()
            ):
                if mode == "loop":
                    self._clear_loop_deadline()
                snapshot = self._snapshot_config(mode, channel_ids)
                channels = snapshot.get("channels", [])
                active_count = sum(ch.get("enabled", True) for ch in channels)
                selected_ids = [str(ch.get("channel_id")) for ch in channels]
                self._begin_run(mode, active_count, selected_ids)

                general = snapshot.get("general", {})
                cookies_source = general.get("cookies_source", "file")
                cookies_path = general.get("cookies_file_path", "")
                if cookies_source == "file" and cookies_path and not os.path.exists(cookies_path):
                    self._broadcaster.broadcast_sync(
                        "ERROR",
                        f"配置校验失败：cookies 文件不存在：{cookies_path}",
                    )
                    self._mark_selected_error("配置校验失败：cookies 文件不存在")
                    self._finish_run(hard_errors=1, outcome="failed")
                    outcome = "failed"
                    break
                if mode == "first_only" and not channels:
                    self._broadcaster.broadcast_sync("WARNING", "没有标记为首次下载的频道")
                    self._finish_run(outcome="success")
                    outcome = "success"
                    break
                if not active_count:
                    self._broadcaster.broadcast_sync("WARNING", "没有可运行的频道")
                    self._finish_run(outcome="success")
                    outcome = "success"
                    break

                engine = YtDlpEngine(snapshot, self._broadcaster, self._update_progress)
                with self._state_lock:
                    self._engine = engine
                await asyncio.to_thread(engine.run_all)
                engine_stats = engine.get_run_stats()
                engine_results = (
                    engine.get_channel_results()
                    if hasattr(engine, "get_channel_results")
                    else None
                )
                self._merge_is_first(snapshot, engine)
                stopped = bool(
                    self._stop_event.is_set() or engine_stats.get("stopped", False)
                )
                if stopped:
                    outcome = "stopped"
                elif hasattr(engine, "get_run_outcome"):
                    outcome = engine.get_run_outcome()
                elif engine_stats.get("hard_errors", 0):
                    outcome = "partial"
                else:
                    outcome = "success"
                self._finish_run(
                    engine_stats,
                    engine_results,
                    stopped=stopped,
                    outcome=outcome,
                )
                with self._state_lock:
                    self._engine = None

                if stopped or mode != "loop":
                    break

                wait_minutes = general.get("wait_time_minutes", 360)
                deadline = time.time() + wait_minutes * 60
                self._broadcaster.broadcast_sync(
                    "INFO",
                    f"下一轮将在 {wait_minutes} 分钟后开始",
                )
                self._persist_loop_deadline(deadline)
                if await self._wait_for_loop_deadline(deadline):
                    outcome = "stopped"
                    break

            if self._stop_event is not None and self._stop_event.is_set():
                outcome = "stopped"

        except Exception as exc:
            self._broadcaster.broadcast_sync("ERROR", f"任务发生未捕获异常：{exc}")
            self._mark_selected_error(str(exc))
            engine_stats = self._engine.get_run_stats() if self._engine else None
            engine_results = (
                self._engine.get_channel_results()
                if self._engine and hasattr(self._engine, "get_channel_results")
                else None
            )
            self._finish_run(
                engine_stats,
                engine_results,
                hard_errors=1,
                outcome="failed",
            )
            outcome = "failed"
        finally:
            messages = {
                "success": ("SUCCESS", "所有任务完成"),
                "partial": ("WARNING", "任务完成，部分频道存在警告或错误"),
                "failed": ("ERROR", "任务异常结束"),
                "stopped": ("WARNING", "任务已停止"),
            }
            level, message = messages.get(outcome, ("ERROR", "任务异常结束"))
            self._broadcaster.broadcast_sync(level, message)
            with self._state_lock:
                self._state = "idle"
                self._mode = None
                self._selected_channel_ids = []
                self._engine = None
                self._stop_event = None
                self._current_task = None
                self._countdown_start = 0.0
                self._countdown_duration = 0.0
            self._clear_progress()

        return {"ok": outcome != "failed", "reason": outcome}

    async def _start(
        self,
        mode: str,
        *,
        initial_wait_until: float | None = None,
        clear_loop_deadline: bool = False,
        channel_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        waiting = bool(initial_wait_until and initial_wait_until > now)
        with self._state_lock:
            if self._state != "idle":
                return {"ok": False, "reason": "已有下载任务运行中"}
            self._state = "waiting" if waiting else "running"
            self._mode = mode
            self._selected_channel_ids = list(channel_ids or [])
            self._stop_event = asyncio.Event()
            self._shutdown_requested = False
            if waiting and initial_wait_until is not None:
                self._countdown_start = now
                self._countdown_duration = initial_wait_until - now
            self._current_task = asyncio.create_task(
                self._run_download(
                    mode,
                    initial_wait_until if waiting else None,
                    channel_ids,
                )
            )
        if clear_loop_deadline:
            self._clear_loop_deadline()
        return {"ok": True}

    async def auto_start_loop(self) -> dict[str, Any]:
        deadline = self._load_loop_deadline()
        if deadline is not None and deadline > time.time():
            return await self._start("loop", initial_wait_until=deadline)
        self._clear_loop_deadline()
        self._broadcaster.broadcast_sync("INFO", "应用启动，自动开始循环运行")
        return await self._start("loop")

    async def start_once(self) -> dict[str, Any]:
        return await self._start("once")

    async def start_loop(self) -> dict[str, Any]:
        return await self._start("loop", clear_loop_deadline=True)

    async def start_first_only(self) -> dict[str, Any]:
        return await self._start("first_only")

    async def start_selected(self, channel_ids: list[str]) -> dict[str, Any]:
        if not channel_ids:
            return {"ok": False, "reason": "未选择频道"}
        if self.active:
            return {"ok": False, "reason": "已有下载任务运行中"}
        return await self._start("selected", channel_ids=channel_ids)

    async def stop(self) -> dict[str, Any]:
        with self._state_lock:
            if self._state == "idle":
                return {"ok": True}
            if self._state == "stopping":
                return {"ok": False, "reason": "任务正在停止"}
            self._state = "stopping"
            stop_event = self._stop_event
            engine = self._engine
        self._clear_loop_deadline()
        if stop_event is not None:
            stop_event.set()
        if engine is not None:
            engine.request_stop()
        return {"ok": True}

    async def update_ytdlp(self) -> dict[str, Any]:
        if self.active:
            return {"ok": False, "reason": "下载任务活跃中，无法更新"}
        if os.getenv("ENABLE_SELF_UPDATE", "false").lower() not in {"1", "true", "yes"}:
            return {"ok": False, "reason": "网页自更新已禁用，请更新容器镜像"}
        try:
            process = await asyncio.create_subprocess_exec(
                os.getenv("YTDLP_BIN", "/usr/local/bin/yt-dlp"),
                "-U",
                "--update-to",
                "nightly",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=120)
            except TimeoutError:
                process.kill()
                await process.wait()
                return {"ok": False, "reason": "更新超时"}
            output = stdout.decode("utf-8", errors="replace").strip()
            self._broadcaster.broadcast_sync("INFO", f"yt-dlp 更新：{output}")
            if process.returncode != 0:
                return {"ok": False, "reason": output or "yt-dlp 更新失败"}
            return {"ok": True, "output": output}
        except OSError as exc:
            return {"ok": False, "reason": str(exc)}

    async def resolve_channel(self, channel: dict) -> dict[str, Any]:
        if self.active:
            return {"ok": False, "message": "下载任务活跃中，无法解析频道"}
        with self._config_lock:
            snapshot = copy.deepcopy(self._config)
        engine = YtDlpEngine(snapshot, self._broadcaster)
        return await asyncio.to_thread(engine.resolve_channel, channel)

    async def test_channel(self, channel: dict) -> dict[str, Any]:
        if self.active:
            return {"ok": False, "message": "下载任务活跃中，无法检测频道"}
        with self._config_lock:
            snapshot = copy.deepcopy(self._config)
        engine = YtDlpEngine(snapshot, self._broadcaster)
        result = await asyncio.to_thread(engine.test_channel, channel)
        now = datetime.now(UTC).isoformat()
        with self._state_lock:
            runtime = self._channel_runtime.setdefault(
                str(channel.get("channel_id")),
                {"channel_id": str(channel.get("channel_id"))},
            )
            runtime["last_test_at"] = now
            runtime["last_test_ok"] = bool(result.get("ok"))
            runtime["last_test_message"] = str(result.get("message") or "")[-2000:]
        return result

    async def shutdown(self) -> None:
        with self._state_lock:
            self._shutdown_requested = True
            stop_event = self._stop_event
            engine = self._engine
            task = self._current_task
        if stop_event is not None:
            stop_event.set()
        if engine is not None:
            engine.request_stop()
        if task is not None and task is not asyncio.current_task():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
            except TimeoutError:
                self._broadcaster.broadcast_sync(
                    "WARNING",
                    "等待下载任务关闭超时，容器将继续退出",
                )
