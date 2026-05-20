# AGENTS.md — yt-dlp-gui

基于 yt-dlp + FastAPI 的 YouTube 频道批量下载 WebUI 工具。Docker 部署。

## 运行与构建

```bash
# 安装依赖
pip install -r requirements.txt

# 运行
python -m app.main

# Docker 构建与运行
docker compose up -d
```

## 架构概要

```
FastAPI (app/main.py)
  └── create_app()
        ├── routes/pages.py  → GET /  Jinja2 渲染 index.html
        ├── routes/api.py    → /api/* REST 端点
        ├── routes/ws.py     → /ws/logs WebSocket
        ├── DownloadManager  → asyncio 下载生命周期
        │     └── YtDlpEngine → subprocess(yt-dlp)
        └── LogBroadcaster   → 线程安全的 WebSocket 广播
```

### 关键模块

| 模块 | 职责 |
|------|------|
| `app/config.py` | TOML 加载/保存，默认值填充，损坏恢复 |
| `app/engine.py` | 封装 yt-dlp 命令行调用、输出解析、进度提取 |
| `app/download_manager.py` | 异步下载生命周期（asyncio 任务 + 超时 + stop） |
| `app/websocket_manager.py` | 线程安全的 WebSocket 日志广播器 |
| `app/routes/api.py` | `/api/*` REST 端点 + Pydantic 校验 |
| `app/routes/pages.py` | Jinja2 页面路由 |
| `app/routes/ws.py` | WebSocket `/ws/logs` 端点 |
| `app/main.py` | 入口：uvicorn.Server 程序化启动 |

### 信号流

```
YtDlpEngine._emit()
  → LogBroadcaster.broadcast_sync(level, msg)
  → asyncio.run_coroutine_threadsafe(ws.send_json, loop)
  → WebSocket → 浏览器 JS → logArea.appendChild()
```

### 进度跟踪

```
YtDlpEngine.download_channel() → yt-dlp 子进程 stdout
  → 正则提取 [download] X.X% → 构造 PROGRESS JSON
  → LogBroadcaster.broadcast_sync("PROGRESS", json)
  → DownloadManager._wrap_progress() Hook 拦截
    ├── _update_progress() 更新 internal state (0-based index)
    │     ├── _current_channel_index → /api/status 轮询
    │     ├── _total_channels
    │     └── _progress_percent
    └── original broadcast → WebSocket → 前端 JS
          ├── progress-fill / progress-text (百分比进度条)
          └── channel-progress (频道进度: N / M)
```

前端 **两处消费进度**，须保持索引一致（均已 1-based 显示）：
- `/api/status` 轮询（每秒）→ `((current_channel_index || 0) + 1) + ' / ' + total_channels`
- WebSocket PROGRESS → `((channel_index || 0) + 1) + ' / ' + channel_total`

## 配置文件 (config.toml)

应用启动时自动加载 `config/config.toml`。不存在时自动创建（含全部默认值 + 空频道列表）。损坏时自动降级重建（备份为 `.toml.bak`）。

```toml
[general]
output_base_path = "/downloads"
proxy_url = ""
sleep_requests = 5
sleep_time = "5s"
wait_time_minutes = 360
quiet_mode = false
dateafter = "20000101"
download_archive = "archive.txt"
filename_format = "[%(upload_date>%Y-%m-%d)s]%(title)s [%(id)s].%(ext)s"
first_run_timeout = 360
normal_timeout = 60
cookies_source = "file"
cookies_file_path = "/app/config/cookies.txt"
cookies_browser = "firefox"
cookies_browser_profile = ""
cookies_browser_container = ""
log_max_history = 200

[download_limits]
normal_limit = 20
first_run_limit = 99999

[[channels]]
folder_name = "频道A"
youtube_id = "@channel_handle"
vid_type = "videos"
dl_type = "audio"
enabled = true
is_regex = false
is_first = false
```

## Docker 设计

- **yt-dlp**: nightly 独立二进制 `/usr/local/bin/yt-dlp`（来源 `yt-dlp-nightly-builds`，内置 curl_cffi）
- **Deno**: `/usr/local/bin/deno`（YouTube JS challenge）
- **ffmpeg**: apt 安装 `/usr/bin/ffmpeg`
- **Cookie**: 支持两种模式 — 
  - `file`：通过 API 上传 `cookies.txt`，`--cookies <path>`
  - `browser`：`--cookies-from-browser <browser>:<profile>`，挂载宿主机 Firefox profile（纯文本 SQLite，无需 keyring）
- **持久化**: `/app/config/`（配置 + archive + cookies），`/downloads/`（下载产物）
- **重启**: 设置页「🔄 重启服务」按钮，通过 `os._exit(0)` + Docker `restart: unless-stopped` 实现
- **健康检查**: `GET /api/health`
- **内存限制**: 2GB（防止 Deno stdin hang）
- **端口**: 通过 `PORT` 环境变量配置（默认 8080）
- **访问日志**: uvicorn `access_log=False`，仅保留应用级日志
- **定时构建**: GitHub Actions 每日 UTC 00:00 自动构建，跟随 yt-dlp nightly 更新
- **版本策略**: 每次代码变更递增小版本（v2.0.1 → v2.0.2 → ...）
- **基础镜像**: `python:3.14-slim`
- **层缓存**: yt-dlp 置于最底层，每日构建仅重拉 yt-dlp 层，apt/deno/pip 命中缓存

## 配置热更新

| 配置项 | 即时生效 | 需重启 |
|--------|----------|--------|
| `cookies_source` / `cookies_browser` | ✅ | |
| `sleep_requests` / `sleep_time` / `wait_time_minutes` | ✅ | |
| 频道列表 (CRUD) | ✅ | |
| `normal_limit` / `first_run_limit` | ✅ | |
| `log_max_history` | | ✅ (LogBroadcaster 初始化) |
| `output_base_path` / `dateafter` | ✅ (下次下载轮次) | |
| `proxy_url` / `quiet_mode` | ✅ (下次下载轮次) | |

## 从桌面版迁移的变更

| 变更 | 说明 |
|------|------|
| `--cookies-from-browser` → `--cookies` / `--cookies-from-browser` | 支持两种模式，browser 模式挂载 Firefox profile |
| `CREATE_NO_WINDOW` → 删除 | Linux 不需要 |
| `process.terminate()` → `os.killpg()` + `start_new_session=True` | 杀整个进程组（含 ffmpeg） |
| QThread → asyncio + `asyncio.to_thread()` | DownloadManager 异步管理 |
| Qt Signals → `broadcast_sync()` | WebSocket 广播 |
| `dict()` 浅拷贝 → `copy.deepcopy()` | 防止 is_first 突变传播 |
| 新增 `threading.Timer` 超时 | 防止 readline 阻塞永久挂起 |
| 新增 `--continue` | 断点续传 |
| 新增 `enabled` 字段 | 频道启停 |
| 新增 `first_run_timeout` / `normal_timeout` | 分别控制首次/常规超时 |
| 删除 browser/yt_dlp_exe/ffmpeg_exe 配置项 | Docker 内路径固定 |

## 前端

- 单 HTML 文件（`templates/index.html`），内联 CSS + JS
- Catppuccin Mocha 深色主题
- 3 个标签页：控制面板 / 频道管理 / 设置
- REST + WebSocket，无框架
- 1 秒轮询 `/api/status`，WebSocket 推送日志
- **日志可视化**（`#log-stats`）：在「清空日志」按钮下方，下载轮次运行期间自动展示 8 项统计指标：
  - 总任务 / 已完成 / 有警告 / 耗时
  - 📥 下载文件数 / 📦 归档跳过 / ⏭ 过滤跳过 / 🔒 会员限制
  - 纯客户端统计，从 `appendLogLine()` 流中提取关键模式
  - 清空日志时重置，新轮次开始时归零

## 修改注意事项

1. 所有后端文件在 `app/` 包内，单 HTML 在 `templates/`
2. 线程安全：GUI 更新必须通过 `LogBroadcaster.broadcast_sync()`，不要直接操作 WebSocket
3. 配置文件读写通过 `app/config.py` 的 `load_config()` / `save_config()`
4. 下载前对 channels 做深拷贝，完成后合并 `is_first` 变更
5. `config.toml` 和 `archive.txt` 已在 `.dockerignore` 中排除
