from __future__ import annotations

import copy
import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

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
        self.channels = copy.deepcopy(config.get("channels", []))
        self._broadcaster = broadcaster
        self._progress_callback = progress_callback
        self._stop_flag = False
        self._progress_re = re.compile(r"\[download\]\s+(\d+\.?\d*)%")
        self._stats_lock = threading.Lock()
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

    def request_stop(self) -> None:
        self._stop_flag = True
        self._set_stat("stopped", True)

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

    def _report_progress(self, progress: dict[str, Any]) -> None:
        if self._progress_callback is not None:
            try:
                self._progress_callback(progress)
            except Exception:
                pass
        self._broadcaster.broadcast_sync("PROGRESS", json.dumps(progress))

    def _get_download_format(self, dl_type: str) -> str:
        formats = {
            "audio": "bestaudio",
            "video": "bestvideo+bestaudio/best",
        }
        return formats.get(dl_type, "bestvideo+bestaudio/best")

    def _get_match_filter(self, is_regex: bool) -> str:
        base_filter = "!is_live & live_status!=is_upcoming"
        if is_regex:
            regex_filter = "& title ~= (?i)(ASMR|安眠|asmr|KU100|Asmr)"
            return f"{base_filter} {regex_filter}"
        return base_filter

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
        elif time_str.endswith("m"):
            return float(time_str[:-1]) * 60
        elif time_str.endswith("h"):
            return float(time_str[:-1]) * 3600
        return float(time_str)

    def _build_command(
        self,
        channel: dict,
        output_template: str,
        *,
        fallback_short_title: bool = False,
    ) -> list[str]:
        folder = channel["folder_name"]
        yt_id = channel["youtube_id"]
        vid_type = channel["vid_type"]
        dl_type = channel["dl_type"]
        is_regex = channel.get("is_regex", False)
        is_first = channel.get("is_first", False)

        download_format = self._get_download_format(dl_type)
        match_filter = self._get_match_filter(is_regex)
        dateafter, download_limit = self._get_download_limit(is_first)

        archive_path = str(os.path.join("config", self.general["download_archive"]))
        output_path = os.path.join(
            self.general["output_base_path"],
            folder,
            output_template,
        )
        url = f"https://www.youtube.com/@{yt_id}/{vid_type}"

        cmd = [
            "/usr/local/bin/yt-dlp",
            "--no-abort-on-error",
            "--newline",
            "--continue",
        ]

        if fallback_short_title:
            cmd.extend([
                "--parse-metadata", "title:safe_title",
                "--replace-in-metadata", "safe_title", SAFE_TITLE_SUFFIX_RE, "",
            ])

        if self.general.get("quiet_mode", False):
            cmd.append("--quiet")

        cookies_source = self.general.get("cookies_source", "file")
        if cookies_source == "browser":
            browser = self.general.get("cookies_browser", "firefox")
            profile = self.general.get("cookies_browser_profile", "")
            container = self.general.get("cookies_browser_container", "")
            spec = browser
            if profile:
                spec += f":{profile}"
            if container:
                spec += f"::{container}"
            cmd.extend(["--cookies-from-browser", spec])
        else:
            cookies_path = self.general.get("cookies_file_path", "/app/config/cookies.txt")
            cmd.extend(["--cookies", cookies_path])

        if self.general.get("po_token_enabled", True):
            base_url = self.general.get(
                "po_token_base_url", "http://bgutil-provider:4416",
            ).rstrip("/")
            cmd.extend([
                "--plugin-dirs", "/opt/yt-dlp-plugins",
                "--extractor-args", "youtube:player_client=web_creator",
                "--extractor-args", f"youtubepot-bgutilhttp:base_url={base_url}",
            ])

        cmd.extend([
            "--dateafter", dateafter,
            "--download-archive", archive_path,
            "--embed-metadata",
            "--ffmpeg-location", "/opt/ffmpeg-wrapper",
            "--match-filter", match_filter,
            "--proxy", self.general["proxy_url"],
            "--sleep-requests", str(self.general["sleep_requests"]),
            "--yes-playlist",
            "--extractor-args", "youtubetab:skip=authcheck",
            "-I", f"1:{download_limit}",
            "-f", download_format,
            "-o", output_path,
            url,
        ])
        return cmd

    def _run_download_attempt(
        self,
        cmd: list[str],
        *,
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

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )

            timeout_minutes = (
                self.general["first_run_timeout"]
                if is_first
                else self.general["normal_timeout"]
            )
            timeout_seconds = timeout_minutes * 60

            def _on_timeout() -> None:
                if process is not None and process.poll() is None:
                    timed_out.set()
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    except ProcessLookupError:
                        pass

            timer = threading.Timer(timeout_seconds, _on_timeout)
            timer.start()

            for line in iter(process.stdout.readline, ""):
                if self._stop_flag:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    self._emit("⏹ 用户中断下载", "warning")
                    return DownloadAttemptResult(
                        ok=False,
                        filename_too_long=filename_too_long,
                        stopped=True,
                        warning=warning,
                        hard_errors=hard_errors,
                        member_skipped=member_skipped,
                    )

                line = line.strip()
                if not line:
                    continue

                progress_match = self._progress_re.search(line)
                if progress_match:
                    percent = float(progress_match.group(1))
                    progress = {
                        "type": "progress",
                        "percent": percent,
                        "channel_index": index,
                        "channel_total": total,
                        "channel_label": label,
                    }
                    self._report_progress(progress)
                    continue

                if self._line_has_filename_too_long(line):
                    filename_too_long = True

                if any(pattern in line for pattern in MEMBER_ONLY_PATTERNS):
                    member_skipped += 1
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
                self._emit(f"[{folder}] ✖ 下载超时，已终止 yt-dlp", "error")

        except FileNotFoundError:
            self._increment_stat("hard_errors")
            self._emit(f"[{folder}] ✖ 找不到 yt-dlp，请确认 /usr/local/bin/yt-dlp 已安装", "error")
            return DownloadAttemptResult(
                ok=False, filename_too_long=filename_too_long,
                warning=True, hard_errors=1,
            )
        except Exception as e:
            self._increment_stat("hard_errors")
            self._emit(f"[{folder}] ✖ 下载错误：{e}", "error")
            return DownloadAttemptResult(
                ok=False, filename_too_long=filename_too_long,
                warning=True, hard_errors=1,
            )
        finally:
            if timer is not None:
                timer.cancel()

        return DownloadAttemptResult(
            ok=True,
            returncode=process.returncode if process else None,
            filename_too_long=filename_too_long,
            warning=warning,
            hard_errors=hard_errors,
            member_skipped=member_skipped,
        )

    def download_channel(self, channel: dict, index: int, total: int) -> bool:
        self._last_channel_warning = False
        if self._stop_flag:
            return False

        folder = channel["folder_name"]
        yt_id = channel["youtube_id"]
        vid_type = channel["vid_type"]
        label = f"{folder} ({yt_id}/{vid_type})"
        self._report_progress({
            "type": "progress",
            "percent": 0.0,
            "channel_index": index,
            "channel_total": total,
            "channel_label": label,
        })
        self._emit(f"▶ [{index+1}/{total}] 开始下载：{label}", "info")

        base_template = self.general["filename_format"]
        output_template = self._normalize_filename_template(base_template)
        cmd = self._build_command(channel, output_template)
        result = self._run_download_attempt(
            cmd,
            folder=folder,
            label=label,
            index=index,
            total=total,
            is_first=channel.get("is_first", False),
        )

        if result.stopped or not result.ok:
            self._last_channel_warning = result.warning
            return False

        if result.filename_too_long and self._template_uses_title(base_template):
            first_result = result
            self._emit(
                "⚠ 检测到文件名过长，启用短文件名降级重试：移除末尾标签块并限制标题 120B",
                "warning",
            )
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
                return False

        if result.returncode == 0:
            self._emit(f"✔ 完成：{label}", "success")
        elif result.member_skipped and not result.hard_errors and not result.warning:
            self._emit(f"✔ 完成（会员内容已跳过）：{label}", "success")
        elif result.returncode is not None:
            result.warning = True
            self._emit(f"⚠ 完成（返回码 {result.returncode}）：{label}", "warning")

        self._last_channel_warning = result.warning or result.hard_errors > 0

        sleep_sec = self._parse_time(self.general["sleep_time"])
        if sleep_sec > 0 and not self._stop_flag:
            self._emit(f"⏳ 休眠 {sleep_sec:.0f} 秒...", "detail")
            for _ in range(int(sleep_sec)):
                if self._stop_flag:
                    return False
                time.sleep(1)

        if channel.get("is_first", False):
            channel["is_first"] = False

        return True

    def run_all(self) -> bool:
        if not self.channels:
            return True

        active_channels = [ch for ch in self.channels if ch.get("enabled", True)]
        self._set_stat("total_channels", len(active_channels))
        if not active_channels:
            self._emit("⚠ 没有启用的频道", "warning")
            return True

        ordered_channels = sorted(
            active_channels,
            key=lambda c: 0 if c.get("is_first", False) else 1,
        )
        total = len(ordered_channels)
        self._emit(f"═══ 开始下载轮次 ═══  共 {total} 个任务", "info")

        for i, ch in enumerate(ordered_channels):
            ok = self.download_channel(ch, i, total)
            self._increment_stat("processed_channels")
            if self._last_channel_warning:
                self._increment_stat("warning_channels")
            elif ok:
                self._increment_stat("completed_channels")
            if not ok:
                if self._stop_flag:
                    self._set_stat("stopped", True)
                self._emit("═══ 下载已中断 ═══", "warning")
                return False

        self._emit(
            f"═══ 下载轮次完成 ═══  {datetime.now().strftime('%H:%M:%S')}",
            "success",
        )
        return True

    def get_channel_state(self) -> list[dict]:
        return copy.deepcopy(self.channels)
