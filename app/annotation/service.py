"""SOP 手工标注工作流：批次、标注单元、evidence、CO 双盲、对齐、KE、GOLD、版本、交叉检查。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import log_change, snapshot
from ..models import (AnnotationBatch, AnnotationCrossReview, AnnotationEvidence,
                      AnnotationMember, AnnotationUnit, CoAlignment, CoAnnotation,
                      CoMergeLog, Concept, ConceptParent, GoldKe, KeAnnotation,
                      OntologyVersion, Operator, User)
from ..paster import check_type_expr

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


def get_batch_or_404(db: Session, bid: int) -> AnnotationBatch:
    b = db.get(AnnotationBatch, bid)
    if not b:
        raise HTTPException(404, "标注批次不存在")
    return b


def get_unit_or_404(db: Session, uid: int) -> AnnotationUnit:
    u = db.get(AnnotationUnit, uid)
    if not u:
        raise HTTPException(404, "标注单元不存在")
    return u


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
    if not _user_exists(db, username):
        raise HTTPException(400, f"用户「{username}」不存在")
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


def submit_co(db: Session, unit: AnnotationUnit, side: str, data: dict, actor: dict) -> CoAnnotation:
    batch = get_batch_or_404(db, unit.batch_id)
    require_view_batch(db, batch, actor)
    if batch.state != "co_blind":
        raise HTTPException(400, "当前不在 CO 双盲阶段")
    if side not in {"A", "B"}:
        raise HTTPException(400, "side 必须是 A/B")
    if not can_annotate_side(db, batch, actor, side):
        raise HTTPException(403, "只能提交自己负责的 A/B 侧 CO")
    latest = db.scalar(select(func.max(CoAnnotation.version)).where(
        CoAnnotation.unit_id == unit.id, CoAnnotation.side == side)) or 0
    for old in db.scalars(select(CoAnnotation).where(
            CoAnnotation.unit_id == unit.id, CoAnnotation.side == side,
            CoAnnotation.is_current.is_(True))).all():
        old.is_current = False
    co = CoAnnotation(
        batch_id=batch.id, unit_id=unit.id, side=side, version=latest + 1,
        is_current=True, decision=str(data.get("decision") or "include"),
        concepts=_lines(data.get("concepts")), constants=_lines(data.get("constants")),
        operators=_lines(data.get("operators")), reason=str(data.get("reason") or ""),
        evidence_ids=_evidence_ids(db, unit, data.get("evidence_ids")),
        annotator=actor["username"], guide_version=batch.guide_version,
    )
    db.add(co)
    db.flush()
    log_change(db, "co_annotation", co.id, "create", None, snapshot(co), actor["username"])
    refresh_unit_status(db, unit)
    db.commit()
    db.refresh(co)
    return co


def submit_alignment(db: Session, unit: AnnotationUnit, data: dict, actor: dict) -> CoAlignment:
    batch = get_batch_or_404(db, unit.batch_id)
    require_view_batch(db, batch, actor)
    if batch.state != "co_alignment":
        raise HTTPException(400, "当前不在 CO 对齐阶段")
    if not can_align(db, batch, actor):
        raise HTTPException(403, "无权提交对齐结论")
    co_a = db.scalar(select(CoAnnotation).where(
        CoAnnotation.unit_id == unit.id, CoAnnotation.side == "A",
        CoAnnotation.is_current.is_(True)))
    co_b = db.scalar(select(CoAnnotation).where(
        CoAnnotation.unit_id == unit.id, CoAnnotation.side == "B",
        CoAnnotation.is_current.is_(True)))
    if not co_a or not co_b:
        raise HTTPException(400, "A/B 两侧 CO 尚未齐备，不能对齐")
    latest = db.scalar(select(func.max(CoAlignment.version)).where(
        CoAlignment.unit_id == unit.id)) or 0
    for old in db.scalars(select(CoAlignment).where(
            CoAlignment.unit_id == unit.id, CoAlignment.is_current.is_(True))).all():
        old.is_current = False
    aligned = {
        "concepts": _lines(data.get("concepts")),
        "constants": _lines(data.get("constants")),
        "operators": _lines(data.get("operators")),
        "decision": str(data.get("decision") or "include"),
    }
    differences = _lines(data.get("differences"))
    align = CoAlignment(
        batch_id=batch.id, unit_id=unit.id, version=latest + 1, is_current=True,
        co_a_id=co_a.id, co_b_id=co_b.id,
        conclusion=str(data.get("conclusion") or "aligned"),
        aligned=aligned, differences=differences,
        reason=str(data.get("reason") or ""),
        evidence_ids=_evidence_ids(db, unit, data.get("evidence_ids")),
        decided_by=actor["username"], guide_version=batch.guide_version,
    )
    db.add(align)
    db.flush()
    log_change(db, "co_alignment", align.id, "create", None, snapshot(align), actor["username"])
    refresh_unit_status(db, unit)
    db.commit()
    db.refresh(align)
    return align


def submit_ke(db: Session, unit: AnnotationUnit, side: str, data: dict, actor: dict) -> KeAnnotation:
    batch = get_batch_or_404(db, unit.batch_id)
    require_view_batch(db, batch, actor)
    if batch.state != "ke_blind":
        raise HTTPException(400, "当前不在 KE 双盲阶段")
    if side not in {"A", "B"}:
        raise HTTPException(400, "side 必须是 A/B")
    if not can_annotate_side(db, batch, actor, side):
        raise HTTPException(403, "只能提交自己负责的 A/B 侧 KE")
    align = current_alignment(db, unit.id)
    if not align or align.conclusion == "pending":
        raise HTTPException(400, "该题 CO 尚未对齐或仍待裁决，不能标 KE")
    content = str(data.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "KE 内容不能为空")
    latest = db.scalar(select(func.max(KeAnnotation.version)).where(
        KeAnnotation.unit_id == unit.id, KeAnnotation.side == side)) or 0
    for old in db.scalars(select(KeAnnotation).where(
            KeAnnotation.unit_id == unit.id, KeAnnotation.side == side,
            KeAnnotation.is_current.is_(True))).all():
        old.is_current = False
    ke = KeAnnotation(
        batch_id=batch.id, unit_id=unit.id, side=side, version=latest + 1,
        is_current=True, content=content,
        evidence_ids=_evidence_ids(db, unit, data.get("evidence_ids")),
        execution=_json_field(data.get("execution"), "execution"),
        self_check=_json_field(data.get("self_check"), "self_check"),
        annotator=actor["username"],
    )
    db.add(ke)
    db.flush()
    log_change(db, "ke_annotation", ke.id, "create", None, snapshot(ke), actor["username"])
    refresh_unit_status(db, unit)
    db.commit()
    db.refresh(ke)
    return ke


def submit_gold(db: Session, unit: AnnotationUnit, data: dict, actor: dict) -> GoldKe:
    batch = get_batch_or_404(db, unit.batch_id)
    require_view_batch(db, batch, actor)
    if batch.state != "gold_merge":
        raise HTTPException(400, "当前不在 GOLD 合并阶段")
    if not can_align(db, batch, actor):
        raise HTTPException(403, "无权提交 GOLD 结论")
    ke_a = current_ke(db, unit.id, "A")
    ke_b = current_ke(db, unit.id, "B")
    if not ke_a or not ke_b:
        raise HTTPException(400, "A/B 两侧 KE 尚未齐备")
    latest = db.scalar(select(func.max(GoldKe.version)).where(
        GoldKe.unit_id == unit.id)) or 0
    for old in db.scalars(select(GoldKe).where(
            GoldKe.unit_id == unit.id, GoldKe.is_current.is_(True))).all():
        old.is_current = False
    gold = GoldKe(
        batch_id=batch.id, unit_id=unit.id, version=latest + 1, is_current=True,
        source=str(data.get("source") or "A"),
        content=str(data.get("content") or ""),
        differences=_lines(data.get("differences")),
        reason=str(data.get("reason") or ""),
        evidence_ids=_evidence_ids(db, unit, data.get("evidence_ids")),
        decided_by=actor["username"],
    )
    db.add(gold)
    db.flush()
    log_change(db, "gold_ke", gold.id, "create", None, snapshot(gold), actor["username"])
    refresh_unit_status(db, unit)
    db.commit()
    db.refresh(gold)
    return gold


def add_cross_review(db: Session, batch: AnnotationBatch, data: dict, actor: dict):
    if batch.state not in {"cross_review", "validation", "rework"}:
        raise HTTPException(400, "当前不在交叉检查阶段")
    if not can_cross_review(db, batch, actor):
        raise HTTPException(403, "仅交叉检查/组长可提交复核结论")
    conclusion = str(data.get("conclusion") or "pass")
    if conclusion not in {"pass", "changes", "block"}:
        raise HTTPException(400, "非法复核结论")
    review = AnnotationCrossReview(
        batch_id=batch.id, reviewer=actor["username"],
        scope=str(data.get("scope") or ""), conclusion=conclusion,
        findings=_lines(data.get("findings")),
    )
    db.add(review)
    db.flush()
    log_change(db, "annotation_cross_review", review.id, "create", None,
               snapshot(review), actor["username"])
    db.commit()
    db.refresh(review)
    return review


def add_merge_log(db: Session, batch: AnnotationBatch, data: dict, actor: dict):
    require_manage(db, batch, actor)
    action = str(data.get("action") or "merge")
    if action not in {"merge", "split", "rename", "reparent", "other"}:
        raise HTTPException(400, "非法合并动作")
    log = CoMergeLog(
        batch_id=batch.id, version_id=data.get("version_id") or None,
        action=action, source_units=_lines(data.get("source_units")),
        target=str(data.get("target") or ""), reason=str(data.get("reason") or ""),
        created_by=actor["username"],
    )
    db.add(log)
    db.flush()
    log_change(db, "co_merge_log", log.id, "create", None, snapshot(log), actor["username"])
    db.commit()
    db.refresh(log)
    return log


def validate_ontology(db: Session) -> dict:
    concepts = db.scalars(select(Concept.name)).all()
    names = set(concepts)
    errors: list[str] = []
    # 父类存在 + 环检测
    links = db.execute(select(ConceptParent.concept_id, ConceptParent.parent_id)).all()
    adj: dict[int, list[int]] = {}
    missing = 0
    for child, parent in links:
        if not db.get(Concept, parent):
            missing += 1
        adj.setdefault(child, []).append(parent)
    if missing:
        errors.append(f"{missing} 条父类链接指向不存在概念")
    visiting: set[int] = set()
    visited: set[int] = set()

    def dfs(n: int) -> bool:
        if n in visiting:
            return True
        if n in visited:
            return False
        visiting.add(n)
        for p in adj.get(n, []):
            if dfs(p):
                return True
        visiting.discard(n)
        visited.add(n)
        return False

    cycles = 0
    for n in list(adj):
        if dfs(n):
            cycles += 1
    if cycles:
        errors.append(f"检测到 {cycles} 个继承环")
    # 算子主体存在 + 返回类型
    ops = db.scalars(select(Operator)).all()
    bad_ops = 0
    for o in ops:
        if not db.get(Concept, o.subject_concept_id):
            bad_ops += 1
            continue
        if check_type_expr(o.return_type, names):
            bad_ops += 1
    if bad_ops:
        errors.append(f"{bad_ops} 个算子的主体或返回类型不合法")
    return {"ok": not errors, "errors": errors[:20],
            "concepts": len(names), "operators": len(ops),
            "checked_at": datetime.now().isoformat(timespec="seconds")}


def create_version(db: Session, batch: AnnotationBatch, data: dict, actor: dict) -> OntologyVersion:
    require_manage(db, batch, actor)
    label = str(data.get("label") or "").strip()
    if not label:
        raise HTTPException(400, "版本号不能为空")
    if db.scalar(select(OntologyVersion).where(OntologyVersion.label == label)):
        raise HTTPException(409, f"版本「{label}」已存在")
    names = sorted(db.scalars(select(Concept.name)).all())
    ops = db.execute(select(Operator.name, Operator.return_type).order_by(Operator.name)).all()
    snap = {
        "concept_hash": hashlib.sha256("\n".join(names).encode()).hexdigest(),
        "operator_hash": hashlib.sha256("\n".join(f"{n}:{r}" for n, r in ops).encode()).hexdigest(),
        "concept_count": len(names), "operator_count": len(ops),
    }
    validation = validate_ontology(db)
    version = OntologyVersion(
        batch_id=batch.id, label=label,
        parent_version_id=data.get("parent_version_id") or None,
        import_batch_id=data.get("import_batch_id") or None,
        snapshot=snap, counts={"concepts": len(names), "operators": len(ops)},
        validation=validation, merge_log=[], created_by=actor["username"],
        note=str(data.get("note") or ""),
    )
    db.add(version)
    db.flush()
    log_change(db, "ontology_version", version.id, "create", None,
               snapshot(version), actor["username"])
    db.commit()
    db.refresh(version)
    return version


def freeze_version(db: Session, version: OntologyVersion, actor: dict):
    batch = get_batch_or_404(db, version.batch_id)
    require_manage(db, batch, actor)
    if version.status != "draft":
        raise HTTPException(400, "只有草稿版本可冻结")
    if not (version.validation or {}).get("ok"):
        raise HTTPException(400, "版本校验未通过，不能冻结")
    before = snapshot(version)
    version.status = "frozen"
    version.frozen_at = datetime.now()
    log_change(db, "ontology_version", version.id, "freeze", before,
               snapshot(version), actor["username"])
    db.commit()


def release_version(db: Session, version: OntologyVersion, actor: dict):
    batch = get_batch_or_404(db, version.batch_id)
    if not can_project_approve(db, batch, actor):
        raise HTTPException(403, "仅项目负责人/管理员可发布")
    if version.status != "frozen":
        raise HTTPException(400, "只有已冻结版本可发布")
    before = snapshot(version)
    version.status = "released"
    version.released_at = datetime.now()
    log_change(db, "ontology_version", version.id, "release", before,
               snapshot(version), actor["username"])
    db.commit()


def _has_current(db: Session, model, unit_id: int, **filters) -> bool:
    stmt = select(model.id).where(model.unit_id == unit_id, model.is_current.is_(True))
    for k, v in filters.items():
        stmt = stmt.where(getattr(model, k) == v)
    return bool(db.scalar(stmt))


def advance_state(db: Session, batch: AnnotationBatch, action: str, actor: dict,
                  return_state: str | None = None):
    require_view_batch(db, batch, actor)
    before_state = snapshot(batch)
    if action in {"pass"}:
        if not can_project_approve(db, batch, actor):
            raise HTTPException(403, "仅项目负责人/管理员可通过")
    elif action in {"cross_review", "validate"}:
        if not (can_manage(db, batch, actor) or can_cross_review(db, batch, actor)):
            raise HTTPException(403, "无权执行该状态迁移")
    elif action == "resume":
        if not can_manage(db, batch, actor):
            raise HTTPException(403, "仅组长/批次负责人可恢复")
    else:
        require_manage(db, batch, actor)

    units = list_units(db, batch)
    if action == "freeze":
        if batch.state != "draft":
            raise HTTPException(400, "只有草稿批次可冻结")
        if not units:
            raise HTTPException(400, "批次没有标注单元")
        if not batch.source_name or not batch.guide_version:
            raise HTTPException(400, "冻结前必须填写来源名称与指南版本")
        if not batch.source_checksum:
            raise HTTPException(400, "缺少数据集校验和")
        if not any(m.role == "annotator" and m.side == "A" for m in _members(db, batch.id)):
            raise HTTPException(400, "缺少 A 侧标注者")
        if not any(m.role == "annotator" and m.side == "B" for m in _members(db, batch.id)):
            raise HTTPException(400, "缺少 B 侧标注者")
        for u in units:
            if not u.raw_text and not u.source_payload:
                raise HTTPException(400, f"单元 {u.unit_id} 缺原文/来源快照")
            if u.language.lower().startswith("en") and not u.translation_text:
                raise HTTPException(400, f"英文单元 {u.unit_id} 缺中文翻译")
            if u.source_type in {"sql", "lean", "sparql"} and not u.source_payload:
                raise HTTPException(400, f"单元 {u.unit_id} 缺 schema/环境快照")
        batch.state = "dataset_frozen"
        batch.frozen_at = datetime.now()
    elif action == "start_co":
        if batch.state != "dataset_frozen":
            raise HTTPException(400, "当前不在数据集冻结状态")
        batch.state = "co_blind"
    elif action == "start_alignment":
        if batch.state != "co_blind":
            raise HTTPException(400, "当前不在 CO 双盲状态")
        missing = [u.unit_id for u in units
                   if not (_has_current(db, CoAnnotation, u.id, side="A")
                           and _has_current(db, CoAnnotation, u.id, side="B"))]
        if missing:
            raise HTTPException(400, f"还有 {len(missing)} 个单元缺 A/B CO")
        batch.state = "co_alignment"
    elif action == "start_merge":
        if batch.state != "co_alignment":
            raise HTTPException(400, "当前不在 CO 对齐状态")
        missing = [u.unit_id for u in units if not current_alignment(db, u.id)]
        if missing:
            raise HTTPException(400, f"还有 {len(missing)} 个单元未对齐")
        batch.state = "co_merge"
    elif action == "start_ke":
        if batch.state != "co_merge":
            raise HTTPException(400, "当前不在本体合并状态")
        if not db.scalar(select(OntologyVersion.id).where(
                OntologyVersion.batch_id == batch.id,
                OntologyVersion.status.in_(["frozen", "released"]))):
            raise HTTPException(400, "请先创建并冻结一个本体版本")
        batch.state = "ke_blind"
    elif action == "start_gold":
        if batch.state != "ke_blind":
            raise HTTPException(400, "当前不在 KE 双盲状态")
        missing = []
        for u in units:
            if not (_has_current(db, KeAnnotation, u.id, side="A")
                    and _has_current(db, KeAnnotation, u.id, side="B")):
                missing.append(u.unit_id)
        if missing:
            raise HTTPException(400, f"还有 {len(missing)} 个单元缺 A/B KE")
        if any(u.source_type in {"sql", "lean", "sparql"} and not
               all((k.execution or {}) for k in current_ke(db, u.id)) for u in units):
            raise HTTPException(400, "SQL/LEAN/SPARQL 单元缺执行/编译结果")
        batch.state = "gold_merge"
    elif action == "start_cross":
        if batch.state != "gold_merge":
            raise HTTPException(400, "当前不在 GOLD 合并状态")
        missing = [u.unit_id for u in units if not current_gold(db, u.id)]
        if missing:
            raise HTTPException(400, f"还有 {len(missing)} 个单元缺 GOLD")
        batch.state = "cross_review"
    elif action == "validate":
        if batch.state != "cross_review":
            raise HTTPException(400, "当前不在交叉检查状态")
        latest = db.scalar(select(AnnotationCrossReview).where(
            AnnotationCrossReview.batch_id == batch.id).order_by(AnnotationCrossReview.id.desc()))
        if not latest or latest.conclusion != "pass":
            raise HTTPException(400, "交叉检查尚未给出通过结论")
        batch.state = "validation"
    elif action == "pass":
        if batch.state != "validation":
            raise HTTPException(400, "当前不在待校验状态")
        if not db.scalar(select(OntologyVersion.id).where(
                OntologyVersion.batch_id == batch.id,
                OntologyVersion.status == "released")):
            raise HTTPException(400, "请先发布一个本体版本")
        batch.state = "passed"
        batch.finished_at = datetime.now()
    elif action == "rework":
        if batch.state == "passed":
            raise HTTPException(400, "已通过批次不能直接返工")
        batch.state = "rework"
        batch.note = (batch.note + f"\n[rework] {datetime.now():%Y-%m-%d %H:%M} by {actor['username']}").strip()
    elif action == "resume":
        if batch.state != "rework":
            raise HTTPException(400, "当前不在返工状态")
        if return_state not in STATE_ORDER or return_state in {"draft", "passed"}:
            raise HTTPException(400, "非法恢复状态")
        batch.state = return_state
    else:
        raise HTTPException(400, "未知状态动作")
    log_change(db, "annotation_batch", batch.id, "state", before_state,
               snapshot(batch), actor["username"])
    db.commit()


def batch_stats(db: Session, batch: AnnotationBatch) -> dict:
    units = list_units(db, batch)
    return {
        "units": len(units),
        "co_a": sum(1 for u in units if _has_current(db, CoAnnotation, u.id, side="A")),
        "co_b": sum(1 for u in units if _has_current(db, CoAnnotation, u.id, side="B")),
        "aligned": sum(1 for u in units if current_alignment(db, u.id)),
        "ke_a": sum(1 for u in units if _has_current(db, KeAnnotation, u.id, side="A")),
        "ke_b": sum(1 for u in units if _has_current(db, KeAnnotation, u.id, side="B")),
        "gold": sum(1 for u in units if current_gold(db, u.id)),
    }


def batch_summary(db: Session, batch: AnnotationBatch) -> dict:
    members = _members(db, batch.id)
    return {
        "batch": batch,
        "stats": batch_stats(db, batch),
        "annotators": {m.side: m.username for m in members if m.role == "annotator" and m.side},
        "lead": next((m.username for m in members if m.role == "lead"), ""),
        "members": members,
    }


def unit_rows(db: Session, batch: AnnotationBatch) -> list[dict]:
    rows = []
    for u in list_units(db, batch):
        rows.append({
            "unit": u,
            "co_a": _has_current(db, CoAnnotation, u.id, side="A"),
            "co_b": _has_current(db, CoAnnotation, u.id, side="B"),
            "aligned": bool(current_alignment(db, u.id)),
            "ke_a": _has_current(db, KeAnnotation, u.id, side="A"),
            "ke_b": _has_current(db, KeAnnotation, u.id, side="B"),
            "gold": bool(current_gold(db, u.id)),
        })
    return rows


def unit_context(db: Session, unit: AnnotationUnit, user: dict) -> dict:
    batch = get_batch_or_404(db, unit.batch_id)
    roles = roles_for(db, batch, user)
    sides = sides_for(db, batch, user)
    can_view = can_view_unit_data(db, batch, user)
    co = {c.side: c for c in current_co(db, unit.id)} if can_view else {}
    ke = {k.side: k for k in current_ke(db, unit.id)} if can_view else {}
    state = batch.state
    privileged = _is_admin(user) or bool(roles & {"lead", "arbiter"})
    # CO 盲评隔离
    if can_view and state == "co_blind" and not privileged:
        co = {s: c for s, c in co.items() if s in sides}
    # KE 盲评隔离
    if can_view and state == "ke_blind" and not privileged:
        ke = {s: k for s, k in ke.items() if s in sides}
    align = current_alignment(db, unit.id) if can_view and state not in {
        "draft", "dataset_frozen", "co_blind"} else None
    gold = current_gold(db, unit.id) if can_view and state in {
        "gold_merge", "cross_review", "validation", "passed", "rework"} else None
    my_side = next(iter(sides), None)
    can_submit_ke_now = (
        state == "ke_blind" and my_side and can_annotate_side(db, batch, user, my_side)
        and align is not None and align.conclusion != "pending")
    return {
        "batch": batch, "unit": unit, "evidence": list_evidence(db, unit),
        "co_a": co.get("A"), "co_b": co.get("B"),
        "alignment": align, "ke_a": ke.get("A"), "ke_b": ke.get("B"), "gold": gold,
        "roles": roles, "sides": sides, "my_side": my_side,
        "can_view_data": can_view,
        "can_submit_co": (state == "co_blind" and my_side is not None
                          and can_annotate_side(db, batch, user, my_side)),
        "can_align": state == "co_alignment" and can_align(db, batch, user),
        "can_submit_ke": bool(can_submit_ke_now),
        "can_submit_gold": state == "gold_merge" and can_align(db, batch, user),
        "can_add_evidence": can_view and state != "passed",
        "state": state, "state_label": STATE_LABELS.get(state, state),
    }
