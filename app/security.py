"""登录与角色。Session Cookie + Bearer API Token（agent 用）双通道；密码 pbkdf2（hashlib 原生）。
P0 加固：登录失败计数锁定（默认 5 次锁 10 分钟，LOGIN_MAX_FAILS/LOGIN_LOCK_SECONDS 可调）。"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .actor import actor_token
from .db import get_db
from .library_context import library_scope
from .models import ApiToken, LibraryMember, OntologyLibrary, User

_ITERATIONS = 120_000
TOKEN_PREFIX = "ke_"
# P1：Bearer token 的 last_used_at 写入节流（秒）；scope 词表
TOKEN_LAST_USED_THROTTLE = int(os.environ.get("TOKEN_LAST_USED_THROTTLE", "300"))
TOKEN_DEFAULT_DAYS = int(os.environ.get("TOKEN_DEFAULT_DAYS", "30"))
TOKEN_RATE_LIMIT = int(os.environ.get("TOKEN_RATE_LIMIT", "300"))
TOKEN_RATE_WINDOW = int(os.environ.get("TOKEN_RATE_WINDOW", "60"))
TOKEN_SCOPES = ("read", "write")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_TOKEN_HITS: dict[int, list[float]] = {}

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


def current_user_raw(request: Request, db: Session = Depends(get_db)) -> dict:
    """只验证身份，不做本体库成员校验（登录跳转/库列表/me 使用）。"""
    user = _bearer_user(db, request)
    if not user:
        sess = request.session.get("user")
        if sess:
            user = {**sess, "via": "session", "scopes": ["read", "write"]}
    if not user:
        raise LoginRequired()
    actor_token.set(user.get("token_name"))
    need = "read" if request.method in _SAFE_METHODS else "write"
    if need not in (user.get("scopes") or []):
        raise HTTPException(403, f"Token 权限不足：需要 {need} scope（该 token 为 {user.get('scopes')}）")
    return user


def _accessible_libraries(db: Session, user: dict):
    # 需要跨库查询成员关系，临时关闭当前库过滤
    with library_scope(None):
        if user.get("role") == "admin":
            return db.scalars(select(OntologyLibrary).order_by(OntologyLibrary.id)).all()
        uid = db.scalar(select(User.id).where(User.username == user["username"]))
        if uid is None:
            return []
        return db.scalars(
            select(OntologyLibrary)
            .join(LibraryMember, LibraryMember.library_id == OntologyLibrary.id)
            .where(LibraryMember.user_id == uid, OntologyLibrary.status == "active")
            .order_by(OntologyLibrary.id)
        ).all()


def current_user(request: Request, db: Session = Depends(get_db)) -> dict:
    """身份 + 当前库成员权限；库级角色与全局角色取较低者。

    - admin：全局通过，不需要 library_members 行；
    - owner/reviewer 成员的 reviewer 账号：有效角色 reviewer；
    - annotator 成员，或 annotator 账号：有效角色 annotator；
    - 无成员关系：403，不能通过直接 URL/API 访问该库。
    """
    user = current_user_raw(request, db)
    lib = getattr(request.state, "library", None)
    if lib is None:
        return user
    if user.get("role") == "admin":
        request.state.libraries = _accessible_libraries(db, user)
        request.state.library_role = "admin"
        return {**user, "library_id": lib.id, "library_role": "admin"}
    member = db.scalar(select(LibraryMember).where(
        LibraryMember.library_id == lib.id,
        LibraryMember.user_id == db.scalar(
            select(User.id).where(User.username == user["username"]))))
    if member is None:
        raise HTTPException(403, f"无权访问本体库「{lib.name}」")
    if user.get("role") == "reviewer" and member.role in {"owner", "reviewer"}:
        effective = "reviewer"
    else:
        effective = "annotator"
    request.state.libraries = _accessible_libraries(db, user)
    request.state.library_role = member.role
    return {**user, "role": effective, "global_role": user["role"],
            "library_id": lib.id, "library_role": member.role}


def mint_token(db: Session, user: User, name: str, scopes: list | None = None,
               expires_in_days: int | None = None) -> str:
    """签发 API Token：明文仅此一次返回；库中只存 sha256。

    `expires_in_days=None` 使用 TOKEN_DEFAULT_DAYS（默认 30 天），0 表示不过期。
    """
    days = TOKEN_DEFAULT_DAYS if expires_in_days is None else max(0, expires_in_days)
    expires_at = datetime.now() + timedelta(days=days) if days else None
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    db.add(ApiToken(user_id=user.id, name=(name or "default")[:64],
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    scopes=scopes or ["read", "write"], expires_at=expires_at))
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
    now = datetime.now()
    if row.expires_at is not None and row.expires_at <= now:
        raise HTTPException(401, "API Token 已过期")
    # 进程内每分钟令牌限流；单 worker 准确，多 worker 各自计数（内网工具够用）
    if TOKEN_RATE_LIMIT > 0:
        hits = _TOKEN_HITS.setdefault(row.id, [])
        cutoff = time.time() - TOKEN_RATE_WINDOW
        hits[:] = [t for t in hits if t >= cutoff]
        if len(hits) >= TOKEN_RATE_LIMIT:
            raise HTTPException(429, f"Token 速率超限（{TOKEN_RATE_LIMIT}/{TOKEN_RATE_WINDOW}s）")
        hits.append(time.time())
    # last_used_at 节流写库；IP 一并留痕
    if row.last_used_at is None \
            or (now - row.last_used_at).total_seconds() > TOKEN_LAST_USED_THROTTLE:
        row.last_used_at = now
        row.last_used_ip = request.client.host if request.client else None
        db.commit()
    return {"username": u.username, "role": u.role, "group": u.group_name,
            "via": "bearer", "scopes": row.scopes or ["read", "write"],
            "token_id": row.id, "token_name": row.name}


def require_role(*roles: str):
    """API 依赖：require_role()=任意登录用户；require_role('admin')=限定角色。"""

    def dep(user: dict = Depends(current_user)) -> dict:
        if roles and user["role"] not in roles:
            raise HTTPException(403, "权限不足")
        return user

    return dep
