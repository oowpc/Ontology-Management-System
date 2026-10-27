"""算子 API：/api/operators"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Operator
from ..schemas import OperatorCreate, OperatorUpdate
from ..security import require_role
from ..services import (create_operator, get_operator_or_404, name_map,
                        operator_dict, set_operator_status, update_operator)

router = APIRouter(prefix="/api/operators", tags=["operators"])


def _out(db: Session, o: Operator) -> dict:
    return operator_dict(o, names=name_map(db, [o.subject_concept_id, o.owner_concept_id]))


@router.get("")
def list_operators(subject_concept_id: int | None = None, q: str | None = None,
                   page: int = 1, size: int = Query(100, le=500),
                   db: Session = Depends(get_db), user: dict = Depends(require_role())):
    stmt = select(Operator)
    if subject_concept_id is not None:
        stmt = stmt.where(Operator.subject_concept_id == subject_concept_id)
    if q and q.strip():
        stmt = stmt.where(Operator.name.like(f"%{q.strip()}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(Operator.id).offset((page - 1) * size).limit(size)).all()
    names = name_map(db, [o.subject_concept_id for o in items] + [o.owner_concept_id for o in items])
    return {"items": [operator_dict(o, names=names) for o in items],
            "total": total, "page": page, "size": size}


@router.post("", status_code=201)
def create(data: OperatorCreate, db: Session = Depends(get_db),
           user: dict = Depends(require_role("reviewer", "admin"))):
    o = create_operator(db, name=data.name, subject_concept_id=data.subject_concept_id,
                        owner_concept_id=data.owner_concept_id, param_types=data.param_types,
                        return_type=data.return_type, source_uri=data.source_uri,
                        ontology_module=data.ontology_module, actor=user["username"])
    return _out(db, o)


@router.get("/{oid}")
def detail(oid: int, db: Session = Depends(get_db), user: dict = Depends(require_role())):
    return _out(db, get_operator_or_404(db, oid))


@router.patch("/{oid}")
def update(oid: int, data: OperatorUpdate, db: Session = Depends(get_db),
           user: dict = Depends(require_role("reviewer", "admin"))):
    o = get_operator_or_404(db, oid)
    o = update_operator(db, o, data.model_dump(exclude_unset=True), user["username"])
    return _out(db, o)


@router.post("/{oid}/disable")
def disable(oid: int, db: Session = Depends(get_db),
            user: dict = Depends(require_role("reviewer", "admin"))):
    o = get_operator_or_404(db, oid)
    return _out(db, set_operator_status(db, o, "disabled", user["username"]))
