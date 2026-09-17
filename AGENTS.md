# AGENTS.md - yt-dlp-gui

基于 FastAPI、yt-dlp 和原生 ES Modules 的单用户批量下载管理器。当前主版本为 v3，Docker 部署，默认面向可信内网。

## 开发命令

本机 Python 必须使用 `uv`：

```bash
uv sync --locked
uv run pytest -q
uv run ruff check app tests
uv run pyright app
uv run pip-audit
uv run python -m app.main
```

启动前必须设置至少 32 个字符的 `APP_TOKEN`。不要退回 `pip install` 工作流。

WSL 的 Ubuntu 使用 root 运行 Docker：

```bash
wsl.exe -u root -- bash -lc 'cd /mnt/c/dev/yt-dlp-gui && docker compose build'
```

Windows 本地代理通常为 `127.0.0.1:7890`。从 WSL 或 Docker 构建访问 GitHub
较慢时，使用 WSL 默认网关地址作为代理：

```bash
PROXY_HOST=$(ip route | awk '/default/ {print $3; exit}')
docker compose build \
  --build-arg HTTP_PROXY="http://${PROXY_HOST}:7890" \
  --build-arg HTTPS_PROXY="http://${PROXY_HOST}:7890"
```

## 架构

```text
FastAPI (app/main.py)
  ├── TokenAuth
  ├── routes/pages.py  -> GET /
  ├── routes/api.py    -> /api/v1/*
  ├── routes/ws.py     -> /ws/logs?token=...
  ├── DownloadManager  -> 运行状态、频道状态、循环调度
  ├── YtDlpEngine      -> yt-dlp 命令、输出解析、进程监督
  └── LogBroadcaster   -> 有界队列、WebSocket writer、轮转日志
```

关键模块：

| 模块 | 职责 |
| --- | --- |
| `app/models.py` | Pydantic 配置、频道、运行状态模型 |
| `app/config.py` | v3 迁移、原子保存、路径环境变量 |
| `app/auth.py` | Bearer Token 和 WebSocket Token 校验 |
| `app/engine.py` | yt-dlp Dry-run、解析、下载和进程组终止 |
| `app/download_manager.py` | 结构化运行结果、选中频道运行、循环调度 |
| `app/websocket_manager.py` | 每客户端有界队列和单 writer |
| `templates/index.html` | 语义化页面结构 |
| `static/css/app.css` | Catppuccin Latte/Mocha 主题 |
| `static/js/*.js` | 原生 ES Modules 前端 |

## API 约定

- `GET /api/health` 公开，其他 `/api/v1/*` 需要 `Authorization: Bearer`。
- 频道 CRUD 使用 UUID，不提供索引 CRUD。
- `PUT /api/v1/channels/order` 必须提交完整且唯一的频道 ID 排列。
- `POST /api/v1/channels/bulk-actions` 一次原子执行批量操作。
- `POST /api/v1/channels/{id}/resolve` 联网解析频道；`POST /api/v1/channels/{id}/test` 执行 Dry-run。
- 配置使用 `PATCH /api/v1/config`，未提交字段保持不变。

## 频道语义

- 配置数组顺序就是实际执行顺序。
- `is_first` 只控制下载上限，不再自动抢占顺序。
- 只有完全成功才清除 `is_first`。
- 删除频道不删除媒体文件。
- 频道目录必须是单一安全路径段。
- `title_filter` 只匹配视频标题；旧 `is_regex=true` 迁移为 ASMR 预设。

## 安全与路径

- `APP_TOKEN` 只从环境变量读取，不写入配置或镜像。
- 浏览器把 Token 存在 `localStorage`，默认只适合可信内网。
- 跨不可信网络必须使用 TLS 反向代理或 VPN。
- `/downloads`、配置目录、Cookie 和 archive 路径由环境变量固定，网页不能修改根路径。
- 容器内默认监听 `0.0.0.0`，可通过 `BIND_HOST` 和 `PORT` 调整。
- 配置和 Cookie 写入必须保持原子替换及 `0600` 权限。
- 不要允许用户输入进入 shell 命令；yt-dlp 始终通过参数数组启动。

## 前端

- 禁止新增内联 `<script>`、内联事件处理器或用户数据 `innerHTML`。
- 所有颜色使用语义变量，必须同时检查浅色和深色主题。
- 交互必须支持键盘；抽屉使用 `dialog`，状态通知使用 `aria-live`。
- 移动端测试宽度至少覆盖 390px。
- Playwright 截图放在 `output/playwright/`，不要提交。

## Docker 与发布

- 运行镜像使用 UID `10001`、只读根文件系统、`init`、`cap_drop: ALL` 和 `no-new-privileges`。
- Python 依赖由 `pyproject.toml + uv.lock` 固定。
- Deno、yt-dlp nightly 和 bgutil 插件版本在 `Dockerfile` 中固定；更新时一起验证。
- 发布任务必须先通过 Ruff、Pyright、pytest 和镜像构建。
- 版本以 `pyproject.toml` 和 `VERSION` 为准，Git tag 必须为 `v<version>`。
