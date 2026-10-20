"""M9 · GOLD 成品库：标注内容入库、版本、定稿锁定、导出。

设计原则（贯穿全模块）：
- 通用性：状态机 draft→reviewed→gold 为 GOLD 数据工作流通用三态；组别动态读
  users.group_name；CSV 列名由 settings.ke_column_map 决定（别家表头可映射）。
- Agent 可操控性：页面动作与 /api/gold/items* 一一对应，写操作全部落 audit_logs
  （实体类型 gold_item，可在「变更日志」查看、回滚）。
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import log_change, snapshot
from ..db import get_db
from ..models import GoldItem
from ..security import current_user, require_role
from ..templating import templates
from .service import list_groups

router = APIRouter(tags=["gold-items"])

ITEM_STATUS = ["draft", "reviewed", "gold"]
ITEM_STATUS_LABELS = {"draft": "草稿", "reviewed": "已复核", "gold": "GOLD·定稿"}
EDIT_ROLES = ("reviewer", "admin")


# ---------------- 权限 ----------------

def can_edit(user: dict, it: GoldItem) -> bool:
    """草稿：审核员/管理员 + 本组标注员；已复核/GOLD：仅审核员/管理员。"""
    if user["role"] in EDIT_ROLES:
        return True
    return it.status == "draft" and bool(user.get("group")) and user.get("group") == it.group_name


def can_finalize(user: dict) -> bool:
    return user["role"] in EDIT_ROLES


# ---------------- 服务层 ----------------

def item_dict(it: GoldItem) -> dict:
    return {"id": it.id, "article_code": it.article_code, "ke_id": it.ke_id,
            "group_name": it.group_name, "ke_text": it.ke_text, "concepts": it.concepts,
            "status": it.status, "status_label": ITEM_STATUS_LABELS.get(it.status, it.status),
            "version": it.version, "created_by": it.created_by, "updated_by": it.updated_by,
            "created_at": it.created_at, "updated_at": it.updated_at}


def get_item_or_404(db: Session, iid: int) -> GoldItem:
    it = db.get(GoldItem, iid)
    if not it:
        raise HTTPException(404, "成品条目不存在")
    return it


def _query(db: Session, article_code=None, status=None, group_name=None, q=None):
    stmt = select(GoldItem).order_by(GoldItem.id.desc())
    if article_code:
        stmt = stmt.where(GoldItem.article_code == article_code.strip())
    if status:
        if status not in ITEM_STATUS:
            raise HTTPException(400, f"状态取值：{' / '.join(ITEM_STATUS)}")
        stmt = stmt.where(GoldItem.status == status)
    if group_name:
        stmt = stmt.where(GoldItem.group_name == group_name.strip())
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(GoldItem.ke_text.like(like) | GoldItem.ke_id.like(like))
    return stmt


def create_item(db: Session, *, article_code, ke_id, group_name, ke_text, concepts, actor) -> GoldItem:
    article_code, ke_id = (article_code or "").strip(), (ke_id or "").strip()
    if not article_code or not ke_id:
        raise HTTPException(400, "篇编号与 KE 编号不能为空")
    if db.scalar(select(GoldItem).where(GoldItem.article_code == article_code,
                                        GoldItem.ke_id == ke_id)):
        raise HTTPException(409, f"条目「{article_code} / {ke_id}」已存在")
    it = GoldItem(article_code=article_code, ke_id=ke_id, group_name=(group_name or "").strip(),
                  ke_text=ke_text or "", concepts=list(concepts or []),
                  created_by=actor, updated_by=actor)
    db.add(it)
    db.flush()
    log_change(db, "gold_item", it.id, "create", None, snapshot(it), actor)
    db.commit()
    db.refresh(it)
    return it


def update_item(db: Session, it: GoldItem, changes: dict, actor: str) -> GoldItem:
    before = snapshot(it)
    if changes.get("ke_text") is not None and changes["ke_text"] != it.ke_text:
        it.ke_text = changes["ke_text"]
        it.version += 1                      # 内容变更才升版本
    if changes.get("group_name") is not None and changes["group_name"].strip():
        it.group_name = changes["group_name"].strip()
    if changes.get("concepts") is not None:
        it.concepts = list(changes["concepts"])
    it.updated_by = actor
    db.flush()
    log_change(db, "gold_item", it.id, "update", before, snapshot(it), actor)
    db.commit()
    db.refresh(it)
    return it


def set_status(db: Session, it: GoldItem, status: str, actor: str) -> GoldItem:
    if status not in ITEM_STATUS:
        raise HTTPException(400, f"状态取值：{' / '.join(ITEM_STATUS)}")
    before = snapshot(it)
    it.status = status
    it.updated_by = actor
    db.flush()
    log_change(db, "gold_item", it.id, "status", before, snapshot(it), actor)
    db.commit()
    db.refresh(it)
    return it


def delete_item(db: Session, it: GoldItem, actor: str):
    log_change(db, "gold_item", it.id, "delete", snapshot(it), None, actor)
    db.delete(it)
    db.commit()


def ingest_rows(db: Session, group: str, rows, uploader: str) -> dict:
    """每日上传 CSV 钩子：有「篇编号」且有「KE内容」的行 → 草稿 upsert。

    幂等且守锁定：已复核 / GOLD 条目绝不被上传覆盖（计入 protected）。
    """
    stats = {"created": 0, "updated": 0, "protected": 0}
    for r in rows:
        art, content = (r.get("article") or "").strip(), (r.get("content") or "").strip()
        if not content or not art:
            continue
        it = db.scalar(select(GoldItem).where(GoldItem.article_code == art,
                                              GoldItem.ke_id == r["id"]))
        if it is None:
            db.add(GoldItem(article_code=art, ke_id=r["id"], group_name=group,
                            ke_text=content, status="draft",
                            created_by=uploader, updated_by=uploader))
            stats["created"] += 1
        elif it.status == "draft":
            if it.ke_text != content:
                it.ke_text = content
                it.version += 1
                it.updated_by = uploader
                stats["updated"] += 1
        else:
            stats["protected"] += 1
    db.commit()
    return stats


def export_csv(db: Session, filters: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["篇编号", "KE编号", "组别", "状态", "版本", "引用概念", "KE内容", "最后修改", "修改时间"])
    for it in db.scalars(_query(db, **filters)).all():
        w.writerow([it.article_code, it.ke_id, it.group_name,
                    ITEM_STATUS_LABELS.get(it.status, it.status), it.version,
                    "、".join(it.concepts or []), it.ke_text, it.updated_by,
                    it.updated_at.strftime("%Y-%m-%d %H:%M") if it.updated_at else ""])
    return "﻿" + buf.getvalue()  # BOM 供 Excel 正确识别中文


# ---------------- API（agent 可完成全部操作） ----------------

class ItemIn(BaseModel):
    article_code: str = Field(min_length=1, max_length=32)
    ke_id: str = Field(min_length=1, max_length=64)
    group_name: str = ""
    ke_text: str = ""
    concepts: list[str] = []


class ItemPatch(BaseModel):
    ke_text: str | None = None
    group_name: str | None = None
    concepts: list[str] | None = None


class StatusIn(BaseModel):
    status: str


@router.get("/api/gold/items")
def api_items(article_code: str | None = None, status: str | None = None,
              group_name: str | None = None, q: str | None = None,
              page: int = 1, size: int = 100,
              db: Session = Depends(get_db), user: dict = Depends(require_role())):
    size = min(size, 200)
    filters = dict(article_code=article_code, status=status, group_name=group_name, q=q)
    total = db.scalar(select(func.count()).select_from(_query(db, **filters).subquery())) or 0
    items = db.scalars(_query(db, **filters).offset((page - 1) * size).limit(size)).all()
    return {"items": [item_dict(i) for i in items], "total": total, "page": page, "size": size}


@router.post("/api/gold/items", status_code=201)
def api_item_create(data: ItemIn, db: Session = Depends(get_db),
                    user: dict = Depends(require_role(*EDIT_ROLES))):
    it = create_item(db, article_code=data.article_code, ke_id=data.ke_id,
                     group_name=data.group_name, ke_text=data.ke_text,
                     concepts=data.concepts, actor=user["username"])
    return item_dict(it)


@router.get("/api/gold/items/export.csv")
def api_export_csv(article_code: str | None = None, status: str | None = None,
                   group_name: str | None = None, q: str | None = None,
                   db: Session = Depends(get_db), user: dict = Depends(require_role())):
    filters = dict(article_code=article_code, status=status, group_name=group_name, q=q)
    return Response(export_csv(db, filters), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=gold_items.csv"})


@router.get("/api/gold/items/export.json")
def api_export_json(article_code: str | None = None, status: str | None = None,
                    group_name: str | None = None, q: str | None = None,
                    db: Session = Depends(get_db), user: dict = Depends(require_role())):
    filters = dict(article_code=article_code, status=status, group_name=group_name, q=q)
    items = db.scalars(_query(db, **filters)).all()
    return {"items": [item_dict(i) for i in items], "total": len(items)}


@router.get("/api/gold/items/{iid}")
def api_item(iid: int, db: Session = Depends(get_db), user: dict = Depends(require_role())):
    return item_dict(get_item_or_404(db, iid))


@router.patch("/api/gold/items/{iid}")
def api_item_patch(iid: int, data: ItemPatch, db: Session = Depends(get_db),
                   user: dict = Depends(require_role())):
    it = get_item_or_404(db, iid)
    if not can_edit(user, it):
        raise HTTPException(403, "无权修改：草稿仅本组标注员/审核员可改，已复核与 GOLD 仅审核员/管理员")
    return item_dict(update_item(db, it, data.model_dump(exclude_unset=True), user["username"]))


@router.post("/api/gold/items/{iid}/status")
def api_item_status(iid: int, data: StatusIn, db: Session = Depends(get_db),
                    user: dict = Depends(require_role())):
    it = get_item_or_404(db, iid)
    if data.status in ("reviewed", "gold") and not can_finalize(user):
        raise HTTPException(403, "复核通过与定稿仅审核员/管理员可操作")
    if data.status == "draft" and not can_edit(user, it):
        raise HTTPException(403, "无权退回该条目")
    return item_dict(set_status(db, it, data.status, user["username"]))


@router.delete("/api/gold/items/{iid}")
def api_item_delete(iid: int, db: Session = Depends(get_db),
                    user: dict = Depends(require_role(*EDIT_ROLES))):
    delete_item(db, get_item_or_404(db, iid), user["username"])
    return {"ok": True}


# ---------------- 页面 ----------------

def _qs(article_code, status, group_name, q) -> str:
    from urllib.parse import urlencode
    return urlencode({k: v for k, v in {"article_code": article_code, "status": status,
                                        "group_name": group_name, "q": q}.items() if v})


@router.get("/gold/items")
def items_page(request: Request, article_code: str | None = None, status: str | None = None,
               group_name: str | None = None, q: str | None = None,
               db: Session = Depends(get_db), user: dict = Depends(current_user)):
    filters = dict(article_code=article_code, status=status, group_name=group_name, q=q)
    items = db.scalars(_query(db, **filters).limit(200)).all()
    total = db.scalar(select(func.count()).select_from(_query(db, **filters).subquery())) or 0
    codes = db.scalars(select(GoldItem.article_code).distinct().order_by(GoldItem.article_code)).all()
    return templates.TemplateResponse(request=request, name="gold/items.html", context={
        "request": request, "user": user, "items": items, "total": total,
        "codes": codes, "groups": list_groups(db),
        "article_code": article_code, "status": status, "group_name": group_name, "q": q,
        "qs": _qs(article_code, status, group_name, q),
        "status_list": ITEM_STATUS, "status_labels": ITEM_STATUS_LABELS,
        "edit_roles": EDIT_ROLES,
    })


@router.get("/gold/items/new")
def item_new_page(request: Request, db: Session = Depends(get_db),
                  user: dict = Depends(current_user)):
    if user["role"] not in EDIT_ROLES:
        raise HTTPException(403, "补录仅审核员/管理员；标注员请走每日 CSV 上传")
    return templates.TemplateResponse(request=request, name="gold/item_form.html", context={
        "request": request, "user": user, "it": None, "groups": list_groups(db),
        "status_list": ITEM_STATUS, "status_labels": ITEM_STATUS_LABELS, "edit_roles": EDIT_ROLES, "error": None,
    })


async def _item_form(request: Request) -> dict:
    form = await request.form()
    concepts_raw = str(form.get("concepts", ""))
    import re as _re
    concepts = [x.strip() for x in _re.split(r"[,，、;；\n]+", concepts_raw) if x.strip()]
    return {"article_code": str(form.get("article_code", "")), "ke_id": str(form.get("ke_id", "")),
            "group_name": str(form.get("group_name", "")), "ke_text": str(form.get("ke_text", "")),
            "concepts": concepts, "status": str(form.get("status", ""))}


@router.post("/gold/items/new")
async def item_new(request: Request, db: Session = Depends(get_db),
                   user: dict = Depends(current_user)):
    if user["role"] not in EDIT_ROLES:
        raise HTTPException(403, "补录仅审核员/管理员")
    data = await _item_form(request)
    try:
        it = create_item(db, actor=user["username"], **{k: v for k, v in data.items() if k != "status"})
        if data["status"] in ("reviewed", "gold"):
            set_status(db, it, data["status"], user["username"])
    except HTTPException as e:
        return templates.TemplateResponse(request=request, name="gold/item_form.html", context={
            "request": request, "user": user, "it": data, "groups": list_groups(db),
            "status_list": ITEM_STATUS, "status_labels": ITEM_STATUS_LABELS, "error": e.detail,
        }, status_code=400)
    return RedirectResponse("/gold/items?ok=条目已创建", status_code=303)


@router.get("/gold/items/{iid}/edit")
def item_edit_page(iid: int, request: Request, db: Session = Depends(get_db),
                   user: dict = Depends(current_user)):
    it = get_item_or_404(db, iid)
    if not can_edit(user, it):
        raise HTTPException(403, "无权修改该条目")
    return templates.TemplateResponse(request=request, name="gold/item_form.html", context={
        "request": request, "user": user, "it": it, "groups": list_groups(db),
        "status_list": ITEM_STATUS, "status_labels": ITEM_STATUS_LABELS, "edit_roles": EDIT_ROLES, "error": None,
    })


@router.post("/gold/items/{iid}/edit")
async def item_edit(iid: int, request: Request, db: Session = Depends(get_db),
                    user: dict = Depends(current_user)):
    it = get_item_or_404(db, iid)
    if not can_edit(user, it):
        raise HTTPException(403, "无权修改该条目")
    data = await _item_form(request)
    try:
        update_item(db, it, {k: data[k] for k in ("ke_text", "group_name", "concepts")}, user["username"])
        if data["status"] and data["status"] != it.status:
            if data["status"] in ("reviewed", "gold") and not can_finalize(user):
                raise HTTPException(403, "复核通过与定稿仅审核员/管理员可操作")
            set_status(db, it, data["status"], user["username"])
    except HTTPException as e:
        return templates.TemplateResponse(request=request, name="gold/item_form.html", context={
            "request": request, "user": user, "it": it, "groups": list_groups(db),
            "status_list": ITEM_STATUS, "status_labels": ITEM_STATUS_LABELS, "error": e.detail,
        }, status_code=400)
    return RedirectResponse("/gold/items?ok=已保存", status_code=303)


@router.post("/gold/items/{iid}/delete")
def item_delete(iid: int, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    if user["role"] not in EDIT_ROLES:
        raise HTTPException(403, "删除仅审核员/管理员")
    delete_item(db, get_item_or_404(db, iid), user["username"])
    return RedirectResponse("/gold/items?ok=已删除", status_code=303)
