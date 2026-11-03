"""M25 · KEOL 实例库：/keol 查询页 + /api/keol/* 只读检索。

数据为 KEOL 源（.md）的镜像，由 scripts/sync_keol.py --with-instances 全量重建；
本模块只读。口径与 KEOL 侧 tools/api_server.py 对齐（literals/outgoing/incoming）。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..keol_sync import check, latest, record
from ..keolsync import mirror_status
from ..models import KeolEntity, KeolFact, KeolSyncRun
from ..security import current_user, require_role
from ..templating import flash, templates

router = APIRouter(tags=["keol"])
MAX_LIMIT = 500
PAGE_SIZE = 50


def _fact_dict(f: KeolFact) -> dict:
    return {"pred": f.pred, "key": f.key, "subj": f.subj, "value": f.value,
            "kind": f.kind, "src": f.src}


def _count(db: Session, stmt) -> int:
    return db.scalar(select(func.count()).select_from(stmt.subquery())) or 0


# ================= API =================

@router.get("/api/keol/status")
def keol_status(db: Session = Depends(get_db), user: dict = Depends(require_role())):
    """镜像概况：实体/事实/类型/谓词数 + 最近同步时间（None=尚未同步）。"""
    return mirror_status(db)


@router.get("/api/keol/entities")
def keol_entities(q: str = "", type: str = "", limit: int = 50, offset: int = 0,
                  db: Session = Depends(get_db), user: dict = Depends(require_role())):
    """实体检索：名称模糊（q）/ 类型精确（type）。"""
    stmt = select(KeolEntity)
    if q:
        stmt = stmt.where(KeolEntity.name.like(f"%{q}%"))
    if type:
        stmt = stmt.where(KeolEntity.type == type)
    total = _count(db, stmt)
    rows = db.scalars(stmt.order_by(KeolEntity.name)
                      .limit(max(1, min(limit, MAX_LIMIT))).offset(max(0, offset))).all()
    return {"count": total,
            "items": [{"name": e.name, "type": e.type, "src": e.src} for e in rows]}


@router.get("/api/keol/entities/{name}")
def keol_entity(name: str, limit: int = 200,
                db: Session = Depends(get_db), user: dict = Depends(require_role())):
    """实体详情：字面量属性 / 出向事实 / 入向事实（口径同 KEOL 侧 /api/entity）。"""
    e = db.scalar(select(KeolEntity).where(KeolEntity.name == name))
    if not e:
        raise HTTPException(404, f"实例不存在：{name}")
    cap = max(1, min(limit, MAX_LIMIT))
    outgoing = db.scalars(select(KeolFact).where(KeolFact.subj == name).limit(cap)).all()
    incoming = db.scalars(
        select(KeolFact)
        .where(or_(KeolFact.value == name,
                   (KeolFact.key.like(f"%{name}%")) & (KeolFact.subj != name)))
        .limit(cap)).all()
    literals = [f for f in outgoing if f.kind in ("string", "number")]
    return {
        "name": e.name, "type": e.type, "src": e.src,
        "literals": [{"pred": f.pred, "value": f.value.strip('"')} for f in literals],
        "outgoing": [_fact_dict(f) for f in outgoing],
        "incoming": [_fact_dict(f) for f in incoming],
    }


@router.get("/api/keol/facts")
def keol_facts(pred: str = "", subj: str = "", value: str = "", key: str = "",
               limit: int = 200, offset: int = 0,
               db: Session = Depends(get_db), user: dict = Depends(require_role())):
    """事实检索：pred/subj/value/key 精确过滤（可组合）。"""
    stmt = select(KeolFact)
    for col, v in ((KeolFact.pred, pred), (KeolFact.subj, subj),
                   (KeolFact.value, value), (KeolFact.key, key)):
        if v:
            stmt = stmt.where(col == v)
    total = _count(db, stmt)
    rows = db.scalars(stmt.order_by(KeolFact.id)
                      .limit(max(1, min(limit, MAX_LIMIT))).offset(max(0, offset))).all()
    return {"count": total, "items": [_fact_dict(f) for f in rows]}


@router.get("/api/keol/sync/status")
def keol_sync_status(db: Session = Depends(get_db),
                     user: dict = Depends(require_role())):
    row = latest(db)
    if row is None:
        return {"checked": False}
    return {"checked": True, "id": row.id, "status": row.status,
            "source_hash": row.source_hash,
            "checked_at": row.checked_at.isoformat(timespec="seconds") if row.checked_at else None,
            "counts": row.counts or {}, "diff": row.diff or {}, "error": row.error}


@router.post("/api/keol/sync/check")
def keol_sync_check(db: Session = Depends(get_db),
                    user: dict = Depends(current_user)):
    try:
        data = check(db)
        row = record(db, data, user["username"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)[:1000]) from e
    return {"id": row.id, **data}


@router.post("/api/keol/sync/apply")
def keol_sync_apply(request: Request, db: Session = Depends(get_db),
                    user: dict = Depends(require_role("reviewer", "admin"))):
    lib = getattr(request.state, "library", None)
    if lib is None:
        raise HTTPException(400, "没有当前本体库")
    if not settings.keol_console_token:
        raise HTTPException(400, "未配置 KEOL_CONSOLE_TOKEN，无法向 KEOL 发起推送")
    body = json.dumps({"mode": "suggest", "confirm": True,
                       "library": lib.key, "domain": "KEOL"}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        settings.keol_console_url.rstrip("/") + "/api/oms/push", data=body,
        headers={"Authorization": "Bearer " + settings.keol_console_token,
                 "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            out = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise HTTPException(502, f"KEOL push {e.code}: {e.read().decode('utf-8', 'replace')[:500]}") from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"KEOL push 失败：{e}") from e
    return {"library": lib.key, "push": out}


# ================= 页面 =================

@router.get("/keol/sync")
def keol_sync_page(request: Request, db: Session = Depends(get_db),
                   user: dict = Depends(current_user)):
    row = latest(db)
    return templates.TemplateResponse(request=request, name="keol_sync.html", context={
        "request": request, "user": user, "row": row,
        "can_apply": bool(settings.keol_console_token),
    })


@router.post("/keol/sync/check")
def keol_sync_check_page(request: Request, db: Session = Depends(get_db),
                         user: dict = Depends(current_user)):
    try:
        data = check(db)
        row = record(db, data, user["username"])
        flash(request, f"检查完成：source {row.source_hash[:12]}，changed={data['changed']}")
    except Exception as e:  # noqa: BLE001
        flash(request, f"检查失败：{e}", "err")
    return RedirectResponse("/keol/sync", status_code=303)


@router.post("/keol/sync/apply")
def keol_sync_apply_page(request: Request, db: Session = Depends(get_db),
                         user: dict = Depends(current_user)):
    if user.get("role") not in {"reviewer", "admin"}:
        raise HTTPException(403, "提交建议推送需审核员/管理员")
    try:
        res = keol_sync_apply(request, db, user)
        flash(request, f"已向 KEOL 提交建议推送：{res.get('push', {}).get('suggestion', {}).get('id', '已提交')}")
    except HTTPException as e:
        flash(request, str(e.detail), "err")
    return RedirectResponse("/keol/sync", status_code=303)


@router.get("/keol")
def keol_page(request: Request, q: str = "", type: str = "", pred: str = "",
              value: str = "", name: str = "", page: int = 1,
              db: Session = Depends(get_db), user: dict = Depends(current_user)):
    """KEOL 库查询页：默认实体检索；带 pred/value 查事实；带 name 看实体详情。"""
    status = mirror_status(db)
    types = db.execute(select(KeolEntity.type, func.count().label("n"))
                       .group_by(KeolEntity.type).order_by(func.count().desc()).limit(30)).all()
    ctx = {"request": request, "user": user, "status": status, "types": types,
           "q": q, "type": type, "pred": pred, "value": value, "page": page,
           "entity": None, "entities": None, "facts": None}
    if name:
        e = db.scalar(select(KeolEntity).where(KeolEntity.name == name))
        if not e:
            raise HTTPException(404, f"实例不存在：{name}")
        outgoing = db.scalars(select(KeolFact).where(KeolFact.subj == name)
                              .limit(200)).all()
        incoming = db.scalars(
            select(KeolFact)
            .where(or_(KeolFact.value == name,
                       (KeolFact.key.like(f"%{name}%")) & (KeolFact.subj != name)))
            .limit(200)).all()
        ctx["entity"] = {"name": e.name, "type": e.type, "src": e.src,
                         "literals": [f for f in outgoing if f.kind in ("string", "number")],
                         "outgoing": outgoing, "incoming": incoming}
        return templates.TemplateResponse(request=request, name="keol.html", context=ctx)
    if q or type or (not pred and not value):
        stmt = select(KeolEntity)
        if q:
            stmt = stmt.where(KeolEntity.name.like(f"%{q}%"))
        if type:
            stmt = stmt.where(KeolEntity.type == type)
        total = _count(db, stmt)
        rows = db.scalars(stmt.order_by(KeolEntity.name)
                          .limit(PAGE_SIZE).offset(max(0, page - 1) * PAGE_SIZE)).all()
        ctx["entities"] = {"items": rows, "total": total,
                           "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)}
    if pred or value:
        stmt = select(KeolFact)
        if pred:
            stmt = stmt.where(KeolFact.pred == pred)
        if value:
            stmt = stmt.where(KeolFact.value == value)
        total = _count(db, stmt)
        rows = db.scalars(stmt.order_by(KeolFact.id)
                          .limit(PAGE_SIZE).offset(max(0, page - 1) * PAGE_SIZE)).all()
        ctx["facts"] = {"items": rows, "total": total,
                        "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)}
    return templates.TemplateResponse(request=request, name="keol.html", context=ctx)

