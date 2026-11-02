"""本体库 API、库成员权限与页面切换。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..libraries import KEY_RE, get_library
from ..library_context import LIBRARY_COOKIE, library_scope
from ..models import LibraryMember, OntologyLibrary, User
from ..security import current_user, current_user_raw, require_role
from ..templating import flash, templates

router = APIRouter(tags=["libraries"])
MEMBER_ROLES = ("owner", "reviewer", "annotator")
ROLE_LABELS = {"owner": "库管理员", "reviewer": "审核员", "annotator": "标注员"}


class LibraryCreate(BaseModel):
    key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    kind: str = "business"
    description: str = ""
    owner: str = "system"


class MemberIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    role: str = "annotator"


def _out(lib: OntologyLibrary, role: str | None = None) -> dict:
    out = {"id": lib.id, "key": lib.key, "name": lib.name, "kind": lib.kind,
           "description": lib.description or "", "owner": lib.owner, "status": lib.status}
    if role:
        out["role"] = role
    return out


def _accessible(db: Session, user: dict):
    with library_scope(None):
        if user.get("role") == "admin":
            libs = db.scalars(select(OntologyLibrary).order_by(OntologyLibrary.id)).all()
            return [(x, "admin") for x in libs]
        uid = db.scalar(select(User.id).where(User.username == user["username"]))
        if uid is None:
            return []
        rows = db.execute(
            select(OntologyLibrary, LibraryMember.role)
            .join(LibraryMember, LibraryMember.library_id == OntologyLibrary.id)
            .where(LibraryMember.user_id == uid, OntologyLibrary.status == "active")
            .order_by(OntologyLibrary.id)
        ).all()
        return [(lib, role) for lib, role in rows]


def _member(db: Session, lib_id: int, username: str):
    with library_scope(None):
        uid = db.scalar(select(User.id).where(User.username == username))
        if uid is None:
            return None, None
        m = db.scalar(select(LibraryMember).where(
            LibraryMember.library_id == lib_id, LibraryMember.user_id == uid))
        return m, uid


def _can_manage(user: dict, lib: OntologyLibrary, role: str | None) -> bool:
    return user.get("role") == "admin" or role == "owner"


@router.get("/api/libraries")
def api_libraries(db: Session = Depends(get_db),
                  user: dict = Depends(current_user_raw)):
    return {"items": [_out(lib, role) for lib, role in _accessible(db, user)],
            "total": len(_accessible(db, user))}


@router.post("/api/libraries", status_code=201)
def api_create_library(data: LibraryCreate, db: Session = Depends(get_db),
                       user: dict = Depends(require_role("admin"))):
    key = data.key.strip().lower()
    if not KEY_RE.match(key):
        raise HTTPException(400, "key 只能使用小写字母、数字、连字符，且以字母开头")
    if data.kind not in {"reference", "business", "pilot"}:
        raise HTTPException(400, "kind 必须是 reference/business/pilot")
    if db.scalar(select(OntologyLibrary).where(OntologyLibrary.key == key)):
        raise HTTPException(409, "库 key 已存在")
    lib = OntologyLibrary(key=key, name=data.name.strip(), kind=data.kind,
                          description=data.description, owner=data.owner or user["username"])
    db.add(lib)
    db.commit()
    db.refresh(lib)
    return _out(lib)


@router.get("/library/switch")
def switch_library(key: str, next: str = "/", user: dict = Depends(current_user_raw)):
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    from ..db import SessionLocal
    db = SessionLocal()
    try:
        lib = get_library(db, key)
        if not lib:
            raise HTTPException(404, "本体库不存在")
        if user.get("role") != "admin":
            m, _ = _member(db, lib.id, user["username"])
            if not m:
                raise HTTPException(403, f"无权访问本体库「{lib.name}」")
        resp = RedirectResponse(target, status_code=303)
        resp.set_cookie(LIBRARY_COOKIE, lib.key, max_age=180 * 24 * 3600,
                        path="/", httponly=True, samesite="lax")
        return resp
    finally:
        db.close()


@router.get("/api/libraries/{key}/members")
def api_members(key: str, db: Session = Depends(get_db),
                user: dict = Depends(current_user_raw)):
    lib = get_library(db, key)
    if not lib:
        raise HTTPException(404, "本体库不存在")
    m, _ = _member(db, lib.id, user["username"])
    if user.get("role") != "admin" and not (m and m.role == "owner"):
        raise HTTPException(403, "仅库管理员可查看成员")
    with library_scope(None):
        rows = db.execute(
            select(LibraryMember, User).join(User, User.id == LibraryMember.user_id)
            .where(LibraryMember.library_id == lib.id)
            .order_by(LibraryMember.role, User.username)
        ).all()
    return {"items": [{"id": m.id, "username": u.username, "role": m.role,
                       "role_label": ROLE_LABELS.get(m.role, m.role)}
                      for m, u in rows]}


@router.post("/api/libraries/{key}/members")
def api_add_member(key: str, data: MemberIn, db: Session = Depends(get_db),
                   user: dict = Depends(current_user_raw)):
    lib = get_library(db, key)
    if not lib:
        raise HTTPException(404, "本体库不存在")
    m, _ = _member(db, lib.id, user["username"])
    if user.get("role") != "admin" and not (m and m.role == "owner"):
        raise HTTPException(403, "仅库管理员可添加成员")
    if data.role not in MEMBER_ROLES:
        raise HTTPException(400, f"role 取值：{' / '.join(MEMBER_ROLES)}")
    with library_scope(None):
        target = db.scalar(select(User).where(User.username == data.username.strip()))
        if not target:
            raise HTTPException(404, "用户不存在")
        row = db.scalar(select(LibraryMember).where(
            LibraryMember.library_id == lib.id, LibraryMember.user_id == target.id))
        if row:
            row.role = data.role
        else:
            row = LibraryMember(library_id=lib.id, user_id=target.id,
                                role=data.role, created_by=user["username"])
            db.add(row)
        db.commit()
        db.refresh(row)
    return {"id": row.id, "username": target.username, "role": row.role}


@router.delete("/api/libraries/{key}/members/{username}")
def api_remove_member(key: str, username: str, db: Session = Depends(get_db),
                      user: dict = Depends(current_user_raw)):
    lib = get_library(db, key)
    if not lib:
        raise HTTPException(404, "本体库不存在")
    m, _ = _member(db, lib.id, user["username"])
    if user.get("role") != "admin" and not (m and m.role == "owner"):
        raise HTTPException(403, "仅库管理员可移除成员")
    with library_scope(None):
        uid = db.scalar(select(User.id).where(User.username == username))
        row = db.scalar(select(LibraryMember).where(
            LibraryMember.library_id == lib.id, LibraryMember.user_id == uid)) if uid else None
        if not row:
            raise HTTPException(404, "成员不存在")
        if row.role == "owner" and username == user["username"] and user.get("role") != "admin":
            raise HTTPException(400, "不能移除自己作为库管理员的权限")
        db.delete(row)
        db.commit()
    return {"ok": True}


# ---------------- 页面 ----------------

@router.get("/libraries")
def libraries_page(request: Request, key: str | None = None,
                   db: Session = Depends(get_db),
                   user: dict = Depends(current_user_raw)):
    accessible = _accessible(db, user)
    selected = None
    selected_role = None
    if key:
        selected = next((lib for lib, _ in accessible if lib.key == key), None)
        if selected is None:
            raise HTTPException(403, "无权访问该本体库")
        selected_role = next((role for lib, role in accessible if lib.id == selected.id), None)
    else:
        current = getattr(request.state, "library", None)
        selected = next((lib for lib, _ in accessible if current and lib.id == current.id),
                        accessible[0][0] if accessible else None)
        selected_role = next((role for lib, role in accessible if selected and lib.id == selected.id), None)
    members = []
    if selected and _can_manage(user, selected, selected_role):
        with library_scope(None):
            members = db.execute(
                select(LibraryMember, User).join(User, User.id == LibraryMember.user_id)
                .where(LibraryMember.library_id == selected.id)
                .order_by(LibraryMember.role, User.username)
            ).all()
    all_users = db.scalars(select(User).order_by(User.username)).all() if selected and _can_manage(user, selected, selected_role) else []
    request.state.libraries = [lib for lib, _ in accessible]
    return templates.TemplateResponse(request=request, name="libraries.html", context={
        "request": request, "user": user, "libraries": accessible,
        "selected": selected, "selected_role": selected_role,
        "members": members, "all_users": all_users,
        "can_manage": bool(selected and _can_manage(user, selected, selected_role)),
        "role_labels": ROLE_LABELS,
    })


@router.post("/libraries/{key}/members")
async def page_add_member(key: str, request: Request, db: Session = Depends(get_db),
                          user: dict = Depends(current_user_raw)):
    form = await request.form()
    lib = get_library(db, key)
    if not lib:
        raise HTTPException(404, "本体库不存在")
    m, _ = _member(db, lib.id, user["username"])
    if user.get("role") != "admin" and not (m and m.role == "owner"):
        raise HTTPException(403, "仅库管理员可添加成员")
    role = str(form.get("role") or "annotator")
    if role not in MEMBER_ROLES:
        role = "annotator"
    with library_scope(None):
        target = db.scalar(select(User).where(User.username == str(form.get("username") or "").strip()))
        if not target:
            flash(request, "用户不存在", "err")
            return RedirectResponse(f"/libraries?key={lib.key}", status_code=303)
        row = db.scalar(select(LibraryMember).where(
            LibraryMember.library_id == lib.id, LibraryMember.user_id == target.id))
        if row:
            row.role = role
        else:
            db.add(LibraryMember(library_id=lib.id, user_id=target.id,
                                 role=role, created_by=user["username"]))
        db.commit()
    flash(request, "成员已保存")
    return RedirectResponse(f"/libraries?key={lib.key}", status_code=303)


@router.post("/libraries/{key}/members/{username}/remove")
def page_remove_member(key: str, username: str, request: Request,
                       db: Session = Depends(get_db),
                       user: dict = Depends(current_user_raw)):
    lib = get_library(db, key)
    if not lib:
        raise HTTPException(404, "本体库不存在")
    m, _ = _member(db, lib.id, user["username"])
    if user.get("role") != "admin" and not (m and m.role == "owner"):
        raise HTTPException(403, "仅库管理员可移除成员")
    with library_scope(None):
        uid = db.scalar(select(User.id).where(User.username == username))
        row = db.scalar(select(LibraryMember).where(
            LibraryMember.library_id == lib.id, LibraryMember.user_id == uid)) if uid else None
        if row and row.role == "owner" and username == user["username"] and user.get("role") != "admin":
            flash(request, "不能移除自己作为库管理员的权限", "err")
        elif row:
            db.delete(row)
            db.commit()
            flash(request, "成员已移除")
    return RedirectResponse(f"/libraries?key={lib.key}", status_code=303)
