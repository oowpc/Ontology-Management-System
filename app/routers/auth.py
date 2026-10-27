"""登录 / 登出 / 当前用户。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..security import (clear_login_fails, current_user, login_lock_remaining,
                        record_login_fail, verify_password)
from ..templating import templates

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={
        "request": request, "user": None,
        "next": request.query_params.get("next", ""), "error": None,
    })


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    locked = login_lock_remaining(username)
    if locked:
        return templates.TemplateResponse(request=request, name="login.html", context={
            "request": request, "user": None,
            "next": str(form.get("next", "")),
            "error": f"失败次数过多，已锁定，{locked} 秒后重试",
        }, status_code=429)
    u = db.scalar(select(User).where(User.username == username))
    if not u or not verify_password(password, u.password_hash):
        record_login_fail(username)
        return templates.TemplateResponse(request=request, name="login.html", context={
            "request": request, "user": None,
            "next": str(form.get("next", "")), "error": "用户名或密码错误",
        }, status_code=401)
    clear_login_fails(username)
    request.session["user"] = {"username": u.username, "role": u.role, "group": u.group_name}
    nxt = str(form.get("next", ""))
    if not nxt.startswith("/"):
        nxt = "/concepts"
    return RedirectResponse(nxt, status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/api/me")
def me(user: dict = Depends(current_user)):
    return user
