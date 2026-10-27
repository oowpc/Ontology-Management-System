"""导入批次回滚：恢复更新过的概念/算子，删除本批新建且未被外部引用的条目。"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import RESTORABLE
from .models import (AuditLog, BackfillRecord, Concept, ConceptParent,
                     ImportBatch, Operator, QuestionConcept, Submission,
                     TmpMapping, UsageStat)
from .services import set_concept_parents


def _restore_concept(db: Session, c: Concept, snap: dict):
    for field in RESTORABLE["concept"]:
        if field in snap and field != "parents":
            setattr(c, field, snap[field])
    if "parents" in snap:
        ids = []
        for name in snap.get("parents") or []:
            p = db.scalar(select(Concept).where(Concept.name == name))
            if p:
                ids.append(p.id)
        set_concept_parents(db, c, ids)


def _restore_operator(db: Session, o: Operator, snap: dict):
    for field in RESTORABLE["operator"]:
        if field in snap:
            setattr(o, field, snap[field])


def collect_references(db: Session, created_c: set[int], created_o: set[int]) -> list[str]:
    refs: list[str] = []
    if not created_c:
        return refs
    # 外部子概念仍指向本批新建父类
    rows = db.execute(
        select(ConceptParent.concept_id, ConceptParent.parent_id)
        .where(ConceptParent.parent_id.in_(created_c))
        .where(ConceptParent.concept_id.notin_(created_c))).all()
    if rows:
        refs.append(f"概念父类引用：{len(rows)} 条")
    # 外部算子挂在本批新建概念上
    ops = db.scalars(select(Operator).where(
        (Operator.subject_concept_id.in_(created_c)) | (Operator.owner_concept_id.in_(created_c))
    )).all()
    external_ops = [o for o in ops if o.id not in created_o]
    if external_ops:
        refs.append(f"外部算子引用本批概念：{len(external_ops)} 条")
    if created_o:
        qhit = db.scalar(select(QuestionConcept).where(
            QuestionConcept.operator_id.in_(created_o)).limit(1))
        if qhit:
            refs.append("QuestionConcept 引用本批算子")
    # 概念表 legacy 字段
    legacy = db.scalar(select(Concept.id).where(
        (Concept.parent_id.in_(created_c)) | (Concept.merged_into_id.in_(created_c))
    ).where(Concept.id.notin_(created_c)).limit(1))
    if legacy:
        refs.append("concepts.parent_id/merged_into_id 外部引用")
    # 业务数据
    for model, fields in [
        (Submission, ("concept_id", "suggested_parent_id", "merge_target_id")),
        (TmpMapping, ("concept_id",)),
        (BackfillRecord, ("concept_id",)),
        (UsageStat, ("concept_id",)),
        (QuestionConcept, ("concept_id",)),
    ]:
        clauses = [getattr(model, f).in_(created_c) for f in fields]
        cond = clauses[0]
        for c in clauses[1:]:
            cond = cond | c
        hit = db.scalar(select(model).where(cond).limit(1))
        if hit:
            refs.append(f"{model.__tablename__} 引用本批概念")
    return refs


def rollback_batch(db: Session, batch_id: str, *, force: bool = False,
                   dry_run: bool = False) -> dict:
    batch = db.scalar(select(ImportBatch).where(ImportBatch.batch_id == batch_id))
    if not batch:
        raise HTTPException(404, f"导入批次「{batch_id}」不存在")
    if batch.status == "rolled_back":
        return {"batch_id": batch_id, "status": "already_rolled_back"}
    created_c = set(batch.created_concept_ids or [])
    created_o = set(batch.created_operator_ids or [])
    updated_c = set(batch.updated_concept_ids or [])
    updated_o = set(batch.updated_operator_ids or [])

    # 先恢复更新过的概念/算子（取每个实体最早一条 before 快照）；这样会移除本批临时挂上的父子链接
    audits = db.scalars(select(AuditLog).where(
        AuditLog.actor == batch.actor,
        AuditLog.entity_type.in_(("concept", "operator")),
        AuditLog.action == "import").order_by(AuditLog.id)).all()
    first_before: dict[tuple[str, int], dict] = {}
    for a in audits:
        key = (a.entity_type, a.entity_id)
        if key not in first_before and a.before is not None:
            first_before[key] = a.before
    restored_c = restored_o = 0
    for (etype, eid), snap in first_before.items():
        if etype == "concept" and eid in updated_c:
            c = db.get(Concept, eid)
            if c:
                _restore_concept(db, c, snap)
                restored_c += 1
        elif etype == "operator" and eid in updated_o:
            o = db.get(Operator, eid)
            if o:
                _restore_operator(db, o, snap)
                restored_o += 1
    db.flush()

    refs = collect_references(db, created_c, created_o)
    if refs and not force:
        db.rollback()
        return {"batch_id": batch_id, "status": "blocked", "references": refs}
    if dry_run:
        db.rollback()
        return {
            "batch_id": batch_id, "status": "dry_run",
            "deleted_concepts": len(created_c), "deleted_operators": len(created_o),
            "restored_concepts": restored_c, "restored_operators": restored_o,
            "references": refs,
        }

    # 删除本批新建算子
    if created_o:
        for o in db.scalars(select(Operator).where(Operator.id.in_(created_o))).all():
            db.delete(o)
    if created_c:
        for link in db.scalars(select(ConceptParent).where(
                (ConceptParent.concept_id.in_(created_c)) |
                (ConceptParent.parent_id.in_(created_c)))).all():
            db.delete(link)
        # 清 legacy 字段/外部引用（force）
        for c in db.scalars(select(Concept).where(
                (Concept.parent_id.in_(created_c)) |
                (Concept.merged_into_id.in_(created_c))).where(Concept.id.notin_(created_c))).all():
            if c.parent_id in created_c:
                c.parent_id = None
            if c.merged_into_id in created_c:
                c.merged_into_id = None
        for s in db.scalars(select(UsageStat).where(UsageStat.concept_id.in_(created_c))).all():
            db.delete(s)
        for c in db.scalars(select(Concept).where(Concept.id.in_(created_c))).all():
            db.delete(c)
    batch.status = "rolled_back"
    batch.finished_at = datetime.now()
    db.commit()
    return {
        "batch_id": batch_id, "status": "rolled_back",
        "deleted_concepts": len(created_c), "deleted_operators": len(created_o),
        "restored_concepts": restored_c, "restored_operators": restored_o,
        "references": refs,
    }
