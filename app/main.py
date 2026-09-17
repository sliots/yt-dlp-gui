from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request

from app.auth import TokenAuth, token_from_environment
from app.config import load_config
from app.download_manager import DownloadManager
from app.routes.api import build_router as build_api_router
from app.routes.pages import build_page_router
from app.routes.ws import build_ws_router
from app.websocket_manager import LogBroadcaster, _redact, set_event_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("yt_dlp_gui")


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                _redact(value) if isinstance(value, str) else value
                for value in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: _redact(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return True


def install_log_redaction() -> None:
    for handler in logging.getLogger().handlers:
        if not any(isinstance(item, RedactingFilter) for item in handler.filters):
            handler.addFilter(RedactingFilter())


def create_app(
    manager: DownloadManager,
    broadcaster: LogBroadcaster,
    config: dict,
    auth: TokenAuth,
) -> FastAPI:
    app = FastAPI(title="YT-DLP Download Manager", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self'; "
            "script-src 'self'; "
            "connect-src 'self' ws: wss:; "
            "font-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'; "
            "form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    app.include_router(build_page_router())
    app.include_router(build_api_router(manager, config, auth))
    app.include_router(build_ws_router(broadcaster, auth))

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": "3.0.0"}

    return app


def main() -> None:
    install_log_redaction()
    config_path = Path(os.getenv("CONFIG_ROOT", "config")) / "config.toml"
    config = load_config(config_path)
    auth = TokenAuth(token_from_environment())

    broadcaster = LogBroadcaster(
        max_history=config.get("general", {}).get("log_max_history", 200),
        log_path=config_path.parent / "download.log",
    )
    broadcaster.install_log_handler("yt_dlp_gui")

    manager = DownloadManager(config, broadcaster, config_path)
    app = create_app(manager, broadcaster, config, auth)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    set_event_loop(loop)

    host = os.getenv("BIND_HOST", "0.0.0.0")  # nosec B104 - token auth is mandatory
    port = int(os.getenv("PORT", "8080"))
    uvicorn_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        access_log=False,
        loop="none",
        log_config=None,
    )
    server = uvicorn.Server(uvicorn_config)

    logger.info("YT-DLP Download Manager started on %s:%d", host, port)
    try:
        loop.run_until_complete(manager.auto_start_loop())
        loop.run_until_complete(server.serve())
    finally:
        logger.info("Shutting down...")
        loop.run_until_complete(manager.shutdown())
        logger.info("YT-DLP Download Manager shutdown complete")


if __name__ == "__main__":
    main()
