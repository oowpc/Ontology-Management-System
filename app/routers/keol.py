"""M25 · KEOL 实例库：/keol 查询页 + /api/keol/* 只读检索。

数据为 KEOL 源（.md）的镜像，由 scripts/sync_keol.py --with-instances 全量重建；
本模块只读。口径与 KEOL 侧 tools/api_server.py 对齐（literals/outgoing/incoming）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..keolsync import mirror_status
from ..models import KeolEntity, KeolFact
from ..security import current_user, require_role
from ..templating import templates

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


# ================= 页面 =================

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

