from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.websocket_manager import LogBroadcaster


def build_ws_router(broadcaster: LogBroadcaster) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/logs")
    async def ws_logs(ws: WebSocket):
        await broadcaster.connect(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            broadcaster.disconnect(ws)

    return router
