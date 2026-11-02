"""SOP 手工标注工作流：批次、标注单元、evidence、CO 双盲、对齐、KE、GOLD、版本、交叉检查。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import log_change, snapshot
from ..library_context import library_scoped
from ..models import (AnnotationBatch, AnnotationCrossReview, AnnotationEvidence,
                      AnnotationMember, AnnotationUnit, CoAlignment, CoAnnotation,
                      CoMergeLog, Concept, ConceptParent, GoldKe, KeAnnotation,
                      OntologyIndividual, OntologyRule, OntologyVersion, Operator,
                      OperatorInput, QueryRun, User)
from ..paster import check_type_expr
from ..services import (concept_parents_map, operator_inputs_map, operator_signature,
                        set_concept_parents, sync_operator_inputs)

STATE_LABELS = {
    "draft": "草稿",
    "dataset_frozen": "数据集已冻结",
    "co_blind": "CO 双盲中",
    "co_alignment": "CO 逐题对齐中",
    "co_merge": "本体合并中",
    "ke_blind": "KE 双盲中",
    "gold_merge": "GOLD 合并中",
    "cross_review": "交叉检查中",
    "validation": "待校验",
    "passed": "已通过",
    "rework": "返工中",
}
STATE_ORDER = ["draft", "dataset_frozen", "co_blind", "co_alignment", "co_merge",
               "ke_blind", "gold_merge", "cross_review", "validation", "passed"]
MEMBER_ROLE_LABELS = {
    "annotator": "标注者",
    "lead": "组长",
    "arbiter": "裁决人",
    "batch_owner": "批次负责人",
    "cross_checker": "交叉检查",
    "project_owner": "项目负责人",
}
BUILTIN_TYPES = {"String", "Number", "Bool", "Date", "TimeInterval", "Real", "Name", "Concept"}
UNIT_STATUS_LABELS = {
    "pending": "待标注",
    "co_a_done": "CO A 已交",
    "co_b_done": "CO B 已交",
    "co_both_done": "CO 双盲已交",
    "aligned": "CO 已对齐",
    "aligned_pending": "CO 对齐待裁决",
    "ke_a_done": "KE A 已交",
    "ke_b_done": "KE B 已交",
    "ke_both_done": "KE 双盲已交",
    "gold_done": "GOLD 已完成",
}


@library_scoped
def get_batch_or_404(db: Session, bid: int) -> AnnotationBatch:
    b = db.get(AnnotationBatch, bid)
    if not b:
        raise HTTPException(404, "标注批次不存在")
    return b

@library_scoped
def get_unit_or_404(db: Session, uid: int) -> AnnotationUnit:
    u = db.get(AnnotationUnit, uid)
    if not u:
        raise HTTPException(404, "标注单元不存在")
    return u

@library_scoped
def get_version_or_404(db: Session, vid: int) -> OntologyVersion:
    v = db.get(OntologyVersion, vid)
    if not v:
        raise HTTPException(404, "本体版本不存在")
    return v

def _is_admin(user: dict) -> bool:
    return user.get("role") == "admin"

def _members(db: Session, batch_id: int, username: str | None = None):
    stmt = select(AnnotationMember).where(AnnotationMember.batch_id == batch_id)
    if username is not None:
        stmt = stmt.where(AnnotationMember.username == username)
    return db.scalars(stmt.order_by(AnnotationMember.id)).all()

def roles_for(db: Session, batch: AnnotationBatch, user: dict) -> set[str]:
    if _is_admin(user):
        return {"admin"}
    return {m.role for m in _members(db, batch.id, user["username"])}

def sides_for(db: Session, batch: AnnotationBatch, user: dict) -> set[str]:
    return {m.side for m in _members(db, batch.id, user["username"])
            if m.role == "annotator" and m.side}

def can_create_batch(user: dict) -> bool:
    return user.get("role") in {"admin", "reviewer"}

def can_view_batch(db: Session, batch: AnnotationBatch, user: dict) -> bool:
    return _is_admin(user) or bool(_members(db, batch.id, user["username"]))

def require_view_batch(db: Session, batch: AnnotationBatch, user: dict):
    if not can_view_batch(db, batch, user):
        raise HTTPException(403, "无权访问该批次")

def can_manage(db: Session, batch: AnnotationBatch, user: dict) -> bool:
    return _is_admin(user) or bool(roles_for(db, batch, user) & {"lead", "batch_owner"})

def require_manage(db: Session, batch: AnnotationBatch, user: dict):
    if not can_manage(db, batch, user):
        raise HTTPException(403, "仅组长/批次负责人可操作")

def can_project_approve(db: Session, batch: AnnotationBatch, user: dict) -> bool:
    return _is_admin(user) or "project_owner" in roles_for(db, batch, user)

def can_annotate_side(db: Session, batch: AnnotationBatch, user: dict, side: str) -> bool:
    return _is_admin(user) or side in sides_for(db, batch, user)

def can_align(db: Session, batch: AnnotationBatch, user: dict) -> bool:
    return _is_admin(user) or bool(roles_for(db, batch, user) & {"annotator", "lead", "arbiter"})

def can_cross_review(db: Session, batch: AnnotationBatch, user: dict) -> bool:
    return _is_admin(user) or bool(roles_for(db, batch, user) & {"cross_checker", "lead"})

def can_view_unit_data(db: Session, batch: AnnotationBatch, user: dict) -> bool:
    if _is_admin(user):
        return True
    roles = roles_for(db, batch, user)
    if roles & {"lead", "arbiter", "batch_owner", "project_owner", "annotator"}:
        return True
    if "cross_checker" in roles:
        return batch.state in {"cross_review", "validation", "passed", "rework"}
    return False

def _user_exists(db: Session, username: str) -> bool:
    return bool(db.scalar(select(User.id).where(User.username == username)))

def _add_member(db: Session, batch: AnnotationBatch, username: str, role: str,
                side: str | None = None, group_name: str = ""):
    username = (username or "").strip()
    if not username:
        return None
    user = db.scalar(select(User).where(User.username == username))
    if not user:
        raise HTTPException(400, f"用户「{username}」不存在")
    group_name = group_name or (user.group_name or "")
    exists = db.scalar(select(AnnotationMember).where(
        AnnotationMember.batch_id == batch.id, AnnotationMember.username == username,
        AnnotationMember.role == role))
    if exists:
        if side and exists.side != side:
            exists.side = side
        return exists
    m = AnnotationMember(batch_id=batch.id, username=username, role=role,
                         side=side, group_name=group_name)
    db.add(m)
    return m

def create_batch(db: Session, data: dict, actor: dict) -> AnnotationBatch:
    if not can_create_batch(actor):
        raise HTTPException(403, "仅审核员/管理员可创建批次")
    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "批次名称不能为空")
    batch_id = str(data.get("batch_id") or "").strip()
    if not batch_id:
        batch_id = "BATCH-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    if db.scalar(select(AnnotationBatch).where(AnnotationBatch.batch_id == batch_id)):
        raise HTTPException(409, f"批次号「{batch_id}」已存在")
    batch = AnnotationBatch(
        batch_id=batch_id, name=name,
        source_name=str(data.get("source_name") or "").strip(),
        source_version=str(data.get("source_version") or "").strip(),
        source_checksum=str(data.get("source_checksum") or "").strip(),
        source_type=str(data.get("source_type") or "policy").strip(),
        domain=str(data.get("domain") or "通用").strip() or "通用",
        guide_version=str(data.get("guide_version") or "").strip(),
        statistical_unit=str(data.get("statistical_unit") or "标注单元").strip(),
        acceptance_threshold=str(data.get("acceptance_threshold") or "").strip(),
        note=str(data.get("note") or ""), created_by=actor["username"],
    )
    db.add(batch)
    db.flush()
    # 创建人至少是批次负责人；若未指定组长，创建人兼组长
    _add_member(db, batch, actor["username"], "batch_owner")
    lead = str(data.get("lead") or "").strip() or actor["username"]
    _add_member(db, batch, lead, "lead")
    for key, role, side in (("annotator_a", "annotator", "A"),
                            ("annotator_b", "annotator", "B"),
                            ("arbiter", "arbiter", None),
                            ("project_owner", "project_owner", None)):
        _add_member(db, batch, str(data.get(key) or "").strip(), role, side)
    for name in str(data.get("cross_checkers") or "").replace("，", ",").split(","):
        _add_member(db, batch, name.strip(), "cross_checker")
    log_change(db, "annotation_batch", batch.id, "create", None, snapshot(batch), actor["username"])
    db.commit()
    db.refresh(batch)
    return batch

def add_member(db: Session, batch: AnnotationBatch, data: dict, actor: dict):
    require_manage(db, batch, actor)
    username = str(data.get("username") or "").strip()
    role = str(data.get("role") or "").strip()
    side = str(data.get("side") or "").strip() or None
    if role not in MEMBER_ROLE_LABELS:
        raise HTTPException(400, "非法成员角色")
    if role == "annotator" and side not in {"A", "B"}:
        raise HTTPException(400, "标注者必须指定 A/B")
    user = db.scalar(select(User).where(User.username == username))
    if not user:
        raise HTTPException(400, f"用户「{username}」不存在")
    _add_member(db, batch, username, role, side, user.group_name or "")
    db.commit()

def import_units(db: Session, batch: AnnotationBatch, rows: list[dict], actor: dict) -> dict:
    require_manage(db, batch, actor)
    if batch.state != "draft":
        raise HTTPException(400, "只有草稿批次可导入/修改标注单元；请先返工")
    if not rows:
        raise HTTPException(400, "没有可导入的标注单元")
    before_count = db.scalar(select(func.count()).select_from(AnnotationUnit).where(
        AnnotationUnit.batch_id == batch.id)) or 0
    created = updated = 0
    for row in rows:
        unit_id = str(row.get("unit_id") or "").strip()
        if not unit_id:
            raise HTTPException(400, "存在缺 unit_id 的标注单元")
        source_type = str(row.get("source_type") or batch.source_type or "policy")
        source_payload = row.get("source_payload")
        source_locator = row.get("source_locator")
        if source_payload is not None and not isinstance(source_payload, dict):
            raise HTTPException(400, f"单元 {unit_id} 的 source_payload 必须是 JSON 对象")
        if source_locator is not None and not isinstance(source_locator, dict):
            raise HTTPException(400, f"单元 {unit_id} 的 source_locator 必须是 JSON 对象")
        unit = db.scalar(select(AnnotationUnit).where(
            AnnotationUnit.batch_id == batch.id, AnnotationUnit.unit_id == unit_id))
        if unit is None:
            unit = AnnotationUnit(batch_id=batch.id, unit_id=unit_id)
            db.add(unit)
            created += 1
        else:
            updated += 1
        unit.source_id = str(row.get("source_id") or "").strip()
        unit.source_type = source_type
        unit.source_locator = source_locator
        unit.raw_text = str(row.get("raw_text") or "")
        unit.translation_text = str(row.get("translation_text") or "")
        unit.language = str(row.get("language") or "zh")
        unit.source_payload = source_payload
        unit.status = "pending"
    if not batch.source_checksum:
        payload = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()
        batch.source_checksum = hashlib.sha256(payload).hexdigest()
    log_change(db, "annotation_batch", batch.id, "unit_import",
               {"unit_count": before_count},
               {"created": created, "updated": updated}, actor["username"])
    db.commit()
    return {"created": created, "updated": updated, "total": created + updated}

def list_units(db: Session, batch: AnnotationBatch):
    return db.scalars(select(AnnotationUnit).where(AnnotationUnit.batch_id == batch.id)
                      .order_by(AnnotationUnit.unit_id)).all()

def add_evidence(db: Session, unit: AnnotationUnit, data: dict, actor: dict) -> AnnotationEvidence:
    batch = get_batch_or_404(db, unit.batch_id)
    require_view_batch(db, batch, actor)
    if batch.state == "passed":
        raise HTTPException(400, "批次已通过，不能再加 evidence")
    locator = data.get("locator")
    if locator is not None and not isinstance(locator, dict):
        raise HTTPException(400, "evidence locator 必须是 JSON 对象")
    ev = AnnotationEvidence(
        batch_id=batch.id, unit_id=unit.id,
        source_id=str(data.get("source_id") or "").strip(),
        locator=locator, quote=str(data.get("quote") or ""),
        language=str(data.get("language") or "zh"),
        kind=str(data.get("kind") or "text"), created_by=actor["username"],
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev

def list_evidence(db: Session, unit: AnnotationUnit):
    return db.scalars(select(AnnotationEvidence).where(AnnotationEvidence.unit_id == unit.id)
                      .order_by(AnnotationEvidence.id)).all()

def current_co(db: Session, unit_id: int, side: str | None = None):
    stmt = select(CoAnnotation).where(CoAnnotation.unit_id == unit_id, CoAnnotation.is_current.is_(True))
    if side:
        stmt = stmt.where(CoAnnotation.side == side)
    return db.scalars(stmt.order_by(CoAnnotation.side)).all()

def current_alignment(db: Session, unit_id: int):
    return db.scalar(select(CoAlignment).where(
        CoAlignment.unit_id == unit_id, CoAlignment.is_current.is_(True)))

def current_ke(db: Session, unit_id: int, side: str | None = None):
    stmt = select(KeAnnotation).where(KeAnnotation.unit_id == unit_id,
                                      KeAnnotation.is_current.is_(True))
    if side:
        stmt = stmt.where(KeAnnotation.side == side)
    return db.scalars(stmt.order_by(KeAnnotation.side)).all()

def current_gold(db: Session, unit_id: int):
    return db.scalar(select(GoldKe).where(GoldKe.unit_id == unit_id, GoldKe.is_current.is_(True)))

def _lines(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [x.strip() for x in str(raw).splitlines() if x.strip()]

def _evidence_ids(db: Session, unit: AnnotationUnit, raw) -> list[int]:
    ids = []
    for x in raw or []:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if db.scalar(select(AnnotationEvidence.id).where(
                AnnotationEvidence.id == i, AnnotationEvidence.unit_id == unit.id)):
            if i not in ids:
                ids.append(i)
    return ids

def _json_field(raw, field: str):
    if raw is None or str(raw).strip() == "":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        raise HTTPException(400, f"{field} 必须是合法 JSON")

def refresh_unit_status(db: Session, unit: AnnotationUnit):
    co = {c.side for c in current_co(db, unit.id)}
    align = current_alignment(db, unit.id)
    ke = {k.side for k in current_ke(db, unit.id)}
    gold = current_gold(db, unit.id)
    if gold:
        unit.status = "gold_done"
    elif ke == {"A", "B"}:
        unit.status = "ke_both_done"
    elif ke:
        unit.status = "ke_" + next(iter(ke)).lower() + "_done"
    elif align:
        unit.status = "aligned_pending" if align.conclusion == "pending" else "aligned"
    elif co == {"A", "B"}:
        unit.status = "co_both_done"
    elif co:
        unit.status = "co_" + next(iter(co)).lower() + "_done"
    else:
        unit.status = "pending"

def _has_current(db: Session, model, unit_id: int, **filters) -> bool:
    stmt = select(model.id).where(model.unit_id == unit_id, model.is_current.is_(True))
    for k, v in filters.items():
        stmt = stmt.where(getattr(model, k) == v)
    return bool(db.scalar(stmt))

