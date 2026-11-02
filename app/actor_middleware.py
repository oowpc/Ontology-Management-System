"""在请求入口解析 Bearer Token 名称，供写操作审计使用。"""
from __future__ import annotations

import hashlib

from sqlalchemy import select

from .actor import actor_token
from .db import SessionLocal
from .models import ApiToken


class ActorTokenMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        token_name = None
        if scope.get("type") == "http":
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode("latin-1", "ignore")
            if auth.lower().startswith("bearer "):
                raw = auth[7:].strip()
                if raw:
                    h = hashlib.sha256(raw.encode()).hexdigest()
                    db = SessionLocal()
                    try:
                        token_name = db.scalar(
                            select(ApiToken.name).where(ApiToken.token_hash == h,
                                                        ApiToken.revoked_at.is_(None)))
                    finally:
                        db.close()
        ctx = actor_token.set(token_name)
        try:
            await self.app(scope, receive, send)
        finally:
            actor_token.reset(ctx)
