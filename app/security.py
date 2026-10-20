"""登录与角色。Session Cookie + Bearer API Token（agent 用）双通道；密码 pbkdf2（hashlib 原生）。"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import ApiToken, User

_ITERATIONS = 120_000
TOKEN_PREFIX = "ke_"


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
    """双通道：Session Cookie（浏览器）或 Authorization: Bearer <API Token>（agent）。"""
    user = request.session.get("user") or _bearer_user(db, request)
    if not user:
        raise LoginRequired()
    return user


def mint_token(db: Session, user: User, name: str) -> str:
    """签发 API Token：明文仅此一次返回；库中只存 sha256。"""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    db.add(ApiToken(user_id=user.id, name=(name or "default")[:64],
                    token_hash=hashlib.sha256(token.encode()).hexdigest()))
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
    row.last_used_at = datetime.now()
    db.commit()
    return {"username": u.username, "role": u.role, "group": u.group_name}


def require_role(*roles: str):
    """API 依赖：require_role()=任意登录用户；require_role('admin')=限定角色。"""

    def dep(user: dict = Depends(current_user)) -> dict:
        if roles and user["role"] not in roles:
            raise HTTPException(403, "权限不足")
        return user

    return dep
