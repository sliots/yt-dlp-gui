from __future__ import annotations

import asyncio
import logging
import os

import uvicorn
from fastapi import FastAPI

from app.config import load_config
from app.download_manager import DownloadManager
from app.routes.api import build_router as build_api_router
from app.routes.pages import build_page_router
from app.routes.ws import build_ws_router
from app.websocket_manager import LogBroadcaster, set_event_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("yt_dlp_gui")


def create_app(
    manager: DownloadManager,
    broadcaster: LogBroadcaster,
    config: dict,
) -> FastAPI:
    app = FastAPI(title="YT-DLP Download Manager")

    app.include_router(build_page_router())
    app.include_router(build_api_router(manager, config))
    app.include_router(build_ws_router(broadcaster))

    return app


def main() -> None:
    config = load_config()

    broadcaster = LogBroadcaster(max_history=200)
    broadcaster.install_log_handler("yt_dlp_gui")

    manager = DownloadManager(config, broadcaster)
    app = create_app(manager, broadcaster, config)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    set_event_loop(loop)

    port = int(os.getenv("PORT", "8080"))
    uvicorn_config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="none")
    server = uvicorn.Server(uvicorn_config)

    logger.info("YT-DLP Download Manager started on 0.0.0.0:%d", port)

    try:
        loop.run_until_complete(server.serve())
    finally:
        logger.info("Shutting down...")
        manager.shutdown()
        logger.info("YT-DLP Download Manager shutdown complete")


if __name__ == "__main__":
    main()
