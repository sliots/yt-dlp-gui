from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")


def build_page_router() -> APIRouter:
    router = APIRouter()
    static_dir = Path("static")
    router.mount("/static", StaticFiles(directory=static_dir), name="static")

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html")

    return router
