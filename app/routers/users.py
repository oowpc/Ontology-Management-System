"""用户与组管理（仅 admin）：建号 / 改角色与组 / 重置密码。agent 可用 /api/admin/users。

约定：不提供删除用户（留痕完整性）；组为自由文本，新组保存后自动出现在各组清单。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..security import current_user, hash_password, require_role
from ..templating import templates

router = APIRouter(tags=["admin-users"])
ROLES = ("annotator", "reviewer", "admin")
ROLE_LABELS = {"annotator": "标注员", "reviewer": "审核员", "admin": "管理员"}


def _dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role,
            "role_label": ROLE_LABELS.get(u.role, u.role),
            "group_name": u.group_name, "created_at": u.created_at}


def _get_user(db: Session, uid: int) -> User:
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    return u


def _create(db: Session, *, username: str, password: str, role: str, group_name: str) -> User:
    username = (username or "").strip()
    if not username or not password:
        raise HTTPException(400, "用户名与密码不能为空")
    if role not in ROLES:
        raise HTTPException(400, f"角色取值：{' / '.join(ROLES)}")
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, f"用户名「{username}」已存在")
    u = User(username=username, password_hash=hash_password(password),
             role=role, group_name=(group_name or "").strip() or None)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _update(db: Session, u: User, *, role=None, group_name=None) -> User:
    if role is not None:
        if role not in ROLES:
            raise HTTPException(400, f"角色取值：{' / '.join(ROLES)}")
        u.role = role
    if group_name is not None:
        u.group_name = group_name.strip() or None
    db.commit()
    db.refresh(u)
    return u


# ---------------- API ----------------

class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    role: str = "annotator"
    group_name: str = ""


class UserPatch(BaseModel):
    role: str | None = None
    group_name: str | None = None


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


@router.get("/api/admin/users")
def api_users(db: Session = Depends(get_db), user: dict = Depends(require_role("admin"))):
    rows = db.scalars(select(User).order_by(User.id)).all()
    return {"items": [_dict(u) for u in rows]}


@router.post("/api/admin/users", status_code=201)
def api_user_create(data: UserIn, db: Session = Depends(get_db),
                    user: dict = Depends(require_role("admin"))):
    return _dict(_create(db, username=data.username, password=data.password,
                         role=data.role, group_name=data.group_name))


@router.patch("/api/admin/users/{uid}")
def api_user_patch(uid: int, data: UserPatch, db: Session = Depends(get_db),
                   user: dict = Depends(require_role("admin"))):
    return _dict(_update(db, _get_user(db, uid), **data.model_dump(exclude_unset=True)))


@router.post("/api/admin/users/{uid}/password")
def api_user_password(uid: int, data: PasswordIn, db: Session = Depends(get_db),
                      user: dict = Depends(require_role("admin"))):
    u = _get_user(db, uid)
    u.password_hash = hash_password(data.password)
    db.commit()
    return {"ok": True}


# ---------------- 页面 ----------------

@router.get("/admin/users")
def users_page(request: Request, db: Session = Depends(get_db),
               user: dict = Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "仅管理员")
    rows = db.scalars(select(User).order_by(User.id)).all()
    return templates.TemplateResponse(request=request, name="admin_users.html", context={
        "request": request, "user": user, "items": rows, "roles": ROLES,
        "role_labels": ROLE_LABELS,
    })


@router.post("/admin/users/create")
async def users_create(request: Request, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "仅管理员")
    form = await request.form()
    try:
        _create(db, username=str(form.get("username", "")), password=str(form.get("password", "")),
                role=str(form.get("role", "")), group_name=str(form.get("group_name", "")))
    except HTTPException as e:
        return RedirectResponse(f"/admin/users?err={e.detail}", status_code=303)
    return RedirectResponse("/admin/users?ok=用户已创建", status_code=303)


@router.post("/admin/users/{uid}/update")
async def users_update(uid: int, request: Request, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "仅管理员")
    form = await request.form()
    try:
        _update(db, _get_user(db, uid), role=str(form.get("role", "")) or None,
                group_name=str(form.get("group_name", "")))
    except HTTPException as e:
        return RedirectResponse(f"/admin/users?err={e.detail}", status_code=303)
    return RedirectResponse("/admin/users?ok=已保存", status_code=303)


@router.post("/admin/users/{uid}/password")
async def users_password(uid: int, request: Request, db: Session = Depends(get_db),
                         user: dict = Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "仅管理员")
    form = await request.form()
    pw = str(form.get("password", ""))
    if not pw.strip():
        return RedirectResponse("/admin/users?err=密码不能为空", status_code=303)
    u = _get_user(db, uid)
    u.password_hash = hash_password(pw)
    db.commit()
    return RedirectResponse("/admin/users?ok=密码已重置", status_code=303)
