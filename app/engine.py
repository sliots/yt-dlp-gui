from __future__ import annotations

import copy
import json
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import DEFAULT_CONFIG_PATH
from app.models import Channel
from app.websocket_manager import LogBroadcaster

TITLE_BYTE_LIMIT = 160
FALLBACK_TITLE_BYTE_LIMIT = 120
TITLE_FIELD_RE = re.compile(r"%\(title\)(?:\.(\d+)B|s)")
LONG_FILENAME_PATTERNS = (
    "File name too long",
    "Errno 36",
    "unable to open for writing",
)
SAFE_TITLE_SUFFIX_RE = (
    r"\s*(?:[【\[][^】\]]*(?:Ear\s*Cleaning|VTuber|for\s*Sleep|オノマトペ|心情代弁|"
    r"吐息|耳ふー|指かき|タッピング)[^】\]]*[】\]])(?:\s*[【\[][^】\]]*[】\]])*\s*$"
)
MEMBER_ONLY_PATTERNS = (
    "Join this channel to get access",
    "available to this channel's members",
)


@dataclass
class DownloadAttemptResult:
    ok: bool
    returncode: int | None = None
    filename_too_long: bool = False
    stopped: bool = False
    warning: bool = False
    hard_errors: int = 0
    member_skipped: int = 0
    output_tail: list[str] | None = None


class YtDlpEngine:
    def __init__(
        self,
        config: dict,
        broadcaster: LogBroadcaster,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ):
        self._config = config
        self.general = config["general"]
        self.limits = config["download_limits"]
        self.channels = [self._normalize_channel(ch) for ch in config.get("channels", [])]
        self._broadcaster = broadcaster
        self._progress_callback = progress_callback
        self._stop_flag = False
        self._progress_re = re.compile(r"\[download\]\s+(\d+\.?\d*)%")
        self._stats_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._stats = {
            "total_channels": 0,
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
        self._last_channel_warning = False
        self._channel_results: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _normalize_channel(channel: dict) -> dict:
        value = copy.deepcopy(channel)
        value.setdefault("channel_id", str(uuid4()))
        value.setdefault("filter_enabled", False)
        value.setdefault("title_filter", "")
        return value

    @property
    def yt_dlp_binary(self) -> str:
        return os.getenv("YTDLP_BIN", "/usr/local/bin/yt-dlp")

    @property
    def output_root(self) -> Path:
        return Path(os.getenv("DOWNLOAD_ROOT", self.general.get("output_base_path", "/downloads")))

    @property
    def config_root(self) -> Path:
        return Path(os.getenv("CONFIG_ROOT", Path(DEFAULT_CONFIG_PATH).parent))

    def request_stop(self) -> None:
        self._stop_flag = True
        self._set_stat("stopped", True)
        with self._process_lock:
            process = self._process
        if process is not None:
            threading.Thread(
                target=self._terminate_process,
                args=(process, 10),
                daemon=True,
                name="yt-dlp-stop",
            ).start()

    def reset_stop(self) -> None:
        self._stop_flag = False

    def _emit(self, msg: str, level: str = "info") -> None:
        level = level.upper()
        self._broadcaster.broadcast_sync("WARNING" if level == "WARN" else level, msg)

    def _increment_stat(self, name: str, amount: int = 1) -> None:
        with self._stats_lock:
            self._stats[name] += amount

    def _set_stat(self, name: str, value: Any) -> None:
        with self._stats_lock:
            self._stats[name] = value

    def get_run_stats(self) -> dict[str, Any]:
        with self._stats_lock:
            return dict(self._stats)

    def get_channel_results(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self._channel_results)

    def get_run_outcome(self) -> str:
        stats = self.get_run_stats()
        if stats["stopped"]:
            return "stopped"
        failed = stats["hard_errors"] > 0
        if failed and stats["completed_channels"] == 0:
            return "failed"
        if failed or stats["warning_channels"] > 0:
            return "partial"
        return "success"

    def _report_progress(self, progress: dict[str, Any]) -> None:
        if self._progress_callback is not None:
            try:
                self._progress_callback(progress)
            except Exception:
                self._emit("进度状态更新失败", "warning")
        self._broadcaster.broadcast_sync("PROGRESS", json.dumps(progress))

    def _get_download_format(self, dl_type: str) -> str:
        formats = {
            "audio": "bestaudio",
            "video": "bestvideo+bestaudio/best",
        }
        return formats.get(dl_type, "bestvideo+bestaudio/best")

    def _get_match_filter(self, channel: dict | bool) -> str:
        base_filter = "!is_live & live_status!=is_upcoming"
        if isinstance(channel, bool):
            channel = {
                "filter_enabled": channel,
                "title_filter": r"(?i)(ASMR|安眠|asmr|KU100|Asmr)",
            }
        if not channel.get("filter_enabled", False):
            return base_filter
        pattern = str(channel.get("title_filter") or "").strip()
        if not pattern:
            return base_filter
        return f"{base_filter} & title ~= {pattern}"

    def _get_download_limit(self, is_first: bool) -> tuple[str, int]:
        if is_first:
            return self.general["dateafter"], self.limits["first_run_limit"]
        return self.general["dateafter"], self.limits["normal_limit"]

    @staticmethod
    def _normalize_filename_template(
        template: str,
        *,
        title_field: str = "title",
        byte_limit: int = TITLE_BYTE_LIMIT,
    ) -> str:
        def _replace(match: re.Match[str]) -> str:
            configured_limit = match.group(1)
            limit = byte_limit
            if configured_limit is not None:
                limit = min(int(configured_limit), byte_limit)
            return f"%({title_field}).{limit}B"

        return TITLE_FIELD_RE.sub(_replace, template)

    @staticmethod
    def _template_uses_title(template: str) -> bool:
        return TITLE_FIELD_RE.search(template) is not None

    @staticmethod
    def _line_has_filename_too_long(line: str) -> bool:
        return any(pattern in line for pattern in LONG_FILENAME_PATTERNS)

    @staticmethod
    def _parse_time(time_str: str) -> float:
        if time_str.endswith("s"):
            return float(time_str[:-1])
        if time_str.endswith("m"):
            return float(time_str[:-1]) * 60
        if time_str.endswith("h"):
            return float(time_str[:-1]) * 3600
        return float(time_str)

    def _channel_url(self, channel: dict) -> str:
        try:
            return Channel.model_validate(channel).channel_url
        except Exception:
            yt_id = channel.get("youtube_id", "")
            if str(yt_id).startswith("UC"):
                base = f"https://www.youtube.com/channel/{yt_id}"
            else:
                base = f"https://www.youtube.com/@{str(yt_id).removeprefix('@')}"
            return f"{base}/{channel.get('vid_type', 'videos')}"

    def _network_args(self) -> list[str]:
        args: list[str] = []
        if self.general.get("quiet_mode", False):
            args.append("--quiet")

        if self.general.get("cookies_source", "file") == "browser":
            browser = self.general.get("cookies_browser", "firefox")
            profile = self.general.get("cookies_browser_profile", "")
            container = self.general.get("cookies_browser_container", "")
            spec = browser
            if profile:
                spec += f":{profile}"
            if container:
                spec += f"::{container}"
            args.extend(["--cookies-from-browser", spec])
        else:
            cookies_path = self.general.get(
                "cookies_file_path",
                str(self.config_root / "cookies.txt"),
            )
            args.extend(["--cookies", cookies_path])

        if self.general.get("po_token_enabled", True):
            base_url = self.general.get(
                "po_token_base_url",
                "http://bgutil-provider:4416",
            ).rstrip("/")
            args.extend(
                [
                    "--plugin-dirs",
                    "/opt/yt-dlp-plugins",
                    "--extractor-args",
                    "youtube:player_client=web_creator",
                    "--extractor-args",
                    f"youtubepot-bgutilhttp:base_url={base_url}",
                ]
            )
        if self.general.get("proxy_url"):
            args.extend(["--proxy", self.general["proxy_url"]])
        return args

    def _build_command(
        self,
        channel: dict,
        output_template: str,
        *,
        fallback_short_title: bool = False,
        test_only: bool = False,
    ) -> list[str]:
        channel = self._normalize_channel(channel)
        folder = channel["folder_name"]
        is_first = channel.get("is_first", False)
        output_path = self.output_root / folder / output_template
        archive_path = self.config_root / Path(str(self.general["download_archive"])).name
        dateafter, download_limit = self._get_download_limit(is_first)

        cmd = [
            self.yt_dlp_binary,
            "--no-abort-on-error",
            "--newline",
            "--continue",
            *self._network_args(),
        ]

        if fallback_short_title:
            cmd.extend(
                [
                    "--parse-metadata",
                    "title:safe_title",
                    "--replace-in-metadata",
                    "safe_title",
                    SAFE_TITLE_SUFFIX_RE,
                    "",
                ]
            )
        if test_only:
            cmd.extend(["--simulate", "--playlist-end", "1"])

        cmd.extend(
            [
                "--dateafter",
                dateafter,
                "--download-archive",
                str(archive_path),
                "--embed-metadata",
                "--ffmpeg-location",
                os.getenv("FFMPEG_WRAPPER_DIR", "/opt/ffmpeg-wrapper"),
                "--match-filter",
                self._get_match_filter(channel),
                "--sleep-requests",
                str(self.general["sleep_requests"]),
                "--yes-playlist",
                "--extractor-args",
                "youtubetab:skip=authcheck",
                "-I",
                f"1:{download_limit}",
                "-f",
                self._get_download_format(channel["dl_type"]),
                "-o",
                str(output_path),
                self._channel_url(channel),
            ]
        )
        return cmd

    @staticmethod
    def _process_group(process: subprocess.Popen) -> int | None:
        if os.name == "nt":
            return None
        try:
            return os.getpgid(process.pid)
        except (AttributeError, ProcessLookupError, OSError):
            return None

    def _signal_process(self, process: subprocess.Popen, sig: int) -> None:
        group = self._process_group(process)
        killpg = getattr(os, "killpg", None)
        try:
            if group is not None and killpg is not None:
                killpg(group, sig)
            elif os.name == "nt":
                process.terminate()
            else:
                process.send_signal(sig)
        except (ProcessLookupError, OSError):
            pass

    def _terminate_process(self, process: subprocess.Popen, grace_seconds: int = 10) -> None:
        if process.poll() is not None:
            return
        self._signal_process(process, signal.SIGTERM)
        try:
            process.wait(timeout=grace_seconds)
            return
        except subprocess.TimeoutExpired:
            pass
        self._signal_process(process, getattr(signal, "SIGKILL", signal.SIGTERM))
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._emit("进程组终止失败，请检查容器资源限制", "error")

    def _run_download_attempt(
        self,
        cmd: list[str],
        *,
        channel_id: str,
        folder: str,
        label: str,
        index: int,
        total: int,
        is_first: bool,
    ) -> DownloadAttemptResult:
        process = None
        timer: threading.Timer | None = None
        filename_too_long = False
        warning = False
        hard_errors = 0
        member_skipped = 0
        timed_out = threading.Event()
        output_tail: list[str] = []

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=os.name != "nt",
            )
            with self._process_lock:
                self._process = process

            timeout_minutes = (
                self.general["first_run_timeout"]
                if is_first
                else self.general["normal_timeout"]
            )

            def _on_timeout() -> None:
                if process.poll() is None:
                    timed_out.set()
                    self._terminate_process(process, 10)

            timer = threading.Timer(timeout_minutes * 60, _on_timeout)
            timer.start()

            if process.stdout is None:
                raise RuntimeError("yt-dlp stdout pipe was not created")
            for line in iter(process.stdout.readline, ""):
                if self._stop_flag:
                    self._terminate_process(process, 10)
                    self._emit("用户已中断下载", "warning")
                    return DownloadAttemptResult(
                        ok=False,
                        filename_too_long=filename_too_long,
                        stopped=True,
                        warning=warning,
                        hard_errors=hard_errors,
                        member_skipped=member_skipped,
                        output_tail=output_tail,
                    )

                line = line.strip()
                if not line:
                    continue
                output_tail.append(line)
                output_tail = output_tail[-20:]

                progress_match = self._progress_re.search(line)
                if progress_match:
                    self._report_progress(
                        {
                            "type": "progress",
                            "percent": float(progress_match.group(1)),
                            "channel_index": index,
                            "channel_total": total,
                            "channel_id": channel_id,
                            "channel_label": label,
                        }
                    )
                    continue

                if self._line_has_filename_too_long(line):
                    filename_too_long = True

                if any(pattern in line for pattern in MEMBER_ONLY_PATTERNS):
                    member_skipped += 1
                    warning = True
                    self._increment_stat("member_skipped")
                    self._emit(f"[{folder}] {line}", "skip")
                elif self._line_has_filename_too_long(line):
                    warning = True
                    self._emit(f"[{folder}] {line}", "warning")
                elif "[download] Destination:" in line:
                    self._increment_stat("downloaded_files")
                    self._emit(line, "info")
                elif "has already been recorded in the archive" in line:
                    self._increment_stat("archive_skipped")
                    self._emit(line, "info")
                elif "does not pass filter" in line:
                    self._increment_stat("filtered_skipped")
                    self._emit(line, "info")
                elif "[download]" in line:
                    self._emit(line, "info")
                elif "ERROR" in line or "error" in line:
                    warning = True
                    hard_errors += 1
                    self._increment_stat("hard_errors")
                    self._emit(f"[{folder}] {line}", "error")
                elif "WARNING" in line:
                    warning = True
                    self._emit(f"[{folder}] {line}", "warning")
                else:
                    self._emit(line, "detail")

            process.wait()
            if timed_out.is_set():
                warning = True
                hard_errors += 1
                self._increment_stat("hard_errors")
                self._emit(f"[{folder}] 下载超时，已终止 yt-dlp", "error")

        except FileNotFoundError:
            self._increment_stat("hard_errors")
            self._emit(f"[{folder}] 找不到 yt-dlp，请确认 {self.yt_dlp_binary} 已安装", "error")
            return DownloadAttemptResult(
                ok=False,
                filename_too_long=filename_too_long,
                warning=True,
                hard_errors=1,
                output_tail=output_tail,
            )
        except Exception as exc:
            self._increment_stat("hard_errors")
            self._emit(f"[{folder}] 下载错误：{exc}", "error")
            return DownloadAttemptResult(
                ok=False,
                filename_too_long=filename_too_long,
                warning=True,
                hard_errors=1,
                output_tail=output_tail,
            )
        finally:
            if timer is not None:
                timer.cancel()
            with self._process_lock:
                if self._process is process:
                    self._process = None
            if process is not None:
                if process.poll() is None:
                    self._terminate_process(process, 2)
                if process.stdout is not None and hasattr(process.stdout, "close"):
                    process.stdout.close()

        return DownloadAttemptResult(
            ok=process.returncode == 0 if process else False,
            returncode=process.returncode if process else None,
            filename_too_long=filename_too_long,
            warning=warning,
            hard_errors=hard_errors,
            member_skipped=member_skipped,
            output_tail=output_tail,
        )

    def download_channel(self, channel: dict, index: int, total: int) -> bool:
        self._last_channel_warning = False
        if self._stop_flag:
            return False

        channel = self._normalize_channel(channel)
        channel_id = str(channel["channel_id"])
        folder = channel["folder_name"]
        yt_id = channel["youtube_id"]
        vid_type = channel["vid_type"]
        label = f"{folder} ({yt_id}/{vid_type})"
        started_at = datetime.now(UTC)
        started_monotonic = time.monotonic()
        stats_before = self.get_run_stats()
        self._channel_results[channel_id] = {
            "channel_id": channel_id,
            "state": "running",
            "percent": 0.0,
            "last_started_at": started_at.isoformat(),
        }
        self._report_progress(
            {
                "type": "progress",
                "percent": 0.0,
                "channel_index": index,
                "channel_total": total,
                "channel_id": channel_id,
                "channel_label": label,
            }
        )
        self._emit(f"[{index + 1}/{total}] 开始下载：{label}", "info")

        base_template = self.general["filename_format"]
        output_template = self._normalize_filename_template(base_template)
        cmd = self._build_command(channel, output_template)
        result = self._run_download_attempt(
            cmd,
            channel_id=channel_id,
            folder=folder,
            label=label,
            index=index,
            total=total,
            is_first=channel.get("is_first", False),
        )

        if result.filename_too_long and self._template_uses_title(base_template):
            first_result = result
            self._emit("检测到文件名过长，启用短文件名降级重试", "warning")
            fallback_template = self._normalize_filename_template(
                base_template,
                title_field="safe_title",
                byte_limit=FALLBACK_TITLE_BYTE_LIMIT,
            )
            fallback_cmd = self._build_command(
                channel,
                fallback_template,
                fallback_short_title=True,
            )
            result = self._run_download_attempt(
                fallback_cmd,
                channel_id=channel_id,
                folder=folder,
                label=label,
                index=index,
                total=total,
                is_first=channel.get("is_first", False),
            )
            result.warning = result.warning or first_result.warning
            result.hard_errors += first_result.hard_errors
            result.member_skipped += first_result.member_skipped
            if result.stopped or not result.ok:
                self._last_channel_warning = True
                self._record_channel_result(
                    channel,
                    result,
                    started_at,
                    started_monotonic,
                    stats_before,
                    outcome="stopped" if result.stopped else "error",
                )
                return False
        elif result.stopped or not result.ok:
            self._last_channel_warning = result.warning or result.hard_errors > 0
            self._record_channel_result(
                channel,
                result,
                started_at,
                started_monotonic,
                stats_before,
                outcome="stopped" if result.stopped else "error",
            )
            return False

        outcome = "success"
        if result.returncode != 0 or result.hard_errors or result.warning:
            outcome = "warning"
            self._last_channel_warning = True
            self._emit(
                f"完成但有警告：{label}，返回码 {result.returncode}",
                "warning",
            )
        else:
            self._emit(f"完成：{label}", "success")

        self._record_channel_result(
            channel,
            result,
            started_at,
            started_monotonic,
            stats_before,
            outcome=outcome,
        )
        if channel.get("is_first", False) and outcome == "success":
            channel["is_first"] = False

        sleep_sec = self._parse_time(self.general["sleep_time"])
        if sleep_sec > 0 and not self._stop_flag:
            self._emit(f"休眠 {sleep_sec:.0f} 秒", "detail")
            deadline = time.monotonic() + sleep_sec
            while time.monotonic() < deadline:
                if self._stop_flag:
                    return False
                time.sleep(min(1.0, deadline - time.monotonic()))

        return True

    def _record_channel_result(
        self,
        channel: dict,
        result: DownloadAttemptResult,
        started_at: datetime,
        started_monotonic: float,
        stats_before: dict[str, Any],
        *,
        outcome: str,
    ) -> None:
        channel_id = str(channel["channel_id"])
        stats = self.get_run_stats()
        self._channel_results[channel_id] = {
            "channel_id": channel_id,
            "state": outcome,
            "percent": 100.0 if outcome == "success" else 0.0,
            "last_started_at": started_at.isoformat(),
            "last_finished_at": datetime.now(UTC).isoformat(),
            "last_outcome": outcome,
            "last_error": (result.output_tail or [""])[-1] if outcome != "success" else "",
            "downloaded_files": stats["downloaded_files"] - stats_before["downloaded_files"],
            "archive_skipped": stats["archive_skipped"] - stats_before["archive_skipped"],
            "filtered_skipped": stats["filtered_skipped"] - stats_before["filtered_skipped"],
            "member_skipped": result.member_skipped,
            "duration_seconds": round(time.monotonic() - started_monotonic, 1),
        }

    def run_all(self) -> bool:
        if not self.channels:
            return True

        active_channels = [ch for ch in self.channels if ch.get("enabled", True)]
        self._set_stat("total_channels", len(active_channels))
        if not active_channels:
            self._emit("没有启用的频道", "warning")
            return True

        total = len(active_channels)
        self._emit(f"开始下载轮次，共 {total} 个任务", "info")

        for i, channel in enumerate(active_channels):
            ok = self.download_channel(channel, i, total)
            self._increment_stat("processed_channels")
            channel_result = self._channel_results.get(str(channel.get("channel_id")), {})
            outcome = channel_result.get("state")
            if outcome == "success":
                self._increment_stat("completed_channels")
            elif outcome in {"warning", "error"}:
                self._increment_stat("warning_channels")
            if not ok:
                if self._stop_flag:
                    self._set_stat("stopped", True)
                    self._emit("下载轮次已中断", "warning")
                    return False
                self._emit("频道下载失败，继续处理后续频道", "warning")

        self._emit(
            f"下载轮次完成：{datetime.now(UTC).astimezone().strftime('%H:%M:%S')}",
            "success",
        )
        return True

    def get_channel_state(self) -> list[dict]:
        return copy.deepcopy(self.channels)

    def resolve_channel(self, channel: dict, timeout: int = 120) -> dict[str, Any]:
        candidate = self._normalize_channel(channel)
        cmd = [
            self.yt_dlp_binary,
            "--dump-single-json",
            "--flat-playlist",
            "--playlist-end",
            "1",
            *self._network_args(),
            self._channel_url(candidate),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "message": str(exc)}
        if result.returncode != 0:
            return {
                "ok": False,
                "message": (result.stderr or result.stdout).strip()[-1000:],
            }
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"ok": False, "message": "yt-dlp 未返回可解析的频道信息"}
        channel_id = payload.get("channel_id") or payload.get("id")
        title = payload.get("channel") or payload.get("title")
        if not isinstance(channel_id, str) or not re.fullmatch(
            r"UC[A-Za-z0-9_-]{20,}",
            channel_id,
        ):
            return {"ok": False, "message": "未获取到有效的 UC 频道 ID"}
        return {
            "ok": True,
            "youtube_channel_id": channel_id,
            "channel_title": title,
            "message": "",
        }

    def test_channel(self, channel: dict, timeout: int = 120) -> dict[str, Any]:
        candidate = self._normalize_channel(channel)
        candidate["is_first"] = False
        cmd = self._build_command(
            candidate,
            self._normalize_filename_template(self.general["filename_format"]),
            test_only=True,
        )
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": "频道检测超时"}
        except OSError as exc:
            return {"ok": False, "message": str(exc)}
        output = (result.stdout or result.stderr).strip()
        return {
            "ok": result.returncode == 0,
            "message": output[-2000:] or ("检测成功" if result.returncode == 0 else "检测失败"),
        }
