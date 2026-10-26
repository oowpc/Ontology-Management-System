"""M7 页面与 API：/ke/upload、/ke/dashboard、/api/ke/*"""
from __future__ import annotations

import json
from datetime import date, datetime
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..security import require_role
from ..templating import templates
from . import service

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


# ================= 页面 =================

@router.get("/ke/dashboard")
def ke_dashboard(request: Request, group: str | None = None, db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    """五屏进度看板。group 缺省 = 全部组汇总（全员可见，见 M7 设计决策）。

    渲染已迁到 Jinja 模板 app/templates/ke/dashboard.html（结构与样式可直接编辑）；
    数据注入点：data_json（指标，</ 已转义）、groups/selected（组别选择器）、
    banner_status（今日上传横幅）、gen_time、data（页脚统计）。
    """
    today = date.today()
    groups = service.list_groups(db)
    selected = group if group in groups else None
    data = service.dashboard_payload(db, selected, today, _deadline())
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return templates.TemplateResponse(request=request, name="ke/dashboard.html", context={
        "request": request, "user": user, "data": data, "data_json": data_json,
        "gen_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "groups": groups, "selected": selected,
        "banner_status": service.group_status(db, today),
    })


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
    if ingest.get("ke_invalid"):
        ok += f"；⚠ {ingest['ke_invalid']} 条 KE 有结构错误（成品库可见明细，定稿前须修正）"
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
        return None, "请选择 CSV / xlsx 文件", 400, None
    raw = await file.read()
    if len(raw) > service.MAX_UPLOAD_BYTES:
        return None, "文件过大（上限 5MB）", 400, None
    try:
        rows, warnings = service.parse_upload(file.filename, raw)
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
