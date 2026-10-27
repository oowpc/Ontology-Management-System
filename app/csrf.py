"""P0 · CSRF 防护：同步器 token（session 存储）+ 表单隐藏域 / 请求头校验。

规则：非安全方法（POST/PUT/PATCH/DELETE）且非 Bearer 认证 → 要求 csrf_token
（表单字段 或 X-CSRF-Token 头）与 session 中 token 一致。Bearer 调用不带 Cookie 会话，
天然免疫 CSRF，直接放行。开关：CSRF_ENABLED=0 关闭（测试用；运行时读取，可动态切换）。

页面接入零模板改动：base.html 注入 <meta name="csrf-token"> + 一段 JS
自动给所有 POST 表单补隐藏域；multipart 表单（KE 上传）同此。
中间件为此缓冲请求体并回放（上传文件会进内存——内网工具量级可接受）。

SessionMiddleware 必须先于本中间件（main.py 中后注册 SessionMiddleware 使其成为外层）。
"""
from __future__ import annotations

import hmac
import re
import secrets
from urllib.parse import parse_qs

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_RE_MULTIPART_FIELD = re.compile(rb'name="csrf_token"\s*\r\n\s*\r\n([^\r\n]+)')


def extract_token(headers: list[tuple[bytes, bytes]], body: bytes) -> str:
    """从请求头（X-CSRF-Token）或请求体（urlencoded / multipart 表单字段）取 token。"""
    hdr = {k.lower(): v for k, v in headers}
    if b"x-csrf-token" in hdr:
        return hdr[b"x-csrf-token"].decode("utf-8", "ignore")
    ctype = hdr.get(b"content-type", b"").decode("utf-8", "ignore")
    if "application/x-www-form-urlencoded" in ctype:
        vals = parse_qs(body.decode("utf-8", "ignore")).get("csrf_token")
        return vals[0] if vals else ""
    if "multipart/form-data" in ctype:
        m = _RE_MULTIPART_FIELD.search(body)
        return m.group(1).decode("utf-8", "ignore") if m else ""
    return ""


class CSRFMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        session = scope.get("session")
        if scope["method"] in SAFE_METHODS:
            # 安全方法：保证 session 里有 token 供页面 meta/表单注入
            if session is not None and "csrf" not in session:
                session["csrf"] = secrets.token_urlsafe(32)
            await self.app(scope, receive, send)
            return

        from .config import settings
        headers = list(scope.get("headers") or [])
        bearer = any(k.lower() == b"authorization" and v.lower().startswith(b"bearer ")
                     for k, v in headers)
        if not settings.csrf_enabled or bearer:
            await self.app(scope, receive, send)
            return

        # 缓冲请求体以解析表单 token，随后回放给下游
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            body += message.get("body", b"")
            if not message.get("more_body"):
                break

        expected = (session or {}).get("csrf", "")
        token = extract_token(headers, body)
        if not expected or not token or not hmac.compare_digest(token, expected):
            from fastapi.responses import JSONResponse
            resp = JSONResponse({"detail": "CSRF 校验失败：缺少或非法的 csrf_token"}, status_code=403)
            await resp(scope, receive, send)
            return

        async def replay():
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)
