"""统一 KE 文档只读 API / 页面。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import KeDocument, KeDocumentVersion
from ..security import current_user, require_role
from ..templating import templates

router = APIRouter(tags=["ke-documents"])


def _doc(e: KeDocument) -> dict:
    return {"id": e.id, "source_type": e.source_type, "source_ref": e.source_ref,
            "question": e.question, "ke_text": e.ke_text,
            "content_hash": e.content_hash, "status": e.status,
            "ontology_version_id": e.ontology_version_id,
            "gold_item_id": e.gold_item_id, "validation": e.validation,
            "created_by": e.created_by,
            "created_at": e.created_at.isoformat(timespec="seconds") if e.created_at else None,
            "updated_at": e.updated_at.isoformat(timespec="seconds") if e.updated_at else None}


@router.get("/api/ke/documents")
def api_documents(source_type: str | None = None, status: str | None = None,
                  q: str | None = None, page: int = 1,
                  size: int = Query(50, ge=1, le=200),
                  db: Session = Depends(get_db), user: dict = Depends(require_role())):
    stmt = select(KeDocument)
    if source_type:
        stmt = stmt.where(KeDocument.source_type == source_type)
    if status:
        stmt = stmt.where(KeDocument.status == status)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(KeDocument.question.like(like),
                              KeDocument.ke_text.like(like),
                              KeDocument.source_ref.like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(KeDocument.id.desc())
                      .offset((max(1, page) - 1) * size).limit(size)).all()
    return {"items": [_doc(x) for x in rows], "total": total, "page": page, "size": size}


@router.get("/api/ke/documents/{did}")
def api_document(did: int, db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    doc = db.get(KeDocument, did)
    if doc is None:
        raise HTTPException(404, "KE 文档不存在")
    versions = db.scalars(select(KeDocumentVersion).where(
        KeDocumentVersion.document_id == did).order_by(KeDocumentVersion.version.desc())).all()
    out = _doc(doc)
    out["versions"] = [{"version": v.version, "ke_text": v.ke_text,
                        "content_hash": v.content_hash, "reason": v.reason,
                        "created_by": v.created_by,
                        "created_at": v.created_at.isoformat(timespec="seconds") if v.created_at else None}
                       for v in versions]
    return out


@router.get("/ke/documents")
def documents_page(request: Request, source_type: str | None = None,
                   status: str | None = None, q: str | None = None,
                   page: int = 1, db: Session = Depends(get_db),
                   user: dict = Depends(current_user)):
    stmt = select(KeDocument)
    if source_type:
        stmt = stmt.where(KeDocument.source_type == source_type)
    if status:
        stmt = stmt.where(KeDocument.status == status)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(KeDocument.question.like(like),
                              KeDocument.ke_text.like(like),
                              KeDocument.source_ref.like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(KeDocument.id.desc())
                      .offset((max(1, page) - 1) * 50).limit(50)).all()
    return templates.TemplateResponse(request=request, name="ke_docs/list.html", context={
        "request": request, "user": user, "rows": rows, "total": total,
        "page": page, "pages": max(1, (total + 49) // 50),
        "source_type": source_type, "status": status, "q": q,
    })
