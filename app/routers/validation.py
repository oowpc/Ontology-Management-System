"""统一校验结果查询与重校 API。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import GoldItem, OntologyRule, Operator, ValidationResult
from ..security import current_user, require_role
from ..templating import flash, templates
from ..validation import run

router = APIRouter(prefix="/api/validation", tags=["validation"])
page_router = APIRouter(tags=["validation"])

ObjectType = Literal["rule", "operator", "gold_item"]


class RunIn(BaseModel):
    object_type: ObjectType
    object_id: int = Field(ge=1)


class RevalidateIn(BaseModel):
    object_type: ObjectType | None = None
    limit: int = Field(default=100, ge=1, le=1000)


def _out(row: ValidationResult, full: bool = False) -> dict:
    data = {
        "id": row.id, "library_id": row.library_id,
        "object_type": row.object_type, "object_id": row.object_id,
        "object_key": row.object_key, "stage": row.stage,
        "ok": row.ok, "error_count": row.error_count,
        "warning_count": row.warning_count, "engine": row.engine,
        "checked_at": row.checked_at.isoformat(timespec="seconds") if row.checked_at else None,
        "created_by": row.created_by,
    }
    if full:
        data["result"] = row.result
    return data


def _get_object(db: Session, kind: str, oid: int):
    if kind == "rule":
        obj = db.get(OntologyRule, oid)
    elif kind == "operator":
        obj = db.get(Operator, oid)
    else:
        obj = db.get(GoldItem, oid)
    if obj is None:
        raise HTTPException(404, f"{kind} #{oid} 不存在")
    return obj


@router.get("/results")
def list_results(object_type: str | None = None, object_id: int | None = None,
                 ok: bool | None = None, page: int = 1,
                 size: int = Query(50, ge=1, le=200),
                 db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    stmt = select(ValidationResult)
    if object_type:
        stmt = stmt.where(ValidationResult.object_type == object_type)
    if object_id is not None:
        stmt = stmt.where(ValidationResult.object_id == object_id)
    if ok is not None:
        stmt = stmt.where(ValidationResult.ok == ok)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(ValidationResult.id.desc())
                      .offset((page - 1) * size).limit(size)).all()
    return {"items": [_out(r) for r in rows], "total": total,
            "page": page, "size": size}


@router.get("/results/{vid}")
def get_result(vid: int, db: Session = Depends(get_db),
               user: dict = Depends(require_role())):
    row = db.get(ValidationResult, vid)
    if row is None:
        raise HTTPException(404, "校验记录不存在")
    return _out(row, full=True)


@router.get("/summary")
def summary(db: Session = Depends(get_db), user: dict = Depends(require_role())):
    rows = db.execute(select(ValidationResult.object_type, ValidationResult.ok,
                             func.count(ValidationResult.id))
                      .group_by(ValidationResult.object_type, ValidationResult.ok)).all()
    out: dict[str, dict] = {}
    for kind, ok, count in rows:
        item = out.setdefault(kind, {"ok": 0, "error": 0})
        item["ok" if ok else "error"] += count
    return out


@router.post("/run")
def run_one(body: RunIn, db: Session = Depends(get_db),
            user: dict = Depends(require_role("reviewer", "admin"))):
    obj = _get_object(db, body.object_type, body.object_id)
    result = run(db, body.object_type, obj, stage="manual", actor=user["username"],
                 enforce=False)
    db.commit()
    return result.as_dict()


@router.post("/revalidate")
def revalidate(body: RevalidateIn, db: Session = Depends(get_db),
               user: dict = Depends(require_role("reviewer", "admin"))):
    kinds = [body.object_type] if body.object_type else ["rule", "operator", "gold_item"]
    summary = {"checked": 0, "ok": 0, "error": 0, "warning": 0, "items": []}
    for kind in kinds:
        model = {"rule": OntologyRule, "operator": Operator,
                 "gold_item": GoldItem}[kind]
        rows = db.scalars(select(model).order_by(model.id).limit(body.limit)).all()
        for obj in rows:
            try:
                res = run(db, kind, obj, stage="revalidate",
                          actor=user["username"], enforce=False)
                summary["checked"] += 1
                summary["ok" if res.ok else "error"] += 1
                if res.warnings:
                    summary["warning"] += 1
                summary["items"].append({
                    "object_type": kind, "object_id": obj.id,
                    "ok": res.ok, "errors": len(res.errors),
                    "warnings": len(res.warnings),
                })
            except Exception as e:  # noqa: BLE001
                summary["items"].append({
                    "object_type": kind, "object_id": obj.id,
                    "ok": False, "error": str(e)[:200],
                })
    db.commit()
    return summary


@page_router.get("/validation")
def validation_page(request: Request, object_type: str | None = None,
                    ok: bool | None = None, page: int = 1,
                    db: Session = Depends(get_db),
                    user: dict = Depends(current_user)):
    stmt = select(ValidationResult)
    if object_type:
        stmt = stmt.where(ValidationResult.object_type == object_type)
    if ok is not None:
        stmt = stmt.where(ValidationResult.ok == ok)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(ValidationResult.id.desc())
                      .offset((max(1, page) - 1) * 50).limit(50)).all()
    counts = db.execute(select(ValidationResult.object_type, ValidationResult.ok,
                               func.count(ValidationResult.id))
                        .group_by(ValidationResult.object_type, ValidationResult.ok)).all()
    summary = {}
    for kind, good, n in counts:
        item = summary.setdefault(kind, {"ok": 0, "error": 0})
        item["ok" if good else "error"] += n
    return templates.TemplateResponse(request=request, name="validation/list.html", context={
        "request": request, "user": user, "rows": rows, "total": total,
        "page": page, "pages": max(1, (total + 49) // 50),
        "object_type": object_type, "ok": ok, "summary": summary,
    })


@page_router.post("/validation/revalidate")
def validation_revalidate_page(request: Request, object_type: str = "",
                               db: Session = Depends(get_db),
                               user: dict = Depends(current_user)):
    if user["role"] not in {"reviewer", "admin"}:
        raise HTTPException(403, "批量重校仅审核员/管理员")
    body = RevalidateIn(object_type=object_type or None, limit=500)
    res = revalidate(body, db, user)
    flash(request, f"重校 {res['checked']} 条：通过 {res['ok']}，错误 {res['error']}")
    return RedirectResponse(f"/validation?object_type={object_type}", status_code=303)
