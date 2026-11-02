"""本体库注册表、请求级当前库上下文与切换中间件。"""
from __future__ import annotations

import re
from urllib.parse import parse_qs

from fastapi.responses import JSONResponse
from sqlalchemy import select

from .db import SessionLocal
from .library_context import (DEFAULT_LIBRARY_ID, DEFAULT_LIBRARY_KEY,
                              LIBRARY_COOKIE, current_library_id, library_scope)
from .models import OntologyLibrary

KEY_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


def list_libraries(db):
    return db.scalars(select(OntologyLibrary).order_by(OntologyLibrary.id)).all()


def get_library(db, key: str | int | None):
    if key is None or key == "":
        return None
    try:
        lid = int(key)
    except (TypeError, ValueError):
        return db.scalar(select(OntologyLibrary).where(OntologyLibrary.key == str(key)))
    return db.get(OntologyLibrary, lid)


def default_library(db):
    lib = db.get(OntologyLibrary, DEFAULT_LIBRARY_ID)
    return lib or db.scalar(select(OntologyLibrary).order_by(OntologyLibrary.id))


def _cookie_value(cookie_header: str, name: str) -> str:
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


class OntologyLibraryMiddleware:
    """解析 X-Ontology-Library / ?library= / cookie，设置 contextvar 和 request.state。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path", "").startswith("/static/"):
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        query = parse_qs(scope.get("query_string", b"").decode("latin-1", "ignore"))
        header_key = headers.get(b"x-ontology-library", b"").decode("latin-1", "ignore").strip()
        query_key = (query.get("library") or [""])[0].strip()
        cookie_key = _cookie_value(
            headers.get(b"cookie", b"").decode("latin-1", "ignore"), LIBRARY_COOKIE)
        key = header_key or query_key or cookie_key or DEFAULT_LIBRARY_KEY
        is_api = scope.get("path", "").startswith("/api/")

        with library_scope(None):
            db = SessionLocal()
            try:
                libraries = list_libraries(db)
                selected = get_library(db, key)
                if selected is None and is_api and (header_key or query_key):
                    resp = JSONResponse({"detail": f"本体库不存在：{key}"}, status_code=400)
                    await resp(scope, receive, send)
                    return
                if selected is None:
                    selected = get_library(db, DEFAULT_LIBRARY_KEY)
                if selected is None:
                    selected = default_library(db)
                if selected is None:
                    if is_api:
                        resp = JSONResponse({"detail": "没有可用的本体库"}, status_code=503)
                        await resp(scope, receive, send)
                        return
                    await self.app(scope, receive, send)
                    return
                # 避免 ORM 对象在会话关闭后被 lazy load 触发；当前只读标量字段已加载
                state = scope.setdefault("state", {})
                state["library"] = selected
                state["libraries"] = list(libraries)
                state["library_key"] = selected.key
            finally:
                db.close()

        token = current_library_id.set(selected.id)
        try:
            await self.app(scope, receive, send)
        finally:
            current_library_id.reset(token)
