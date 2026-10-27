"""API Token 管理：自助签发/吊销（agent 接入入口；明文仅签发时返回一次）。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ApiToken, User
from ..security import TOKEN_SCOPES, mint_token, require_role

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class TokenIn(BaseModel):
    name: str = Field(default="default", max_length=64)
    username: str | None = None  # 缺省=自己；代他人签发需 admin
    scopes: list[str] | None = None  # 缺省=["read","write"]；["read"]=只读（agent 最小权限）


def _dict(db: Session, r: ApiToken) -> dict:
    u = db.get(User, r.user_id)
    return {"id": r.id, "name": r.name, "username": u.username if u else "?",
            "scopes": r.scopes or ["read", "write"],
            "created_at": r.created_at, "last_used_at": r.last_used_at,
            "revoked": r.revoked_at is not None}


@router.post("", status_code=201)
def mint(data: TokenIn, db: Session = Depends(get_db), user: dict = Depends(require_role())):
    target = data.username or user["username"]
    if target != user["username"] and user["role"] != "admin":
        raise HTTPException(403, "只能为自己签发；代他人签发需管理员")
    u = db.scalar(select(User).where(User.username == target))
    if not u:
        raise HTTPException(404, f"用户「{target}」不存在")
    scopes = data.scopes if data.scopes is not None else ["read", "write"]
    bad = [s for s in scopes if s not in TOKEN_SCOPES]
    if bad or not scopes:
        raise HTTPException(400, f"scopes 只能是 {list(TOKEN_SCOPES)} 的非空子集，收到：{scopes}")
    token = mint_token(db, u, data.name, scopes)
    return {"token": token, "username": target, "name": data.name, "scopes": scopes,
            "note": "Token 仅此一次返回，请妥善保存。用法：请求头 Authorization: Bearer <token>"}


@router.get("")
def list_tokens(db: Session = Depends(get_db), user: dict = Depends(require_role())):
    stmt = select(ApiToken).order_by(ApiToken.id.desc())
    if user["role"] != "admin":
        uid = db.scalar(select(User.id).where(User.username == user["username"]))
        stmt = stmt.where(ApiToken.user_id == uid)
    return {"items": [_dict(db, r) for r in db.scalars(stmt).all()]}


@router.delete("/{tid}")
def revoke(tid: int, db: Session = Depends(get_db), user: dict = Depends(require_role())):
    row = db.get(ApiToken, tid)
    if not row:
        raise HTTPException(404, "Token 不存在")
    owner = db.get(User, row.user_id)
    if user["role"] != "admin" and (not owner or owner.username != user["username"]):
        raise HTTPException(403, "只能吊销自己的 Token")
    row.revoked_at = datetime.now()
    db.commit()
    return {"ok": True}
