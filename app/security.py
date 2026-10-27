"""登录与角色。Session Cookie + Bearer API Token（agent 用）双通道；密码 pbkdf2（hashlib 原生）。
P0 加固：登录失败计数锁定（默认 5 次锁 10 分钟，LOGIN_MAX_FAILS/LOGIN_LOCK_SECONDS 可调）。"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import ApiToken, User

_ITERATIONS = 120_000
TOKEN_PREFIX = "ke_"
# P1：Bearer token 的 last_used_at 写入节流（秒）；scope 词表
TOKEN_LAST_USED_THROTTLE = int(os.environ.get("TOKEN_LAST_USED_THROTTLE", "300"))
TOKEN_SCOPES = ("read", "write")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

# 登录防爆破：进程内存计数（单 worker 准确；多 worker 各自计数、重启清零——内网工具的低成本口径）
LOGIN_MAX_FAILS = int(os.environ.get("LOGIN_MAX_FAILS", "5"))
LOGIN_LOCK_SECONDS = int(os.environ.get("LOGIN_LOCK_SECONDS", "600"))
_LOGIN_FAILS: dict[str, list[float]] = {}


def login_lock_remaining(username: str) -> int:
    """该用户名剩余锁定秒数（0=未锁）。顺带清理窗口外的失败记录。"""
    fails = _LOGIN_FAILS.get(username)
    if not fails:
        return 0
    now = time.time()
    fails[:] = [t for t in fails if now - t < LOGIN_LOCK_SECONDS]
    if len(fails) < LOGIN_MAX_FAILS:
        return 0
    return max(1, int(LOGIN_LOCK_SECONDS - (now - fails[-LOGIN_MAX_FAILS])) + 1)


def record_login_fail(username: str) -> None:
    _LOGIN_FAILS.setdefault(username, []).append(time.time())


def clear_login_fails(username: str) -> None:
    _LOGIN_FAILS.pop(username, None)


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, hexdigest = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), hexdigest)
    except Exception:
        return False


class LoginRequired(Exception):
    """未登录；由 main.py 的异常处理器转成 401（API）或跳转登录页（页面）。"""


def current_user(request: Request, db: Session = Depends(get_db)) -> dict:
    """双通道：Authorization: Bearer <API Token>（agent）或 Session Cookie（浏览器）。
    Bearer 优先（显式凭证优于环境 Cookie——否则只读 token 会被并存 session 提权）。
    Bearer 通道带 scope 闸：只读 token 调写接口 → 403（session 用户隐含全权限）。"""
    user = _bearer_user(db, request)
    if not user:
        sess = request.session.get("user")
        if sess:
            user = {**sess, "via": "session", "scopes": ["read", "write"]}
    if not user:
        raise LoginRequired()
    need = "read" if request.method in _SAFE_METHODS else "write"
    if need not in (user.get("scopes") or []):
        raise HTTPException(403, f"Token 权限不足：需要 {need} scope（该 token 为 {user.get('scopes')}）")
    return user


def mint_token(db: Session, user: User, name: str, scopes: list | None = None) -> str:
    """签发 API Token：明文仅此一次返回；库中只存 sha256。scopes 缺省=读写全权限。"""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    db.add(ApiToken(user_id=user.id, name=(name or "default")[:64],
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    scopes=scopes or ["read", "write"]))
    db.commit()
    return token


def _bearer_user(db: Session, request: Request) -> dict | None:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    raw = auth[7:].strip()
    if not raw:
        return None
    h = hashlib.sha256(raw.encode()).hexdigest()
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == h,
                                           ApiToken.revoked_at.is_(None)))
    if not row:
        return None
    u = db.get(User, row.user_id)
    if not u:
        return None
    # P1：last_used_at 节流——距上次更新超过阈值才写库（agent 高频调用时每请求一写会成为瓶颈）
    now = datetime.now()
    if row.last_used_at is None \
            or (now - row.last_used_at).total_seconds() > TOKEN_LAST_USED_THROTTLE:
        row.last_used_at = now
        db.commit()
    return {"username": u.username, "role": u.role, "group": u.group_name,
            "via": "bearer", "scopes": row.scopes or ["read", "write"]}


def require_role(*roles: str):
    """API 依赖：require_role()=任意登录用户；require_role('admin')=限定角色。"""

    def dep(user: dict = Depends(current_user)) -> dict:
        if roles and user["role"] not in roles:
            raise HTTPException(403, "权限不足")
        return user

    return dep
