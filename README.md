# yt-dlp-gui

基于 FastAPI、yt-dlp 和原生 ES Modules 的单用户批量下载管理器。v3 提供 Token 认证、频道抽屉管理、批量操作、浅色/深色主题和 Docker 部署。

## 快速开始

本机开发统一使用 uv：

```bash
uv sync --locked
uv run pytest
uv run ruff check app tests
uv run pyright app
uv run python -m app.main
```

启动前必须设置至少 32 个字符的 `APP_TOKEN`：

```powershell
$env:APP_TOKEN = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 40 | ForEach-Object {[char]$_})
uv run python -m app.main
```

浏览器访问 `http://127.0.0.1:8080`，首次打开时输入同一个 Token。Token 保存在浏览器 `localStorage` 中，仅适用于可信内网。

## Docker

```bash
cp .env.example .env
# 将 .env 中的 APP_TOKEN 替换为 openssl rand -hex 32 的输出
docker compose up -d --build
```

默认端口映射为 `0.0.0.0:8080:8080`。可通过环境变量调整：

```bash
HOST_BIND_IP=127.0.0.1 HOST_PORT=8080 APP_TOKEN="$APP_TOKEN" docker compose up -d
```

Compose 使用非 root 用户、只读根文件系统、`init`、能力削减和独立配置/下载卷。Windows/WSL 部署使用：

```bash
cd /mnt/c/dev/yt-dlp-gui
export APP_TOKEN="$(openssl rand -hex 32)"
docker compose -f docker-compose.wsl.yml up -d --build
```

WSL 绑定目录必须允许 UID `10001` 写入：

```bash
sudo mkdir -p /mnt/c/data/yt-dlp-gui/{config,downloads}
sudo chown -R 10001:10001 /mnt/c/data/yt-dlp-gui
```

## Token 与网络

- `/api/health`、主页和静态资源公开。
- 其他 `/api/v1/*` 请求必须携带 `Authorization: Bearer <APP_TOKEN>`。
- WebSocket 使用 `/ws/logs?token=<APP_TOKEN>`；应用日志会清理 Token，但代理服务器不应记录原始 WebSocket URL。
- 修改 `APP_TOKEN` 后重启容器，浏览器会在下一次 401 时清除旧值并提示重新输入。
- 不要将 Token 模式直接暴露到公网。跨不可信网络必须使用 TLS 反向代理或 VPN。

## 频道管理

- 支持 `@handle`、YouTube 频道 URL 和 `UC...` 频道 ID。
- 保存前可联网验证并获取 UC ID、频道标题和验证时间。
- 列表显示顺序就是实际下载顺序。启用名称或状态排序时只改变视图，不能拖拽。
- 支持搜索、状态/类型筛选、多选、批量启停、删除、改类型、改过滤规则和运行选中频道。
- 批量运行使用常规下载上限，不修改首次下载标记；下载任务活跃时会拒绝新的批量运行。
- 频道目录不能包含分隔符或 `..`。多个频道可共享目录，但页面会显示冲突警告。
- 删除频道只删除配置和运行状态，不删除已经下载的媒体。

## 配置路径

以下路径由部署环境固定，网页只读：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DOWNLOAD_ROOT` | `/downloads` | 下载根目录 |
| `CONFIG_ROOT` | `/app/config` | 配置根目录 |
| `COOKIES_FILE` | `/app/config/cookies.txt` | Cookie 文件 |
| `DOWNLOAD_ARCHIVE` | `archive.txt` | 相对配置目录的 archive 文件名 |
| `BIND_HOST` | `0.0.0.0` | 容器内监听地址 |
| `PORT` | `8080` | 应用监听端口 |
| `ENABLE_SELF_UPDATE` | `false` | 是否允许网页更新 yt-dlp |

配置保存采用临时文件、fsync 和原子替换。v2 配置会迁移到 `schema_version = 3`，并在同目录保留时间戳备份。

## 主题与测试

页面支持跟随系统、浅色和深色三种模式，选择保存在浏览器中。桌面端使用频道列表和右侧抽屉，移动端使用卡片和全屏抽屉。

常用检查：

```bash
uv run pytest -q
uv run ruff check app tests
uv run pyright app
uv run pip-audit
```

Playwright 验收截图存放在 `output/playwright/`，该目录不进入 Git。
