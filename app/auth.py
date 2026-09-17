from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import Header, HTTPException, WebSocket, status


class TokenAuth:
    def __init__(self, token: str) -> None:
        if len(token) < 32:
            raise ValueError("APP_TOKEN 至少需要 32 个字符")
        self._token = token

    def matches(self, candidate: str | None) -> bool:
        if not candidate:
            return False
        return hmac.compare_digest(candidate, self._token)

    def require_header(
        self,
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not self.matches(token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token 无效或未提供",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def require_websocket(self, websocket: WebSocket, token: str | None) -> bool:
        if self.matches(token):
            return True
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False


def token_from_environment() -> str:
    token = os.getenv("APP_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("必须设置至少 32 个字符的 APP_TOKEN")
    return token
