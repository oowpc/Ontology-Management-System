"""概念 API：/api/concepts"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String as SAString, cast, select
from sqlalchemy.orm import Session

from ..audit import audit_dict, entity_history
from ..audit import rollback as audit_rollback
from ..db import get_db
from ..models import AuditLog, Concept, ConceptParent, Operator
from ..schemas import ConceptCreate, ConceptUpdate
from ..security import require_role
from ..services import (concept_dict, concept_parents_map, create_concept,
                        get_concept_or_404, name_map, operator_dict,
                        query_concepts, set_concept_status, update_concept,
                        usage_map)
from ..similarity import SimilarityBlock

router = APIRouter(prefix="/api/concepts", tags=["concepts"])


def _out(db: Session, c: Concept) -> dict:
    return concept_dict(
        c,
        parents=name_map(db, [c.parent_id] if c.parent_id else []),
        stats=usage_map(db, [c.id]),
        parent_details=concept_parents_map(db, [c.id]).get(c.id, []),
    )


@router.get("")
def list_concepts(status: str | None = None, parent_id: int | None = None,
                  q: str | None = None, domain: str | None = None,
                  page: int = 1, size: int = Query(50, le=200),
                  db: Session = Depends(get_db), user: dict = Depends(require_role())):
    items, total = query_concepts(db, status=status, parent_id=parent_id, q=q,
                                  domain=domain, page=page, size=size)
    stats = usage_map(db, [c.id for c in items])
    parents = name_map(db, [c.parent_id for c in items if c.parent_id])
    parent_details = concept_parents_map(db, [c.id for c in items])
    return {
        "items": [concept_dict(c, parents=parents, stats=stats,
                               parent_details=parent_details.get(c.id, [])) for c in items],
        "total": total, "page": page, "size": size,
    }


@router.post("", status_code=201)
def create(data: ConceptCreate, db: Session = Depends(get_db),
           user: dict = Depends(require_role("reviewer", "admin"))):
    try:
        c = create_concept(db, name=data.name, domain=data.domain, aliases=data.aliases,
                           definition=data.definition, parent_id=data.parent_id,
                           parents=data.parents, source_uri=data.source_uri,
                           ontology_module=data.ontology_module, status=data.status,
                           actor=user["username"], check_similar=True)
    except SimilarityBlock as e:
        raise HTTPException(409, detail={"message": str(e), "similar": e.similar})
    return _out(db, c)


@router.get("/search")
def search_concepts(q: str = "", status: str | None = None,
                    limit: int = Query(20, ge=1, le=50),
                    exclude_id: int | None = None,
                    db: Session = Depends(get_db),
                    user: dict = Depends(require_role())):
    """概念选择器轻量搜索：只返回 id/name/domain/status，避免大页面塞满 option。"""
    stmt = select(Concept)
    if status:
        stmt = stmt.where(Concept.status == status)
    if exclude_id is not None:
        stmt = stmt.where(Concept.id != exclude_id)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(Concept.name.like(like) | cast(Concept.aliases, SAString).like(like))
    items = db.scalars(stmt.order_by(Concept.name).limit(limit)).all()
    return {"items": [{"id": c.id, "name": c.name, "domain": c.domain or "通用",
                       "status": c.status} for c in items]}


@router.get("/{cid}")
def detail(cid: int, db: Session = Depends(get_db), user: dict = Depends(require_role())):
    c = get_concept_or_404(db, cid)
    d = _out(db, c)
    child_ids = select(ConceptParent.concept_id).where(ConceptParent.parent_id == cid)
    children = db.scalars(select(Concept).where(Concept.id.in_(child_ids)).order_by(Concept.id)).all()
    ops = db.scalars(select(Operator).where(Operator.subject_concept_id == cid).order_by(Operator.id)).all()
    names = name_map(db, [o.subject_concept_id for o in ops] + [o.owner_concept_id for o in ops])
    d["children"] = [concept_dict(ch) for ch in children]
    d["operators"] = [operator_dict(o, names=names) for o in ops]
    return d


@router.patch("/{cid}")
def update(cid: int, data: ConceptUpdate, db: Session = Depends(get_db),
           user: dict = Depends(require_role("reviewer", "admin"))):
    c = get_concept_or_404(db, cid)
    c = update_concept(db, c, data.model_dump(exclude_unset=True), user["username"])
    return _out(db, c)


@router.post("/{cid}/disable")
def disable(cid: int, db: Session = Depends(get_db),
            user: dict = Depends(require_role("reviewer", "admin"))):
    c = get_concept_or_404(db, cid)
    return _out(db, set_concept_status(db, c, "disabled", user["username"]))


@router.post("/{cid}/restore")
def restore(cid: int, db: Session = Depends(get_db),
            user: dict = Depends(require_role("reviewer", "admin"))):
    c = get_concept_or_404(db, cid)
    return _out(db, set_concept_status(db, c, "approved", user["username"]))


@router.get("/{cid}/history")
def history(cid: int, db: Session = Depends(get_db), user: dict = Depends(require_role())):
    get_concept_or_404(db, cid)
    return {"items": [audit_dict(a) for a in entity_history(db, "concept", cid)]}


@router.post("/{cid}/rollback/{audit_id}")
def rollback_ep(cid: int, audit_id: int, db: Session = Depends(get_db),
                user: dict = Depends(require_role("admin"))):
    entry = db.get(AuditLog, audit_id)
    if not entry or entry.entity_type != "concept" or entry.entity_id != cid:
        raise HTTPException(404, "历史记录不存在")
    c = audit_rollback(db, audit_id, user["username"])
    return _out(db, c)
