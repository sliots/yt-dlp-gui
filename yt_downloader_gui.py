#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube 视频/音频下载器 - GUI 版本
使用 yt-dlp + PySide6 构建现代化界面
支持系统托盘最小化、实时日志、频道管理
"""

import subprocess
import threading
import time
import sys
import os
import re
from pathlib import Path
from typing import Optional
from datetime import datetime

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        print("错误：需要安装 tomli 库（Python < 3.11）")
        print("请运行：pip install tomli")
        sys.exit(1)

try:
    import tomli_w
except ImportError:
    tomli_w = None

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QTextEdit, QLineEdit, QComboBox,
    QCheckBox, QSpinBox, QGroupBox, QFormLayout, QFileDialog,
    QSystemTrayIcon, QMenu, QMessageBox, QProgressBar,
    QSplitter, QFrame, QStyle, QAbstractItemView, QDialog,
    QDialogButtonBox, QToolBar, QStatusBar, QSizePolicy
)
from PySide6.QtCore import (
    Qt, QTimer, Signal, QObject, QSize, QThread, QSettings
)
from PySide6.QtGui import (
    QIcon, QFont, QColor, QPalette, QAction, QPixmap, QPainter,
    QTextCursor, QFontDatabase
)


# ─────────────────────────── 样式表 ───────────────────────────

STYLESHEET = """
/* ── 全局 ── */
QMainWindow, QDialog {
    background-color: #1e1e2e;
}

QWidget {
    color: #cdd6f4;
    font-size: 13px;
}

/* ── 标签页 ── */
QTabWidget::pane {
    border: 1px solid #313244;
    border-radius: 8px;
    background-color: #1e1e2e;
    top: -1px;
}

QTabBar::tab {
    background-color: #181825;
    color: #a6adc8;
    padding: 10px 24px;
    margin-right: 2px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    border: 1px solid #313244;
    border-bottom: none;
    font-weight: 500;
}

QTabBar::tab:selected {
    background-color: #1e1e2e;
    color: #cba6f7;
    border-bottom: 2px solid #cba6f7;
}

QTabBar::tab:hover:!selected {
    background-color: #262637;
    color: #cdd6f4;
}

/* ── 表格 ── */
QTableWidget {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 8px;
    gridline-color: #313244;
    selection-background-color: #45475a;
    selection-color: #cdd6f4;
    outline: none;
}

QTableWidget::item {
    padding: 6px 10px;
    border-bottom: 1px solid #262637;
}

QTableWidget::item:hover {
    background-color: #262637;
}

QHeaderView::section {
    background-color: #181825;
    color: #a6adc8;
    padding: 8px 10px;
    border: none;
    border-bottom: 2px solid #313244;
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
}

/* ── 按钮 ── */
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: 500;
    min-height: 20px;
}

QPushButton:hover {
    background-color: #45475a;
    border-color: #585b70;
}

QPushButton:pressed {
    background-color: #585b70;
}

QPushButton:disabled {
    background-color: #262637;
    color: #585b70;
    border-color: #313244;
}

QPushButton#primaryBtn {
    background-color: #cba6f7;
    color: #1e1e2e;
    border: none;
    font-weight: 600;
}

QPushButton#primaryBtn:hover {
    background-color: #b4befe;
}

QPushButton#primaryBtn:pressed {
    background-color: #a6adc8;
}

QPushButton#primaryBtn:disabled {
    background-color: #585b70;
    color: #45475a;
}

QPushButton#dangerBtn {
    background-color: #f38ba8;
    color: #1e1e2e;
    border: none;
    font-weight: 600;
}

QPushButton#dangerBtn:hover {
    background-color: #eba0ac;
}

QPushButton#stopBtn {
    background-color: #fab387;
    color: #1e1e2e;
    border: none;
    font-weight: 600;
}

QPushButton#stopBtn:hover {
    background-color: #f9e2af;
}

/* ── 输入框 ── */
QLineEdit, QSpinBox, QComboBox {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 8px 12px;
    color: #cdd6f4;
    selection-background-color: #cba6f7;
    selection-color: #1e1e2e;
}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #cba6f7;
}

QComboBox::drop-down {
    border: none;
    padding-right: 8px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid #a6adc8;
    margin-right: 8px;
}

QComboBox QAbstractItemView {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
    selection-background-color: #45475a;
    outline: none;
}

/* ── 日志区域 ── */
QTextEdit#logArea {
    background-color: #11111b;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 10px;
    font-family: 'Cascadia Code', 'Consolas', 'SF Mono', monospace;
    font-size: 12px;
    color: #a6adc8;
    selection-background-color: #45475a;
}

/* ── 分组框 ── */
QGroupBox {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 8px;
    margin-top: 16px;
    padding-top: 24px;
    font-weight: 600;
    color: #cba6f7;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 4px 12px;
    left: 12px;
}

/* ── 复选框 ── */
QCheckBox {
    spacing: 8px;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #45475a;
    border-radius: 4px;
    background-color: #181825;
}

QCheckBox::indicator:checked {
    background-color: #cba6f7;
    border-color: #cba6f7;
}

QCheckBox::indicator:hover {
    border-color: #cba6f7;
}

/* ── 进度条 ── */
QProgressBar {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
    text-align: center;
    color: #cdd6f4;
    height: 24px;
    font-size: 11px;
}

QProgressBar::chunk {
    background-color: #cba6f7;
    border-radius: 5px;
}

/* ── 状态栏 ── */
QStatusBar {
    background-color: #181825;
    color: #a6adc8;
    border-top: 1px solid #313244;
    font-size: 12px;
    padding: 4px;
}

/* ── 菜单 ── */
QMenu {
    background-color: #1e1e2e;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 6px;
}

QMenu::item {
    padding: 8px 32px 8px 16px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #45475a;
}

/* ── 工具栏 ── */
QToolBar {
    background-color: #181825;
    border: none;
    border-bottom: 1px solid #313244;
    padding: 6px;
    spacing: 6px;
}

/* ── 滚动条 ── */
QScrollBar:vertical {
    background-color: #181825;
    width: 10px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background-color: #45475a;
    border-radius: 5px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background-color: #585b70;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
    height: 0px;
}

QScrollBar:horizontal {
    background-color: #181825;
    height: 10px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal {
    background-color: #45475a;
    border-radius: 5px;
    min-width: 30px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #585b70;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
    width: 0px;
}

/* ── 标签 ── */
QLabel#titleLabel {
    font-size: 18px;
    font-weight: 700;
    color: #cba6f7;
}

QLabel#subtitleLabel {
    font-size: 12px;
    color: #585b70;
}

QLabel#statusRunning {
    color: #a6e3a1;
    font-weight: 600;
}

QLabel#statusStopped {
    color: #f38ba8;
    font-weight: 600;
}

QLabel#statusWaiting {
    color: #f9e2af;
    font-weight: 600;
}

/* ── 分割线 ── */
QFrame#separator {
    background-color: #313244;
    max-height: 1px;
}

/* ── 对话框 ── */
QDialogButtonBox QPushButton {
    min-width: 80px;
}
"""


# ─────────────────────── 日志信号桥 ────────────────────────

class LogBridge(QObject):
    """用于在工作线程和 GUI 线程之间传递日志消息"""
    log_message = Signal(str, str)   # (消息, 级别)
    progress_update = Signal(int, int, str)  # (当前, 总数, 描述)
    status_update = Signal(str)  # 状态文本
    download_finished = Signal()  # 单轮下载完成
    countdown_tick = Signal(int)  # 倒计时分钟数


# ─────────────────────── 下载引擎 ────────────────────────

class YtDlpEngine:
    """yt-dlp 下载引擎（从原始脚本重构）"""

    def __init__(self, config: dict, log_bridge: LogBridge):
        self.config = config
        self.general = config["general"]
        self.limits = config["download_limits"]
        self.channels = config["channels"]
        self.log = log_bridge
        self._stop_flag = False

    def request_stop(self):
        self._stop_flag = True

    def reset_stop(self):
        self._stop_flag = False

    def _emit(self, msg: str, level: str = "info"):
        self.log.log_message.emit(msg, level)

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

    def _get_download_limit(self, is_first: bool) -> tuple:
        if is_first:
            return self.general["dateafter"], self.limits["first_run_limit"]
        return self.general["dateafter"], self.limits["normal_limit"]

    @staticmethod
    def _parse_time(time_str: str) -> float:
        if time_str.endswith("s"):
            return float(time_str[:-1])
        elif time_str.endswith("m"):
            return float(time_str[:-1]) * 60
        elif time_str.endswith("h"):
            return float(time_str[:-1]) * 3600
        else:
            return float(time_str)

    def download_channel(self, channel: dict, index: int, total: int) -> bool:
        """下载单个频道，返回 False 表示被中断"""
        if self._stop_flag:
            return False

        folder = channel["folder_name"]
        yt_id = channel["youtube_id"]
        vid_type = channel["vid_type"]
        dl_type = channel["dl_type"]
        is_regex = channel.get("is_regex", False)
        is_first = channel.get("is_first", False)

        label = f"{folder} ({yt_id}/{vid_type})"
        self._emit(f"▶ [{index+1}/{total}] 开始下载：{label}", "info")
        self.log.progress_update.emit(index, total, label)

        download_format = self._get_download_format(dl_type)
        match_filter = self._get_match_filter(is_regex)
        dateafter, download_limit = self._get_download_limit(is_first)

        output_path = (
            f"{self.general['output_base_path']}/{folder}/"
            f"{self.general['filename_format']}"
        )
        url = f"https://www.youtube.com/@{yt_id}/{vid_type}"

        cmd = [
            self.general["yt_dlp_exe"],
            "--no-warnings",
            "--no-abort-on-error",
            "--newline",
        ]

        if self.general.get("quiet_mode", False):
            cmd.append("--quiet")

        cmd.extend([
            "--cookies-from-browser", self.general["browser"],
            "--dateafter", dateafter,
            "--download-archive", self.general["download_archive"],
            "--embed-metadata",
            "--ffmpeg-location", self.general["ffmpeg_exe"],
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

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            for line in iter(process.stdout.readline, ""):
                if self._stop_flag:
                    process.terminate()
                    self._emit("⏹ 用户中断下载", "warn")
                    return False
                line = line.strip()
                if line:
                    # 解析进度行
                    if "[download]" in line and "%" in line:
                        self._emit(line, "progress")
                    elif "[download]" in line:
                        self._emit(line, "info")
                    elif "ERROR" in line or "error" in line.lower():
                        self._emit(line, "error")
                    elif "WARNING" in line:
                        self._emit(line, "warn")
                    else:
                        self._emit(line, "detail")

            process.wait()
            if process.returncode == 0:
                self._emit(f"✔ 完成：{label}", "success")
            else:
                self._emit(f"⚠ 完成（返回码 {process.returncode}）：{label}", "warn")

        except FileNotFoundError:
            self._emit(f"✖ 找不到 {self.general['yt_dlp_exe']}", "error")
        except Exception as e:
            self._emit(f"✖ 下载错误：{e}", "error")

        # 休眠
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

    def run_all(self):
        """执行所有频道的下载"""
        if not self.channels:
            return True
        ordered_channels = sorted(
            self.channels,
            key=lambda c: 0 if c.get("is_first", False) else 1
        )
        total = len(ordered_channels)
        self._emit(f"═══ 开始下载轮次 ═══  共 {total} 个任务", "info")
        self.log.status_update.emit("downloading")

        for i, ch in enumerate(ordered_channels):
            if not self.download_channel(ch, i, total):
                self._emit("═══ 下载已中断 ═══", "warn")
                self.log.status_update.emit("stopped")
                return False

        self.log.progress_update.emit(total, total, "全部完成")
        self._emit(f"═══ 下载轮次完成 ═══  {datetime.now().strftime('%H:%M:%S')}", "success")
        self.log.download_finished.emit()
        return True


# ─────────────────────── 工作线程 ────────────────────────

class DownloadWorker(QThread):
    """后台下载线程"""

    def __init__(self, engine: YtDlpEngine, loop: bool = False, wait_minutes: int = 360):
        super().__init__()
        self.engine = engine
        self.loop = loop
        self.wait_minutes = wait_minutes

    def run(self):
        self.engine.reset_stop()

        if self.loop:
            while not self.engine._stop_flag:
                ok = self.engine.run_all()
                if not ok or self.engine._stop_flag:
                    break
                # 倒计时等待
                self.engine.log.status_update.emit("waiting")
                self.engine._emit(
                    f"⏰ 下一轮将在 {self.wait_minutes} 分钟后开始",
                    "info"
                )
                for remaining in range(self.wait_minutes, 0, -1):
                    if self.engine._stop_flag:
                        break
                    self.engine.log.countdown_tick.emit(remaining)
                    for _ in range(60):
                        if self.engine._stop_flag:
                            break
                        time.sleep(1)
        else:
            self.engine.run_all()

        self.engine.log.status_update.emit("stopped")

    def stop(self):
        self.engine.request_stop()


# ──────────────── 频道编辑对话框 ─────────────────

class ChannelEditDialog(QDialog):
    """添加/编辑频道对话框"""

    def __init__(self, parent=None, channel_data: Optional[dict] = None):
        super().__init__(parent)
        self.setWindowTitle("编辑频道" if channel_data else "添加频道")
        self.setMinimumWidth(460)
        self.setStyleSheet(STYLESHEET)
        self._init_ui(channel_data)

    def _init_ui(self, data):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        form.setSpacing(10)

        self.folder_edit = QLineEdit(data.get("folder_name", "") if data else "")
        form.addRow("文件夹名称：", self.folder_edit)

        self.yt_id_edit = QLineEdit(data.get("youtube_id", "") if data else "")
        form.addRow("YouTube ID：", self.yt_id_edit)

        self.vid_type_combo = QComboBox()
        self.vid_type_combo.addItems(["videos", "streams", "shorts"])
        if data:
            idx = self.vid_type_combo.findText(data.get("vid_type", "videos"))
            if idx >= 0:
                self.vid_type_combo.setCurrentIndex(idx)
        form.addRow("视频类型：", self.vid_type_combo)

        self.dl_type_combo = QComboBox()
        self.dl_type_combo.addItems(["audio", "video"])
        if data:
            idx = self.dl_type_combo.findText(data.get("dl_type", "audio"))
            if idx >= 0:
                self.dl_type_combo.setCurrentIndex(idx)
        form.addRow("下载类型：", self.dl_type_combo)

        self.regex_check = QCheckBox("使用 ASMR 正则匹配")
        self.regex_check.setChecked(data.get("is_regex", False) if data else False)
        form.addRow("", self.regex_check)

        self.first_check = QCheckBox("首次下载（大批量）")
        self.first_check.setChecked(data.get("is_first", False) if data else True)
        form.addRow("", self.first_check)

        layout.addLayout(form)

        # 按钮
        btn_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def get_data(self) -> dict:
        return {
            "folder_name": self.folder_edit.text().strip(),
            "youtube_id": self.yt_id_edit.text().strip(),
            "vid_type": self.vid_type_combo.currentText(),
            "dl_type": self.dl_type_combo.currentText(),
            "is_regex": self.regex_check.isChecked(),
            "is_first": self.first_check.isChecked(),
        }


# ─────────────────── 创建托盘图标 ──────────────────

def create_app_icon() -> QIcon:
    """创建一个简单的应用图标（紫色下载箭头）"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    # 圆形背景
    painter.setBrush(QColor("#cba6f7"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)

    # 下载箭头
    painter.setBrush(QColor("#1e1e2e"))
    painter.setPen(Qt.NoPen)
    # 竖线
    painter.drawRect(26, 14, 12, 22)
    # 箭头三角
    from PySide6.QtGui import QPolygon
    from PySide6.QtCore import QPoint
    triangle = QPolygon([
        QPoint(16, 36),
        QPoint(48, 36),
        QPoint(32, 52),
    ])
    painter.drawPolygon(triangle)

    painter.end()
    return QIcon(pixmap)


# ─────────────────────── 主窗口 ────────────────────────

class MainWindow(QMainWindow):
    """主应用窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("YT-DLP 下载管理器")
        self.resize(1080, 720)
        self.setMinimumSize(800, 500)

        self.app_icon = create_app_icon()
        self.setWindowIcon(self.app_icon)

        # 状态
        self.config = None
        self.config_path = None
        self.worker: Optional[DownloadWorker] = None
        self.log_bridge = LogBridge()
        self.current_status = "stopped"

        # 设置（记住窗口位置等）
        self.settings = QSettings("YtDlpGUI", "MainWindow")

        # 初始化 UI
        self._init_ui()
        self._init_tray()
        self._connect_signals()
        self._restore_geometry()

        # 尝试自动加载同目录的 config.toml
        auto_path = Path("config.toml")
        if auto_path.exists():
            self._load_config(str(auto_path))

    # ────────── UI 构建 ──────────

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 顶部工具栏
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))

        # 标题区域
        title_widget = QWidget()
        title_layout = QHBoxLayout(title_widget)
        title_layout.setContentsMargins(16, 8, 16, 8)

        title_lbl = QLabel("YT-DLP 下载管理器")
        title_lbl.setObjectName("titleLabel")
        title_layout.addWidget(title_lbl)

        subtitle_lbl = QLabel("v2.0  ·  基于 yt-dlp")
        subtitle_lbl.setObjectName("subtitleLabel")
        title_layout.addWidget(subtitle_lbl)
        title_layout.addStretch()

        # 加载配置按钮
        self.btn_load_config = QPushButton("📂 加载配置")
        self.btn_load_config.setCursor(Qt.PointingHandCursor)
        title_layout.addWidget(self.btn_load_config)

        # 保存配置按钮
        self.btn_save_config = QPushButton("💾 保存配置")
        self.btn_save_config.setCursor(Qt.PointingHandCursor)
        self.btn_save_config.setEnabled(False)
        title_layout.addWidget(self.btn_save_config)

        toolbar.addWidget(title_widget)
        self.addToolBar(toolbar)

        # 主内容区（标签页）
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(16, 12, 16, 12)
        content_layout.setSpacing(12)

        self.tabs = QTabWidget()
        content_layout.addWidget(self.tabs)

        # ─── Tab 1：控制面板 ───
        control_tab = QWidget()
        ctl = QVBoxLayout(control_tab)
        ctl.setSpacing(12)

        # 控制按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_run_once = QPushButton("▶  运行一次")
        self.btn_run_once.setObjectName("primaryBtn")
        self.btn_run_once.setCursor(Qt.PointingHandCursor)
        self.btn_run_once.setEnabled(False)
        btn_row.addWidget(self.btn_run_once)

        self.btn_run_loop = QPushButton("🔁  循环运行")
        self.btn_run_loop.setObjectName("primaryBtn")
        self.btn_run_loop.setCursor(Qt.PointingHandCursor)
        self.btn_run_loop.setEnabled(False)
        btn_row.addWidget(self.btn_run_loop)

        self.btn_run_first_only = QPushButton("⭐  仅首次下载")
        self.btn_run_first_only.setObjectName("primaryBtn")
        self.btn_run_first_only.setCursor(Qt.PointingHandCursor)
        self.btn_run_first_only.setEnabled(False)
        btn_row.addWidget(self.btn_run_first_only)

        self.btn_stop = QPushButton("⏹  停止")
        self.btn_stop.setObjectName("stopBtn")
        self.btn_stop.setCursor(Qt.PointingHandCursor)
        self.btn_stop.setEnabled(False)
        btn_row.addWidget(self.btn_stop)

        btn_row.addStretch()

        self.status_label = QLabel("● 已停止")
        self.status_label.setObjectName("statusStopped")
        btn_row.addWidget(self.status_label)

        ctl.addLayout(btn_row)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%v/%m — %p%")
        ctl.addWidget(self.progress_bar)

        # 日志区
        self.log_area = QTextEdit()
        self.log_area.setObjectName("logArea")
        self.log_area.setReadOnly(True)
        self.log_area.setPlaceholderText("等待开始...")
        ctl.addWidget(self.log_area, 1)

        # 日志操作行
        log_btn_row = QHBoxLayout()
        self.btn_clear_log = QPushButton("🗑 清空日志")
        self.btn_clear_log.setCursor(Qt.PointingHandCursor)
        log_btn_row.addWidget(self.btn_clear_log)
        log_btn_row.addStretch()

        self.auto_scroll_check = QCheckBox("自动滚动")
        self.auto_scroll_check.setChecked(True)
        log_btn_row.addWidget(self.auto_scroll_check)
        ctl.addLayout(log_btn_row)

        self.tabs.addTab(control_tab, "🎛  控制面板")

        # ─── Tab 2：频道管理 ───
        channel_tab = QWidget()
        ch_layout = QVBoxLayout(channel_tab)
        ch_layout.setSpacing(12)

        ch_btn_row = QHBoxLayout()
        self.btn_add_channel = QPushButton("➕ 添加频道")
        self.btn_add_channel.setCursor(Qt.PointingHandCursor)
        self.btn_add_channel.setEnabled(False)
        ch_btn_row.addWidget(self.btn_add_channel)

        self.btn_edit_channel = QPushButton("✏️ 编辑选中")
        self.btn_edit_channel.setCursor(Qt.PointingHandCursor)
        self.btn_edit_channel.setEnabled(False)
        ch_btn_row.addWidget(self.btn_edit_channel)

        self.btn_del_channel = QPushButton("🗑 删除选中")
        self.btn_del_channel.setObjectName("dangerBtn")
        self.btn_del_channel.setCursor(Qt.PointingHandCursor)
        self.btn_del_channel.setEnabled(False)
        ch_btn_row.addWidget(self.btn_del_channel)

        ch_btn_row.addStretch()

        self.channel_count_label = QLabel("共 0 个任务")
        self.channel_count_label.setObjectName("subtitleLabel")
        ch_btn_row.addWidget(self.channel_count_label)

        ch_layout.addLayout(ch_btn_row)

        self.channel_table = QTableWidget()
        self.channel_table.setColumnCount(6)
        self.channel_table.setHorizontalHeaderLabels(
            ["文件夹", "YouTube ID", "类型", "下载", "正则", "首次"]
        )
        self.channel_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self.channel_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        for col in range(2, 6):
            self.channel_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeToContents
            )
        self.channel_table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )
        self.channel_table.setSelectionMode(
            QAbstractItemView.SingleSelection
        )
        self.channel_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers
        )
        self.channel_table.verticalHeader().setVisible(False)
        self.channel_table.setAlternatingRowColors(True)
        ch_layout.addWidget(self.channel_table, 1)

        self.tabs.addTab(channel_tab, "📋  频道管理")

        # ─── Tab 3：设置 ───
        settings_tab = QWidget()
        st_layout = QVBoxLayout(settings_tab)
        st_layout.setSpacing(16)

        # 通用设置
        general_group = QGroupBox("通用设置")
        g_form = QFormLayout(general_group)
        g_form.setSpacing(10)

        self.edit_output_path = QLineEdit()
        path_row = QHBoxLayout()
        path_row.addWidget(self.edit_output_path, 1)
        self.btn_browse_output = QPushButton("浏览...")
        self.btn_browse_output.setCursor(Qt.PointingHandCursor)
        path_row.addWidget(self.btn_browse_output)
        g_form.addRow("输出目录：", path_row)

        self.edit_proxy = QLineEdit()
        g_form.addRow("代理地址：", self.edit_proxy)

        self.edit_browser = QComboBox()
        self.edit_browser.addItems(["firefox", "chrome", "edge", "opera", "brave", "chromium", "safari"])
        self.edit_browser.setEditable(True)
        g_form.addRow("浏览器（Cookie）：", self.edit_browser)

        self.edit_ytdlp_path = QLineEdit()
        g_form.addRow("yt-dlp 路径：", self.edit_ytdlp_path)

        self.edit_ffmpeg_path = QLineEdit()
        g_form.addRow("ffmpeg 路径：", self.edit_ffmpeg_path)

        st_layout.addWidget(general_group)

        # 下载设置
        dl_group = QGroupBox("下载设置")
        d_form = QFormLayout(dl_group)
        d_form.setSpacing(10)

        self.spin_normal_limit = QSpinBox()
        self.spin_normal_limit.setRange(1, 99999)
        d_form.addRow("常规下载数量上限：", self.spin_normal_limit)

        self.spin_first_limit = QSpinBox()
        self.spin_first_limit.setRange(1, 99999)
        d_form.addRow("首次下载数量上限：", self.spin_first_limit)

        self.spin_sleep_requests = QSpinBox()
        self.spin_sleep_requests.setRange(0, 120)
        d_form.addRow("请求间隔（秒）：", self.spin_sleep_requests)

        self.edit_sleep_time = QLineEdit()
        self.edit_sleep_time.setPlaceholderText("如 5s、1m、0.5h")
        d_form.addRow("任务间休眠时间：", self.edit_sleep_time)

        self.spin_wait_minutes = QSpinBox()
        self.spin_wait_minutes.setRange(1, 99999)
        self.spin_wait_minutes.setSuffix(" 分钟")
        d_form.addRow("循环等待间隔：", self.spin_wait_minutes)

        self.check_quiet = QCheckBox("安静模式（减少 yt-dlp 输出）")
        d_form.addRow("", self.check_quiet)

        self.edit_dateafter = QLineEdit()
        self.edit_dateafter.setPlaceholderText("如 20000101")
        d_form.addRow("日期过滤（dateafter）：", self.edit_dateafter)

        st_layout.addWidget(dl_group)
        st_layout.addStretch()

        self.tabs.addTab(settings_tab, "⚙  设置")

        main_layout.addWidget(content_widget, 1)

        # 状态栏
        self.statusBar().showMessage("就绪 — 请加载配置文件")

    def _init_tray(self):
        """初始化系统托盘"""
        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        self.tray_icon.setToolTip("YT-DLP 下载管理器")

        tray_menu = QMenu()

        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self._show_window)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        run_once_action = QAction("运行一次", self)
        run_once_action.triggered.connect(self._on_run_once)
        tray_menu.addAction(run_once_action)

        run_loop_action = QAction("循环运行", self)
        run_loop_action.triggered.connect(self._on_run_loop)
        tray_menu.addAction(run_loop_action)

        stop_action = QAction("停止", self)
        stop_action.triggered.connect(self._on_stop)
        tray_menu.addAction(stop_action)

        tray_menu.addSeparator()

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _connect_signals(self):
        """连接所有信号和槽"""
        self.btn_load_config.clicked.connect(self._on_load_config)
        self.btn_save_config.clicked.connect(self._on_save_config)
        self.btn_run_once.clicked.connect(self._on_run_once)
        self.btn_run_loop.clicked.connect(self._on_run_loop)
        self.btn_run_first_only.clicked.connect(self._on_run_first_only)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_clear_log.clicked.connect(self.log_area.clear)

        self.btn_add_channel.clicked.connect(self._on_add_channel)
        self.btn_edit_channel.clicked.connect(self._on_edit_channel)
        self.btn_del_channel.clicked.connect(self._on_del_channel)
        self.channel_table.doubleClicked.connect(self._on_edit_channel)

        self.btn_browse_output.clicked.connect(self._on_browse_output)

        # 日志桥信号
        self.log_bridge.log_message.connect(self._append_log)
        self.log_bridge.progress_update.connect(self._update_progress)
        self.log_bridge.status_update.connect(self._update_status)
        self.log_bridge.countdown_tick.connect(self._update_countdown)
        self.log_bridge.download_finished.connect(self._on_download_finished)

    # ────────── 配置加载/保存 ──────────

    def _on_load_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择配置文件", "",
            "TOML 文件 (*.toml);;所有文件 (*)"
        )
        if path:
            self._load_config(path)

    def _load_config(self, path: str):
        try:
            with open(path, "rb") as f:
                self.config = tomllib.load(f)
            self._ensure_config_defaults()
            self.config_path = path
            self._populate_ui_from_config()
            self._populate_channel_table()
            self._set_config_loaded(True)
            self.statusBar().showMessage(f"已加载配置：{path}")
            self._append_log(f"✔ 已加载配置：{path}", "success")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载配置失败：\n{e}")

    def _ensure_config_defaults(self):
        if self.config is None:
            return
        self.config.setdefault("general", {})
        self.config.setdefault("download_limits", {})
        channels = self.config.get("channels")
        if not isinstance(channels, list):
            self.config["channels"] = []

        g = self.config["general"]
        g.setdefault("output_base_path", "")
        g.setdefault("proxy_url", "")
        g.setdefault("browser", "firefox")
        g.setdefault("yt_dlp_exe", "./yt-dlp.exe")
        g.setdefault("ffmpeg_exe", "./ffmpeg.exe")
        g.setdefault("sleep_requests", 5)
        g.setdefault("sleep_time", "5s")
        g.setdefault("wait_time_minutes", 360)
        g.setdefault("quiet_mode", False)
        g.setdefault("dateafter", "20000101")
        g.setdefault("download_archive", "archive.txt")
        g.setdefault(
            "filename_format",
            "[%(upload_date>%Y-%m-%d)s]%(title)s [%(id)s].%(ext)s",
        )

        l = self.config["download_limits"]
        l.setdefault("normal_limit", 20)
        l.setdefault("first_run_limit", 99999)

    def _populate_ui_from_config(self):
        g = self.config["general"]
        l = self.config["download_limits"]

        self.edit_output_path.setText(g.get("output_base_path", ""))
        self.edit_proxy.setText(g.get("proxy_url", ""))
        idx = self.edit_browser.findText(g.get("browser", "firefox"))
        if idx >= 0:
            self.edit_browser.setCurrentIndex(idx)
        else:
            self.edit_browser.setEditText(g.get("browser", "firefox"))
        self.edit_ytdlp_path.setText(g.get("yt_dlp_exe", "./yt-dlp.exe"))
        self.edit_ffmpeg_path.setText(g.get("ffmpeg_exe", "./ffmpeg.exe"))

        self.spin_normal_limit.setValue(l.get("normal_limit", 20))
        self.spin_first_limit.setValue(l.get("first_run_limit", 99999))
        self.spin_sleep_requests.setValue(g.get("sleep_requests", 5))
        self.edit_sleep_time.setText(g.get("sleep_time", "5s"))
        self.spin_wait_minutes.setValue(g.get("wait_time_minutes", 360))
        self.check_quiet.setChecked(g.get("quiet_mode", False))
        self.edit_dateafter.setText(g.get("dateafter", "20000101"))

    def _populate_channel_table(self):
        channels = self.config.get("channels", [])
        self.channel_table.setRowCount(len(channels))
        for i, ch in enumerate(channels):
            self.channel_table.setItem(i, 0, QTableWidgetItem(ch.get("folder_name", "")))
            self.channel_table.setItem(i, 1, QTableWidgetItem(ch.get("youtube_id", "")))
            self.channel_table.setItem(i, 2, QTableWidgetItem(ch.get("vid_type", "")))
            self.channel_table.setItem(i, 3, QTableWidgetItem(ch.get("dl_type", "")))
            self.channel_table.setItem(i, 4, QTableWidgetItem("✔" if ch.get("is_regex") else ""))
            self.channel_table.setItem(i, 5, QTableWidgetItem("✔" if ch.get("is_first") else ""))
            # 居中
            for col in range(2, 6):
                item = self.channel_table.item(i, col)
                if item:
                    item.setTextAlignment(Qt.AlignCenter)

        self.channel_count_label.setText(f"共 {len(channels)} 个任务")

    def _set_config_loaded(self, loaded: bool):
        self.btn_run_once.setEnabled(loaded)
        self.btn_run_loop.setEnabled(loaded)
        self.btn_run_first_only.setEnabled(loaded)
        self.btn_save_config.setEnabled(loaded)
        self.btn_add_channel.setEnabled(loaded)
        self.btn_edit_channel.setEnabled(loaded)
        self.btn_del_channel.setEnabled(loaded)

    def _sync_config_from_ui(self):
        """将 UI 中的设置写回 config 字典"""
        if not self.config:
            return
        g = self.config.setdefault("general", {})
        g["output_base_path"] = self.edit_output_path.text()
        g["proxy_url"] = self.edit_proxy.text()
        g["browser"] = self.edit_browser.currentText()
        g["yt_dlp_exe"] = self.edit_ytdlp_path.text()
        g["ffmpeg_exe"] = self.edit_ffmpeg_path.text()
        g["sleep_requests"] = self.spin_sleep_requests.value()
        g["sleep_time"] = self.edit_sleep_time.text()
        g["wait_time_minutes"] = self.spin_wait_minutes.value()
        g["quiet_mode"] = self.check_quiet.isChecked()
        g["dateafter"] = self.edit_dateafter.text()

        limits = self.config.setdefault("download_limits", {})
        limits["normal_limit"] = self.spin_normal_limit.value()
        limits["first_run_limit"] = self.spin_first_limit.value()

    def _on_save_config(self):
        if not self.config:
            return
        self._sync_config_from_ui()

        if tomli_w is None:
            QMessageBox.warning(
                self, "缺少依赖",
                "保存配置需要 tomli_w 库。\n请运行：pip install tomli-w"
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "保存配置文件",
            self.config_path or "config.toml",
            "TOML 文件 (*.toml)"
        )
        if path:
            try:
                with open(path, "wb") as f:
                    tomli_w.dump(self.config, f)
                self.statusBar().showMessage(f"配置已保存：{path}")
                self._append_log(f"✔ 配置已保存：{path}", "success")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存失败：\n{e}")

    def _auto_save_config(self):
        if not self.config:
            return
        if tomli_w is None:
            QMessageBox.warning(self, "缺少依赖", "保存配置需要 tomli_w 库。\n请运行：pip install tomli-w")
            return
        target = self.config_path or "config.toml"
        try:
            with open(target, "wb") as f:
                tomli_w.dump(self.config, f)
            self.statusBar().showMessage(f"配置已自动保存：{target}")
            self._append_log(f"✔ 配置已自动保存：{target}", "success")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败：\n{e}")

    # ────────── 频道管理 ──────────

    def _on_add_channel(self):
        dlg = ChannelEditDialog(self)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            if data["folder_name"] and data["youtube_id"]:
                self.config["channels"].append(data)
                self._populate_channel_table()
                self._auto_save_config()

    def _on_edit_channel(self, _=None):
        row = self.channel_table.currentRow()
        if row < 0:
            return
        channels = self.config["channels"]
        if row >= len(channels):
            return
        dlg = ChannelEditDialog(self, channels[row])
        if dlg.exec() == QDialog.Accepted:
            channels[row] = dlg.get_data()
            self._populate_channel_table()
            self._auto_save_config()

    def _on_del_channel(self):
        row = self.channel_table.currentRow()
        if row < 0:
            return
        channels = self.config["channels"]
        if row >= len(channels):
            return
        ch = channels[row]
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定删除频道 {ch['folder_name']} ({ch['youtube_id']}) 吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            channels.pop(row)
            self._populate_channel_table()
            self._auto_save_config()

    # ────────── 下载控制 ──────────

    def _on_run_once(self):
        if self.worker and self.worker.isRunning():
            return
        self._sync_config_from_ui()
        engine = YtDlpEngine(self.config, self.log_bridge)
        self.worker = DownloadWorker(engine, loop=False)
        self.worker.start()
        self._set_running_ui(True)

    def _on_run_first_only(self):
        if self.worker and self.worker.isRunning():
            return
        self._sync_config_from_ui()
        first_channels = [ch for ch in self.config["channels"] if ch.get("is_first", False)]
        if not first_channels:
            self._append_log("⚠ 没有标记为首次下载的频道", "warn")
            return
        temp_config = dict(self.config)
        temp_config["channels"] = first_channels
        engine = YtDlpEngine(temp_config, self.log_bridge)
        self.worker = DownloadWorker(engine, loop=False)
        self.worker.start()
        self._set_running_ui(True)

    def _on_run_loop(self):
        if self.worker and self.worker.isRunning():
            return
        self._sync_config_from_ui()
        engine = YtDlpEngine(self.config, self.log_bridge)
        wait = self.config["general"].get("wait_time_minutes", 360)
        self.worker = DownloadWorker(engine, loop=True, wait_minutes=wait)
        self.worker.start()
        self._set_running_ui(True)

    def _on_stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self._append_log("⏹ 正在停止...", "warn")
            self.btn_stop.setEnabled(False)

    def _set_running_ui(self, running: bool):
        self.btn_run_once.setEnabled(not running)
        self.btn_run_loop.setEnabled(not running)
        self.btn_run_first_only.setEnabled(not running)
        self.btn_stop.setEnabled(running)

    # ────────── 日志与状态更新 ──────────

    def _append_log(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")

        color_map = {
            "info": "#89b4fa",
            "success": "#a6e3a1",
            "warn": "#f9e2af",
            "error": "#f38ba8",
            "detail": "#6c7086",
            "progress": "#cba6f7",
        }
        color = color_map.get(level, "#cdd6f4")

        html = (
            f'<span style="color:#585b70">[{timestamp}]</span> '
            f'<span style="color:{color}">{message}</span>'
        )
        self.log_area.append(html)

        if self.auto_scroll_check.isChecked():
            cursor = self.log_area.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.log_area.setTextCursor(cursor)

    def _update_progress(self, current: int, total: int, desc: str):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_bar.setFormat(f"{current}/{total} — {desc}")

    def _update_status(self, status: str):
        self.current_status = status
        if status == "downloading":
            self.status_label.setText("● 下载中")
            self.status_label.setObjectName("statusRunning")
            self.tray_icon.setToolTip("YT-DLP — 下载中...")
            self._set_running_ui(True)
        elif status == "waiting":
            self.status_label.setText("● 等待中")
            self.status_label.setObjectName("statusWaiting")
            self.tray_icon.setToolTip("YT-DLP — 等待下一轮")
            self._set_running_ui(True)
        else:
            self.status_label.setText("● 已停止")
            self.status_label.setObjectName("statusStopped")
            self.tray_icon.setToolTip("YT-DLP — 已停止")
            self._set_running_ui(False)

        # 刷新样式
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _update_countdown(self, remaining_minutes: int):
        if not self.config:
            return
        self.progress_bar.setMaximum(self.config["general"].get("wait_time_minutes", 360))
        total = self.progress_bar.maximum()
        elapsed = total - remaining_minutes
        self.progress_bar.setValue(elapsed)
        self.progress_bar.setFormat(f"下一轮：{remaining_minutes} 分钟后")

    def _on_download_finished(self):
        self._populate_channel_table()
        self._auto_save_config()

    # ────────── 其他操作 ──────────

    def _on_browse_output(self):
        directory = QFileDialog.getExistingDirectory(
            self, "选择输出目录", self.edit_output_path.text()
        )
        if directory:
            self.edit_output_path.setText(directory)

    # ────────── 系统托盘 ──────────

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self._show_window()

    def _show_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def closeEvent(self, event):
        """点击关闭按钮时最小化到托盘"""
        if self.tray_icon.isVisible():
            self._save_geometry()
            self.hide()
            self.tray_icon.showMessage(
                "YT-DLP 下载管理器",
                "程序已最小化到系统托盘，右键托盘图标可操作",
                QSystemTrayIcon.Information,
                2000,
            )
            event.ignore()
        else:
            self._quit_app()

    def _quit_app(self):
        """真正退出"""
        self._save_geometry()
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        self.tray_icon.hide()
        QApplication.quit()

    def _save_geometry(self):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())

    def _restore_geometry(self):
        geom = self.settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)
        state = self.settings.value("windowState")
        if state:
            self.restoreState(state)


# ─────────────────────── 入口 ────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)

    # 设置调色板（深色）
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#1e1e2e"))
    palette.setColor(QPalette.WindowText, QColor("#cdd6f4"))
    palette.setColor(QPalette.Base, QColor("#181825"))
    palette.setColor(QPalette.AlternateBase, QColor("#1e1e2e"))
    palette.setColor(QPalette.Text, QColor("#cdd6f4"))
    palette.setColor(QPalette.Button, QColor("#313244"))
    palette.setColor(QPalette.ButtonText, QColor("#cdd6f4"))
    palette.setColor(QPalette.Highlight, QColor("#cba6f7"))
    palette.setColor(QPalette.HighlightedText, QColor("#1e1e2e"))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
