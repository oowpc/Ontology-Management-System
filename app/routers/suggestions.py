"""本体建议箱：KEOL/agent 提交，OMS 审批后写入全局。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import OntologySuggestion
from ..security import current_user, require_role
from ..suggestions import (approve_suggestion, create_suggestion,
                           reject_suggestion, suggestion_dict)
from ..templating import flash, templates

router = APIRouter(tags=["suggestions"])


class SuggestionIn(BaseModel):
    title: str = ""
    origin: str = "keol"
    source: str = "keol"
    note: str = ""
    package: dict
    refs: dict = Field(default_factory=dict)


class RejectIn(BaseModel):
    note: str = ""


def _page_data(db: Session, status: str | None = None):
    stmt = select(OntologySuggestion)
    if status:
        stmt = stmt.where(OntologySuggestion.status == status)
    return db.scalars(stmt.order_by(OntologySuggestion.id.desc())).all()


@router.post("/api/keol/suggestions", status_code=201)
def create_suggestion_api(body: SuggestionIn, db: Session = Depends(get_db),
                          user: dict = Depends(require_role("reviewer", "admin"))):
    s = create_suggestion(db, body.package, body.refs, user["username"],
                          title=body.title, origin=body.origin,
                          source=body.source, note=body.note)
    return suggestion_dict(s)


@router.get("/api/keol/suggestions")
def list_suggestions_api(status: str | None = None, db: Session = Depends(get_db),
                         user: dict = Depends(require_role())):
    items = _page_data(db, status)
    out = []
    for s in items:
        d = suggestion_dict(s)
        d.pop("package", None)
        d.pop("refs", None)
        out.append(d)
    return {"items": out, "total": len(out)}


@router.get("/api/keol/suggestions/{sid}")
def get_suggestion_api(sid: int, db: Session = Depends(get_db),
                       user: dict = Depends(require_role())):
    s = db.get(OntologySuggestion, sid)
    if not s:
        raise HTTPException(404, "建议不存在")
    return suggestion_dict(s)


@router.post("/api/keol/suggestions/{sid}/approve")
def approve_suggestion_api(sid: int, db: Session = Depends(get_db),
                           user: dict = Depends(require_role("reviewer", "admin"))):
    s = db.get(OntologySuggestion, sid)
    if not s:
        raise HTTPException(404, "建议不存在")
    stats = approve_suggestion(db, s, user["username"])
    return {"status": "approved", "stats": stats, "suggestion": suggestion_dict(s)}


@router.post("/api/keol/suggestions/{sid}/reject")
def reject_suggestion_api(sid: int, body: RejectIn, db: Session = Depends(get_db),
                          user: dict = Depends(require_role("reviewer", "admin"))):
    s = db.get(OntologySuggestion, sid)
    if not s:
        raise HTTPException(404, "建议不存在")
    reject_suggestion(db, s, user["username"], body.note)
    return {"status": "rejected", "suggestion": suggestion_dict(s)}


@router.get("/suggestions")
def suggestions_page(request: Request, status: str | None = None,
                     db: Session = Depends(get_db), user: dict = Depends(current_user)):
    items = _page_data(db, status)
    return templates.TemplateResponse(request=request, name="suggestions/list.html", context={
        "request": request, "user": user, "items": items, "status": status,
        "pending": db.scalar(select(func.count(OntologySuggestion.id)).where(
            OntologySuggestion.status == "pending")) or 0,
    })


@router.get("/suggestions/{sid}")
def suggestion_detail_page(sid: int, request: Request, db: Session = Depends(get_db),
                           user: dict = Depends(current_user)):
    s = db.get(OntologySuggestion, sid)
    if not s:
        raise HTTPException(404, "建议不存在")
    return templates.TemplateResponse(request=request, name="suggestions/detail.html", context={
        "request": request, "user": user, "s": s, "summary": s.summary or {},
    })


@router.post("/suggestions/{sid}/approve")
def suggestion_approve_page(sid: int, request: Request, db: Session = Depends(get_db),
                            user: dict = Depends(current_user)):
    s = db.get(OntologySuggestion, sid)
    if not s:
        raise HTTPException(404, "建议不存在")
    if user["role"] not in {"reviewer", "admin"}:
        raise HTTPException(403, "权限不足")
    try:
        approve_suggestion(db, s, user["username"])
    except HTTPException as e:
        return RedirectResponse(f"/suggestions/{sid}?err={e.detail}", status_code=303)
    flash(request, "建议已审批并写入全局")
    return RedirectResponse(f"/suggestions/{sid}", status_code=303)


@router.post("/suggestions/{sid}/reject")
async def suggestion_reject_page(sid: int, request: Request, db: Session = Depends(get_db),
                                 user: dict = Depends(current_user)):
    s = db.get(OntologySuggestion, sid)
    if not s:
        raise HTTPException(404, "建议不存在")
    if user["role"] not in {"reviewer", "admin"}:
        raise HTTPException(403, "权限不足")
    form = await request.form()
    reject_suggestion(db, s, user["username"], str(form.get("note") or ""))
    flash(request, "建议已驳回")
    return RedirectResponse(f"/suggestions/{sid}", status_code=303)
