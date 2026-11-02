"""Agent JSON API：全局规则 / 个体 CRUD。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import log_change, snapshot
from ..db import get_db
from ..models import Concept, OntologyIndividual, OntologyRule
from ..security import require_role

router = APIRouter(prefix="/api", tags=["library-api"])
Status = Literal["draft", "pending", "approved", "disabled"]
VALID_STATUS = {"draft", "pending", "approved", "disabled"}


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    domain: str = "通用"
    description: str = ""
    body: dict | None = None
    head: list[dict] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    supply: dict = Field(default_factory=dict)
    status: Status = "approved"
    source_uri: str | None = None
    ontology_module: str | None = None

    @model_validator(mode="after")
    def _check_head(self):
        if self.status == "approved" and not self.head:
            raise ValueError("approved 规则的 head 不能为空")
        return self


class RulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    domain: str | None = None
    description: str | None = None
    body: dict | None = None
    head: list[dict] | None = None
    sources: list[str] | None = None
    supply: dict | None = None
    status: Status | None = None
    source_uri: str | None = None
    ontology_module: str | None = None


class IndividualCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    concept_id: int | None = None
    concept: str | None = None
    domain: str = "通用"
    description: str = ""
    sources: list[str] = Field(default_factory=list)
    supply: dict = Field(default_factory=dict)
    status: Status = "approved"
    source_uri: str | None = None
    ontology_module: str | None = None

    @model_validator(mode="after")
    def _check_concept(self):
        if self.concept_id is None and not (self.concept or "").strip():
            raise ValueError("必须提供 concept_id 或 concept")
        return self


class IndividualPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    concept_id: int | None = None
    concept: str | None = None
    domain: str | None = None
    description: str | None = None
    sources: list[str] | None = None
    supply: dict | None = None
    status: Status | None = None
    source_uri: str | None = None
    ontology_module: str | None = None


def _rule_out(r: OntologyRule) -> dict:
    return {
        "id": r.id, "name": r.name, "domain": r.domain,
        "description": r.description or "", "body": r.body, "head": r.head or [],
        "sources": r.sources or [], "supply": r.supply or {}, "status": r.status,
        "source_uri": r.source_uri or "", "ontology_module": r.ontology_module or "",
        "created_by": r.created_by, "reviewed_by": r.reviewed_by,
        "created_at": r.created_at.isoformat(timespec="seconds") if r.created_at else None,
        "updated_at": r.updated_at.isoformat(timespec="seconds") if r.updated_at else None,
    }


def _ind_out(i: OntologyIndividual, concept: Concept | None) -> dict:
    return {
        "id": i.id, "name": i.name, "concept_id": i.concept_id,
        "concept": concept.name if concept else None,
        "domain": i.domain, "description": i.description or "",
        "sources": i.sources or [], "supply": i.supply or {}, "status": i.status,
        "source_uri": i.source_uri or "", "ontology_module": i.ontology_module or "",
        "created_by": i.created_by, "reviewed_by": i.reviewed_by,
        "created_at": i.created_at.isoformat(timespec="seconds") if i.created_at else None,
        "updated_at": i.updated_at.isoformat(timespec="seconds") if i.updated_at else None,
    }


def _resolve_concept(db: Session, concept_id: int | None, concept: str | None) -> Concept:
    c = db.get(Concept, concept_id) if concept_id is not None else None
    if c is None and (concept or "").strip():
        c = db.scalar(select(Concept).where(Concept.name == concept.strip()))
    if c is None:
        raise HTTPException(400, f"所属概念不存在：{concept or concept_id}")
    return c


def _check_rule(db: Session, name: str, head: list, status: str, exclude_id: int | None = None):
    q = select(OntologyRule).where(OntologyRule.name == name)
    if exclude_id is not None:
        q = q.where(OntologyRule.id != exclude_id)
    if db.scalar(q):
        raise HTTPException(409, f"规则「{name}」已存在")
    if status == "approved" and not head:
        raise HTTPException(400, "approved 规则的 head 不能为空")


# ================= 规则 =================

@router.get("/rules")
def list_rules(q: str | None = None, status: str | None = None,
               domain: str | None = None, page: int = 1, size: int = Query(50, ge=1, le=200),
               db: Session = Depends(get_db), user: dict = Depends(require_role())):
    stmt = select(OntologyRule)
    if q:
        stmt = stmt.where(or_(OntologyRule.name.like(f"%{q}%"),
                              OntologyRule.description.like(f"%{q}%")))
    if status:
        stmt = stmt.where(OntologyRule.status == status)
    if domain:
        stmt = stmt.where(OntologyRule.domain == domain)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(OntologyRule.name)
                      .limit(size).offset(max(0, page - 1) * size)).all()
    return {"items": [_rule_out(r) for r in rows], "total": total,
            "page": page, "size": size}


@router.post("/rules", status_code=201)
def create_rule(data: RuleCreate, db: Session = Depends(get_db),
                user: dict = Depends(require_role("reviewer", "admin"))):
    _check_rule(db, data.name, data.head, data.status)
    r = OntologyRule(name=data.name, domain=data.domain, description=data.description,
                     body=data.body, head=data.head, sources=data.sources,
                     supply=data.supply, status=data.status,
                     source_uri=data.source_uri, ontology_module=data.ontology_module,
                     created_by=user["username"], reviewed_by=user["username"])
    db.add(r)
    db.flush()
    log_change(db, "rule", r.id, "create", None, snapshot(r), user["username"])
    db.commit()
    db.refresh(r)
    return _rule_out(r)


@router.get("/rules/{rid}")
def get_rule(rid: int, db: Session = Depends(get_db),
             user: dict = Depends(require_role())):
    r = db.get(OntologyRule, rid)
    if not r:
        raise HTTPException(404, "规则不存在")
    return _rule_out(r)


@router.patch("/rules/{rid}")
def update_rule(rid: int, data: RulePatch, db: Session = Depends(get_db),
                user: dict = Depends(require_role("reviewer", "admin"))):
    r = db.get(OntologyRule, rid)
    if not r:
        raise HTTPException(404, "规则不存在")
    changes = data.model_dump(exclude_unset=True)
    name = changes.get("name", r.name)
    head = changes.get("head", r.head or [])
    status = changes.get("status", r.status)
    _check_rule(db, name, head, status, exclude_id=rid)
    before = snapshot(r)
    for attr in ("name", "domain", "description", "body", "head", "sources",
                 "supply", "status", "source_uri", "ontology_module"):
        if attr in changes:
            setattr(r, attr, changes[attr])
    r.reviewed_by = user["username"]
    db.flush()
    log_change(db, "rule", r.id, "update", before, snapshot(r), user["username"])
    db.commit()
    db.refresh(r)
    return _rule_out(r)


# ================= 个体 =================

@router.get("/individuals")
def list_individuals(q: str | None = None, status: str | None = None,
                     domain: str | None = None, concept_id: int | None = None,
                     page: int = 1, size: int = Query(50, ge=1, le=200),
                     db: Session = Depends(get_db), user: dict = Depends(require_role())):
    stmt = select(OntologyIndividual)
    if q:
        stmt = stmt.where(OntologyIndividual.name.like(f"%{q}%"))
    if status:
        stmt = stmt.where(OntologyIndividual.status == status)
    if domain:
        stmt = stmt.where(OntologyIndividual.domain == domain)
    if concept_id:
        stmt = stmt.where(OntologyIndividual.concept_id == concept_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(OntologyIndividual.concept_id, OntologyIndividual.name)
                      .limit(size).offset(max(0, page - 1) * size)).all()
    names = {c.id: c for c in db.scalars(select(Concept).where(
        Concept.id.in_({i.concept_id for i in rows}))).all()} if rows else {}
    return {"items": [_ind_out(i, names.get(i.concept_id)) for i in rows],
            "total": total, "page": page, "size": size}


@router.post("/individuals", status_code=201)
def create_individual(data: IndividualCreate, db: Session = Depends(get_db),
                      user: dict = Depends(require_role("reviewer", "admin"))):
    c = _resolve_concept(db, data.concept_id, data.concept)
    if db.scalar(select(OntologyIndividual).where(
            OntologyIndividual.concept_id == c.id, OntologyIndividual.name == data.name)):
        raise HTTPException(409, f"概念「{c.name}」下已有个体「{data.name}」")
    i = OntologyIndividual(name=data.name, concept_id=c.id, domain=data.domain,
                           description=data.description, sources=data.sources,
                           supply=data.supply, status=data.status,
                           source_uri=data.source_uri,
                           ontology_module=data.ontology_module,
                           created_by=user["username"], reviewed_by=user["username"])
    db.add(i)
    db.flush()
    log_change(db, "individual", i.id, "create", None, snapshot(i), user["username"])
    db.commit()
    db.refresh(i)
    return _ind_out(i, c)


@router.get("/individuals/{iid}")
def get_individual(iid: int, db: Session = Depends(get_db),
                   user: dict = Depends(require_role())):
    i = db.get(OntologyIndividual, iid)
    if not i:
        raise HTTPException(404, "个体不存在")
    return _ind_out(i, db.get(Concept, i.concept_id))


@router.patch("/individuals/{iid}")
def update_individual(iid: int, data: IndividualPatch, db: Session = Depends(get_db),
                      user: dict = Depends(require_role("reviewer", "admin"))):
    i = db.get(OntologyIndividual, iid)
    if not i:
        raise HTTPException(404, "个体不存在")
    changes = data.model_dump(exclude_unset=True)
    c = db.get(Concept, i.concept_id)
    if "concept_id" in changes or "concept" in changes:
        c = _resolve_concept(db, changes.get("concept_id"), changes.get("concept"))
        changes["concept_id"] = c.id
    name = changes.get("name", i.name)
    if db.scalar(select(OntologyIndividual).where(
            OntologyIndividual.concept_id == c.id, OntologyIndividual.name == name,
            OntologyIndividual.id != iid)):
        raise HTTPException(409, f"概念「{c.name}」下已有个体「{name}」")
    before = snapshot(i)
    for attr in ("name", "concept_id", "domain", "description", "sources", "supply",
                 "status", "source_uri", "ontology_module"):
        if attr in changes:
            setattr(i, attr, changes[attr])
    i.reviewed_by = user["username"]
    db.flush()
    log_change(db, "individual", i.id, "update", before, snapshot(i), user["username"])
    db.commit()
    db.refresh(i)
    return _ind_out(i, db.get(Concept, i.concept_id))
