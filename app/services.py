"""概念 / 算子的共享读写逻辑：API 路由与页面路由共用。
M4 会把审计快照统一挂到这里的写操作上。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import String as SAString, cast, func, select
from sqlalchemy.orm import Session

from .audit import log_change, snapshot
from .config import settings
from .models import Concept, Operator, UsageStat
from .similarity import SimilarityBlock, find_similar

VALID_STATUS = {"draft", "pending", "approved", "disabled"}


# ---------- 查询辅助 ----------

def get_concept_or_404(db: Session, cid: int) -> Concept:
    c = db.get(Concept, cid)
    if not c:
        raise HTTPException(404, "概念不存在")
    return c


def get_operator_or_404(db: Session, oid: int) -> Operator:
    o = db.get(Operator, oid)
    if not o:
        raise HTTPException(404, "算子不存在")
    return o


def query_concepts(db: Session, *, status=None, parent_id=None, q=None, page=1, size=50):
    stmt = select(Concept)
    if status:
        if status not in VALID_STATUS:
            raise HTTPException(400, "非法状态")
        stmt = stmt.where(Concept.status == status)
    if parent_id is not None:
        stmt = stmt.where(Concept.parent_id == parent_id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            Concept.name.like(like)
            | cast(Concept.aliases, SAString).like(like)
            | Concept.definition.like(like)
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(Concept.id).offset((page - 1) * size).limit(size)).all()
    return items, total


def usage_map(db: Session, ids) -> dict[int, UsageStat]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {s.concept_id: s for s in db.scalars(select(UsageStat).where(UsageStat.concept_id.in_(ids)))}


def name_map(db: Session, ids) -> dict[int, str]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {c.id: c.name for c in db.scalars(select(Concept).where(Concept.id.in_(ids)))}


# ---------- 序列化 ----------

def concept_dict(c: Concept, *, parents=None, stats=None) -> dict:
    u = (stats or {}).get(c.id)
    return {
        "id": c.id,
        "name": c.name,
        "aliases": c.aliases or [],
        "definition": c.definition or "",
        "parent_id": c.parent_id,
        "parent_name": (parents or {}).get(c.parent_id) if c.parent_id else None,
        "status": c.status,
        "merged_into_id": c.merged_into_id,
        "created_by": c.created_by,
        "reviewed_by": c.reviewed_by,
        "ke_count": u.ke_count if u else 0,
        "last_used_at": u.last_used_at if u else None,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def operator_dict(o: Operator, *, names=None) -> dict:
    names = names or {}
    owner_id = o.owner_concept_id or o.subject_concept_id  # NULL 所属 = 同主体
    return {
        "id": o.id,
        "name": o.name,
        "subject_concept_id": o.subject_concept_id,
        "subject_name": names.get(o.subject_concept_id),
        "owner_concept_id": o.owner_concept_id,
        "owner_name": names.get(owner_id),
        "param_types": o.param_types or [],
        "return_type": o.return_type,
        "status": o.status,
        "created_by": o.created_by,
        "created_at": o.created_at,
        "updated_at": o.updated_at,
    }


# ---------- 概念写操作 ----------

def would_create_cycle(db: Session, concept_id: int, new_parent_id: int | None) -> bool:
    pid, seen = new_parent_id, set()
    while pid is not None:
        if pid == concept_id or pid in seen:
            return True
        seen.add(pid)
        p = db.get(Concept, pid)
        pid = p.parent_id if p else None
    return False


def create_concept(db: Session, *, name, aliases, definition, parent_id, status, actor,
                   check_similar: bool = False) -> Concept:
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    if status not in {"draft", "approved"}:
        raise HTTPException(400, "非法初始状态")
    if db.scalar(select(Concept).where(Concept.name == name)):
        raise HTTPException(409, f"概念「{name}」已存在")
    if parent_id is not None:
        get_concept_or_404(db, parent_id)
    if check_similar:
        hits = [s for s in find_similar(db, name=name, aliases=aliases, parent_id=parent_id)
                if s["score"] >= settings.similarity_block_threshold]
        if hits:
            raise SimilarityBlock(name, hits)
    c = Concept(
        name=name, aliases=list(aliases or []), definition=definition or "",
        parent_id=parent_id, status=status, created_by=actor, reviewed_by=actor,
    )
    db.add(c)
    db.flush()
    log_change(db, "concept", c.id, "create", None, snapshot(c), actor)
    db.commit()
    db.refresh(c)
    return c


def update_concept(db: Session, c: Concept, changes: dict, actor: str) -> Concept:
    before = snapshot(c)
    if "name" in changes:
        new_name = (changes["name"] or "").strip()
        if not new_name:
            raise HTTPException(400, "名称不能为空")
        dup = db.scalar(select(Concept).where(Concept.name == new_name, Concept.id != c.id))
        if dup:
            raise HTTPException(409, f"概念「{new_name}」已存在")
        c.name = new_name
    if "aliases" in changes and changes["aliases"] is not None:
        c.aliases = list(changes["aliases"])
    if "definition" in changes and changes["definition"] is not None:
        c.definition = changes["definition"]
    if "parent_id" in changes:
        pid = changes["parent_id"]
        if pid is not None:
            get_concept_or_404(db, pid)
            if would_create_cycle(db, c.id, pid):
                raise HTTPException(400, "会形成循环继承")
        c.parent_id = pid
    db.flush()
    log_change(db, "concept", c.id, "update", before, snapshot(c), actor)
    db.commit()
    db.refresh(c)
    return c


def set_concept_status(db: Session, c: Concept, status: str, actor: str,
                       action: str = "status", merged_into_id=None) -> Concept:
    """停用/恢复/合并等状态迁移的统一入口（留审计快照）。"""
    before = snapshot(c)
    c.status = status
    if merged_into_id is not None:
        c.merged_into_id = merged_into_id
    db.flush()
    log_change(db, "concept", c.id, action, before, snapshot(c), actor)
    db.commit()
    db.refresh(c)
    return c


# ---------- 算子写操作 ----------

def _check_operator_dup(db: Session, name: str, subject_id: int, exclude_id: int | None = None):
    stmt = select(Operator).where(Operator.name == name, Operator.subject_concept_id == subject_id)
    if exclude_id is not None:
        stmt = stmt.where(Operator.id != exclude_id)
    if db.scalar(stmt):
        raise HTTPException(409, "同一主体概念下算子名重复")


def create_operator(db: Session, *, name, subject_concept_id, owner_concept_id,
                    param_types, return_type, actor) -> Operator:
    name = (name or "").strip()
    return_type = (return_type or "").strip()
    if not name or not return_type:
        raise HTTPException(400, "名称与返回类型不能为空")
    get_concept_or_404(db, subject_concept_id)
    if owner_concept_id is not None:
        get_concept_or_404(db, owner_concept_id)
    _check_operator_dup(db, name, subject_concept_id)
    o = Operator(
        name=name, subject_concept_id=subject_concept_id, owner_concept_id=owner_concept_id,
        param_types=list(param_types or []), return_type=return_type,
        status="approved", created_by=actor,
    )
    db.add(o)
    db.flush()
    log_change(db, "operator", o.id, "create", None, snapshot(o), actor)
    db.commit()
    db.refresh(o)
    return o


def update_operator(db: Session, o: Operator, changes: dict, actor: str) -> Operator:
    before = snapshot(o)
    new_name = o.name
    new_subject = o.subject_concept_id
    if "subject_concept_id" in changes and changes["subject_concept_id"] is not None:
        get_concept_or_404(db, changes["subject_concept_id"])
        new_subject = changes["subject_concept_id"]
    if "name" in changes:
        new_name = (changes["name"] or "").strip()
        if not new_name:
            raise HTTPException(400, "名称不能为空")
    _check_operator_dup(db, new_name, new_subject, exclude_id=o.id)
    o.name = new_name
    o.subject_concept_id = new_subject
    if "owner_concept_id" in changes:
        oid = changes["owner_concept_id"]
        if oid is not None:
            get_concept_or_404(db, oid)
        o.owner_concept_id = oid
    if "param_types" in changes and changes["param_types"] is not None:
        o.param_types = list(changes["param_types"])
    if "return_type" in changes and changes["return_type"] is not None:
        rt = changes["return_type"].strip()
        if not rt:
            raise HTTPException(400, "返回类型不能为空")
        o.return_type = rt
    db.flush()
    log_change(db, "operator", o.id, "update", before, snapshot(o), actor)
    db.commit()
    db.refresh(o)
    return o


def set_operator_status(db: Session, o: Operator, status: str, actor: str) -> Operator:
    before = snapshot(o)
    o.status = status
    db.flush()
    log_change(db, "operator", o.id, "status", before, snapshot(o), actor)
    db.commit()
    db.refresh(o)
    return o
