"""Agent 全局搜索 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Concept, OntologyIndividual, OntologyRule, Operator
from ..security import require_role

router = APIRouter(prefix="/api/search", tags=["search"])
KINDS = ("concepts", "operators", "rules", "individuals")


@router.get("")
def search(q: str = Query(..., min_length=1), types: str = "",
           limit: int = Query(20, ge=1, le=100),
           db: Session = Depends(get_db), user: dict = Depends(require_role())):
    q = q.strip()
    wanted = [x.strip() for x in types.split(",") if x.strip()] or list(KINDS)
    bad = [x for x in wanted if x not in KINDS]
    if bad:
        from fastapi import HTTPException
        raise HTTPException(400, f"types 只能是 {list(KINDS)}，收到 {bad}")
    like = f"%{q}%"
    out = {}
    if "concepts" in wanted:
        rows = db.scalars(select(Concept).where(or_(
            Concept.name.like(like), Concept.definition.like(like)))
            .order_by(Concept.name).limit(limit)).all()
        out["concepts"] = [{"id": c.id, "name": c.name, "kind": "concept",
                            "domain": c.domain, "status": c.status} for c in rows]
    if "operators" in wanted:
        rows = db.scalars(select(Operator).where(Operator.name.like(like))
                          .order_by(Operator.name).limit(limit)).all()
        out["operators"] = [{"id": o.id, "name": o.name, "kind": "operator",
                             "return_type": o.return_type, "status": o.status}
                            for o in rows]
    if "rules" in wanted:
        rows = db.scalars(select(OntologyRule).where(or_(
            OntologyRule.name.like(like), OntologyRule.description.like(like)))
            .order_by(OntologyRule.name).limit(limit)).all()
        out["rules"] = [{"id": r.id, "name": r.name, "kind": "rule",
                         "domain": r.domain, "status": r.status} for r in rows]
    if "individuals" in wanted:
        rows = db.scalars(select(OntologyIndividual).where(
            OntologyIndividual.name.like(like)).order_by(OntologyIndividual.name)
            .limit(limit)).all()
        out["individuals"] = [{"id": i.id, "name": i.name, "kind": "individual",
                               "concept_id": i.concept_id, "status": i.status}
                              for i in rows]
    return {"q": q, "total": sum(len(v) for v in out.values()), "items": out}
