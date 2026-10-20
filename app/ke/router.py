"""M7 页面与 API：/ke/upload、/ke/dashboard、/api/ke/*"""
from __future__ import annotations

from datetime import date, datetime
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..security import require_role
from ..templating import templates
from . import service
from .template import render_dashboard

router = APIRouter(tags=["ke"])
EDIT_ROLES = ("reviewer", "admin")


def _deadline() -> date:
    try:
        return datetime.strptime(settings.ke_deadline, "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def _can_upload_to(user: dict, group: str) -> bool:
    """reviewer/admin 可代传任意组；annotator 仅本组。"""
    return user["role"] in EDIT_ROLES or (bool(user.get("group")) and user.get("group") == group)


def _header_extra(groups, selected):
    options = ['<option value="">全部组</option>']
    for g in groups:
        sel = " selected" if g == selected else ""
        options.append(f'<option value="{escape(g)}"{sel}>{escape(g)}</option>')
    return ('<a class="backlink" href="/concepts">← 本体系统</a>'
            '<select class="gselect" id="groupPick" title="切换查看组别">'
            + "".join(options) + "</select>")


def _banner(status) -> str:
    if not status:
        return ""
    miss = [g["name"] for g in status if not g["uploaded_today"]]
    done = len(status) - len(miss)
    cls = "upbanner miss" if miss else "upbanner"
    tail = ("；未上传：" + "、".join(escape(m) for m in miss)) if miss else "，全部到齐 ✓"
    return (f'<div class="{cls}">📮 今日已上传 {done}/{len(status)} 组{tail}'
            f' · <a href="/ke/upload">前往上传</a></div>')


# ================= 页面 =================

@router.get("/ke/dashboard", response_class=HTMLResponse)
def ke_dashboard(group: str | None = None, db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    """五屏进度看板。group 缺省 = 全部组汇总（全员可见，见 M7 设计决策）。"""
    today = date.today()
    groups = service.list_groups(db)
    selected = group if group in groups else None
    data = service.dashboard_payload(db, selected, today, _deadline())
    html = render_dashboard(
        data, gen_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        header_extra=_header_extra(groups, selected),
        banner=_banner(service.group_status(db, today)))
    return HTMLResponse(html)


@router.get("/ke/upload")
def ke_upload_page(request: Request, db: Session = Depends(get_db),
                   user: dict = Depends(require_role())):
    today = date.today()
    my_group = user.get("group")
    is_admin = user["role"] in EDIT_ROLES
    return templates.TemplateResponse(request=request, name="ke/upload.html", context={
        "request": request, "user": user, "today": today.isoformat(),
        "status": service.group_status(db, today),
        "my_group": my_group, "is_admin": is_admin,
        "group_options": service.list_groups(db),
        "can_upload": bool(my_group) or is_admin,
        "history": service.upload_history(db, None if is_admin else my_group),
    })


@router.post("/ke/upload")
async def ke_upload_submit(request: Request, db: Session = Depends(get_db),
                           user: dict = Depends(require_role())):
    form = await request.form()
    up, err, _, ingest = await _do_upload(db, form, user)
    if err:
        return RedirectResponse(f"/ke/upload?err={quote(err)}", status_code=303)
    ok = f"已上传 {up.row_count} 行（已完成 {up.done_count}），校验提示 {len(up.warnings)} 条"
    if ingest["created"] or ingest["updated"] or ingest["protected"]:
        ok += (f"；成品库：新增草稿 {ingest['created']}，更新 {ingest['updated']}，"
               f"定稿保护 {ingest['protected']}")
    return RedirectResponse(f"/ke/upload?ok={quote(ok)}", status_code=303)


# ================= API =================

async def _do_upload(db: Session, form, user: dict):
    """解析表单 → 校验权限与文件 → 全量替换 → 成品库同步草稿。

    返回 (KeUpload|None, 错误消息|None, HTTP码, 成品入库统计)。"""
    file = form.get("file")
    group = str(form.get("group") or user.get("group") or "").strip()
    if not group:
        return None, "未指定组别", 400, None
    if not _can_upload_to(user, group):
        return None, "只能上传本组数据", 403, None
    if file is None or not getattr(file, "filename", ""):
        return None, "请选择 CSV 文件", 400, None
    raw = await file.read()
    if len(raw) > service.MAX_UPLOAD_BYTES:
        return None, "文件过大（上限 5MB）", 400, None
    try:
        rows, warnings = service.parse_csv_text(service.decode_upload(raw))
    except ValueError as e:
        return None, str(e), 400, None
    up = service.replace_group_tasks(db, group, rows, file.filename, user["username"], warnings)
    from ..gold.items import ingest_rows   # M9 钩子（调用时导入，避免模块级循环依赖）
    ingest = ingest_rows(db, group, rows, user["username"])
    return up, None, 200, ingest


@router.post("/api/ke/upload", status_code=201)
async def api_ke_upload(request: Request, db: Session = Depends(get_db),
                        user: dict = Depends(require_role())):
    form = await request.form()
    up, err, code, ingest = await _do_upload(db, form, user)
    if err:
        raise HTTPException(code, err)
    return {"group": up.group_name, "row_count": up.row_count, "done_count": up.done_count,
            "warnings": up.warnings, "items": ingest,
            "uploaded_at": up.uploaded_at.isoformat() if up.uploaded_at else None}


@router.get("/api/ke/groups")
def api_ke_groups(db: Session = Depends(get_db), user: dict = Depends(require_role())):
    today = date.today()
    return {"today": today.isoformat(), "groups": [
        {"name": g["name"], "uploaded_today": g["uploaded_today"],
         "row_count": g["last_upload"].row_count if g["last_upload"] else 0,
         "done_count": g["last_upload"].done_count if g["last_upload"] else 0,
         "last_upload_at": g["last_upload"].uploaded_at.isoformat() if g["last_upload"] and g["last_upload"].uploaded_at else None,
         "uploaded_by": g["last_upload"].uploaded_by if g["last_upload"] else None}
        for g in service.group_status(db, today)]}
