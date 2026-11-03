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
from ..ke_docs import upsert as upsert_ke_document
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




































































from .service_base import *  # noqa: F401,F403
from .service_base import (_is_admin, _members, roles_for, sides_for, can_view_batch,
                           can_manage, can_project_approve, can_annotate_side, can_align,
                           current_co, current_alignment, current_ke, current_gold,
                           current_alignment, refresh_unit_status, _has_current, _lines,
                           _evidence_ids, _json_field, get_batch_or_404, get_unit_or_404,
                           get_version_or_404, list_units, list_evidence, _add_member,
                           _user_exists)

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
    evidence_ids = _evidence_ids(db, unit, data.get("evidence_ids"))
    if not evidence_ids:
        raise HTTPException(400, "CO 至少需要关联一条 evidence")
    co = CoAnnotation(
        batch_id=batch.id, unit_id=unit.id, side=side, version=latest + 1,
        is_current=True, decision=str(data.get("decision") or "include"),
        concepts=_lines(data.get("concepts")), constants=_lines(data.get("constants")),
        operators=_lines(data.get("operators")), reason=str(data.get("reason") or ""),
        evidence_ids=evidence_ids,
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
    concept_rows = _json_field(data.get("concept_rows"), "concept_rows")
    operator_rows = _json_field(data.get("operator_rows"), "operator_rows")
    if concept_rows:
        aligned["concept_rows"] = concept_rows
    if operator_rows:
        aligned["operator_rows"] = operator_rows
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
    evidence_ids = _evidence_ids(db, unit, data.get("evidence_ids"))
    if not evidence_ids:
        raise HTTPException(400, "KE 至少需要关联一条 evidence")
    ke = KeAnnotation(
        batch_id=batch.id, unit_id=unit.id, side=side, version=latest + 1,
        is_current=True, content=content,
        evidence_ids=evidence_ids,
        execution=_json_field(data.get("execution"), "execution"),
        self_check=_json_field(data.get("self_check"), "self_check"),
        annotator=actor["username"],
    )
    db.add(ke)
    db.flush()
    version = db.scalar(select(OntologyVersion).where(
        OntologyVersion.batch_id == batch.id,
        OntologyVersion.status.in_(["frozen", "released"])).order_by(OntologyVersion.id.desc()))
    doc = upsert_ke_document(
        db, source_type="annotation", source_ref=f"ke_annotation:{ke.id}",
        question=unit.raw_text or "", ke_text=content, status="candidate",
        actor=actor["username"], annotation_batch_id=batch.id,
        ontology_version_id=version.id if version else None,
        reason=f"ke annotation {side} v{ke.version}")
    ke.ke_document_id = doc.id
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
    evidence_ids = _evidence_ids(db, unit, data.get("evidence_ids"))
    if not evidence_ids:
        raise HTTPException(400, "GOLD 至少需要关联一条 evidence")
    gold = GoldKe(
        batch_id=batch.id, unit_id=unit.id, version=latest + 1, is_current=True,
        source=str(data.get("source") or "A"),
        content=str(data.get("content") or ""),
        differences=_lines(data.get("differences")),
        reason=str(data.get("reason") or ""),
        evidence_ids=evidence_ids,
        decided_by=actor["username"],
    )
    db.add(gold)
    db.flush()
    version = db.scalar(select(OntologyVersion).where(
        OntologyVersion.batch_id == batch.id,
        OntologyVersion.status.in_(["frozen", "released"])).order_by(OntologyVersion.id.desc()))
    doc = upsert_ke_document(
        db, source_type="annotation", source_ref=f"gold_ke:{gold.id}",
        question=unit.raw_text or "", ke_text=gold.content, status="reviewed",
        actor=actor["username"], annotation_batch_id=batch.id,
        ontology_version_id=version.id if version else None,
        reason=f"gold ke v{gold.version}")
    gold.ke_document_id = doc.id
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


def _walk_rule_node(node, concept_ids: set[int], operator_ids: set[int],
                    errors: list[str], label: str) -> None:
    """轻量校验 spec_v1 节点形状与 OMS ID 引用；外部/内建引用不误杀。"""
    if not isinstance(node, dict):
        errors.append(f"{label}: 节点必须是 JSON 对象")
        return
    if "connective" in node:
        if node.get("connective") not in {"AND", "OR", "NOT", "IMPLIES", "IFF"}:
            errors.append(f"{label}: 非法连接词 {node.get('connective')!r}")
        if "formula_left" not in node:
            errors.append(f"{label}: Formula 缺 formula_left")
        else:
            _walk_rule_node(node["formula_left"], concept_ids, operator_ids, errors, label)
        if node.get("formula_right") is not None:
            _walk_rule_node(node["formula_right"], concept_ids, operator_ids, errors, label)
        return
    if "lhs" not in node:
        errors.append(f"{label}: 未知节点（缺 lhs/connective）")
        return
    _walk_rule_term(node.get("lhs"), concept_ids, operator_ids, errors, label)
    if node.get("rhs") is not None:
        _walk_rule_term(node["rhs"], concept_ids, operator_ids, errors, label)


def _walk_rule_term(term, concept_ids: set[int], operator_ids: set[int],
                    errors: list[str], label: str) -> None:
    if isinstance(term, str):
        return
    if not isinstance(term, dict):
        errors.append(f"{label}: 项必须是变量/对象")
        return
    if "operator" in term:
        ref = term.get("operator")
        if isinstance(ref, str) and ref.startswith("oms-op-") and ref[7:].isdigit():
            if int(ref[7:]) not in operator_ids:
                errors.append(f"{label}: 算子引用不存在 {ref}")
        args = term.get("arguments")
        if args is not None and not isinstance(args, list):
            errors.append(f"{label}: arguments 必须是数组")
        else:
            for arg in args or []:
                _walk_rule_term(arg, concept_ids, operator_ids, errors, label)
        return
    if "symbol" in term:
        ref = term.get("belong_concepts")
        refs = ref if isinstance(ref, list) else [ref]
        for r in refs:
            if isinstance(r, str) and r.startswith("oms-c-") and r[6:].isdigit():
                if int(r[6:]) not in concept_ids:
                    errors.append(f"{label}: 概念引用不存在 {r}")
        return
    errors.append(f"{label}: 未知项形状")


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
        if o.output_concept_id and not db.get(Concept, o.output_concept_id):
            bad_ops += 1
    bad_inputs = 0
    for inp in db.scalars(select(OperatorInput)).all():
        if inp.concept_id and not db.get(Concept, inp.concept_id):
            bad_inputs += 1
    if bad_ops:
        errors.append(f"{bad_ops} 个算子的主体/返回类型/输出概念不合法")
    if bad_inputs:
        errors.append(f"{bad_inputs} 个算子输入指向不存在概念")
    # 全局个体：所属概念必须存在
    individuals = db.scalars(select(OntologyIndividual)).all()
    bad_ind = sum(1 for i in individuals if not db.get(Concept, i.concept_id))
    if bad_ind:
        errors.append(f"{bad_ind} 个个体的所属概念不存在")
    # 全局规则：节点形状 + OMS 结构引用存在性（外部/内建引用放行）
    rules = db.scalars(select(OntologyRule)).all()
    concept_ids = set(db.scalars(select(Concept.id)).all())
    operator_ids = set(db.scalars(select(Operator.id)).all())
    bad_rules = 0
    for r in rules:
        before = len(errors)
        if r.body is not None:
            _walk_rule_node(r.body, concept_ids, operator_ids, errors, f"规则「{r.name}」body")
        if not isinstance(r.head, list) or not r.head:
            errors.append(f"规则「{r.name}」head 必须是非空数组")
        else:
            for idx, node in enumerate(r.head, 1):
                _walk_rule_node(node, concept_ids, operator_ids, errors,
                                f"规则「{r.name}」head[{idx}]")
        if len(errors) > before:
            bad_rules += 1
    if bad_rules:
        errors.append(f"{bad_rules} 条规则结构或引用不合法")
    return {"ok": not errors, "errors": errors[:20],
            "concepts": len(names), "operators": len(ops),
            "rules": len(rules), "individuals": len(individuals),
            "checked_at": datetime.now().isoformat(timespec="seconds")}


def build_version_payload(db: Session, batch: AnnotationBatch, label: str = "") -> dict:
    """可回放的完整本体快照：概念/算子/规则/个体 + ID 与引用。"""
    concepts = db.scalars(select(Concept).order_by(Concept.id)).all()
    names = {c.id: c.name for c in concepts}
    parent_rows = db.execute(
        select(ConceptParent, Concept)
        .join(Concept, Concept.id == ConceptParent.parent_id)
        .order_by(ConceptParent.concept_id, ConceptParent.is_primary.desc(),
                  ConceptParent.position, Concept.name)
    ).all()
    parents_by: dict[int, list[dict]] = {}
    for link, parent in parent_rows:
        parents_by.setdefault(link.concept_id, []).append(
            {"id": parent.id, "name": parent.name})
    ops = db.scalars(select(Operator).order_by(Operator.id)).all()
    op_inputs = operator_inputs_map(db, [o.id for o in ops])
    rules = db.scalars(select(OntologyRule).order_by(OntologyRule.id)).all()
    individuals = db.scalars(select(OntologyIndividual).order_by(OntologyIndividual.id)).all()
    return {
        "version": 1, "label": label, "batch_id": batch.id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "concepts": [{
            "id": c.id, "name": c.name, "canonical_name": c.name,
            "domain": c.domain or "通用", "aliases": c.aliases or [],
            "definition": c.definition or "", "description": c.description or "",
            "parents": parents_by.get(c.id, []),
            "source_uri": c.source_uri or "", "ontology_module": c.ontology_module or "",
            "status": c.status,
            "created_at": c.created_at.isoformat(timespec="seconds") if c.created_at else None,
        } for c in concepts],
        "operators": [{
            "id": o.id, "name": o.name, "subject_concept_id": o.subject_concept_id,
            "subject_name": names.get(o.subject_concept_id),
            "owner_concept_id": o.owner_concept_id,
            "owner_name": names.get(o.owner_concept_id),
            "param_types": o.param_types or [], "return_type": o.return_type,
            "description": o.description or "",
            "output_concept_id": o.output_concept_id,
            "output_concept": names.get(o.output_concept_id),
            "inputs": op_inputs.get(o.id, []),
            "source_uri": o.source_uri or "", "ontology_module": o.ontology_module or "",
            "status": o.status,
            "created_at": o.created_at.isoformat(timespec="seconds") if o.created_at else None,
        } for o in ops],
        "rules": [{
            "id": r.id, "name": r.name, "canonical_name": r.name,
            "domain": r.domain, "description": r.description or "",
            "body": r.body, "head": r.head or [], "sources": r.sources or [],
            "supply": r.supply or {}, "status": r.status,
            "source_uri": r.source_uri or "", "ontology_module": r.ontology_module or "",
            "created_at": r.created_at.isoformat(timespec="seconds") if r.created_at else None,
        } for r in rules],
        "individuals": [{
            "id": i.id, "name": i.name, "canonical_name": i.name,
            "concept_id": i.concept_id, "concept": names.get(i.concept_id),
            "domain": i.domain, "description": i.description or "",
            "sources": i.sources or [], "supply": i.supply or {}, "status": i.status,
            "source_uri": i.source_uri or "", "ontology_module": i.ontology_module or "",
            "created_at": i.created_at.isoformat(timespec="seconds") if i.created_at else None,
        } for i in individuals],
    }


def create_version(db: Session, batch: AnnotationBatch, data: dict, actor: dict) -> OntologyVersion:
    require_manage(db, batch, actor)
    label = str(data.get("label") or "").strip()
    if not label:
        raise HTTPException(400, "版本号不能为空")
    if db.scalar(select(OntologyVersion).where(OntologyVersion.label == label)):
        raise HTTPException(409, f"版本「{label}」已存在")
    names = sorted(db.scalars(select(Concept.name)).all())
    op_rows = db.scalars(select(Operator).order_by(Operator.name)).all()
    op_inputs = operator_inputs_map(db, [o.id for o in op_rows])
    ops = [(o.name, operator_signature(o, op_inputs.get(o.id, []))) for o in op_rows]
    rules = db.execute(select(OntologyRule.name, OntologyRule.body, OntologyRule.head)
                       .order_by(OntologyRule.name)).all()
    inds = db.execute(select(OntologyIndividual.name, Concept.name)
                      .join(Concept, Concept.id == OntologyIndividual.concept_id)
                      .order_by(OntologyIndividual.name, Concept.name)).all()
    rule_payload = [[n, b, h] for n, b, h in rules]
    snap = {
        "concept_hash": hashlib.sha256("\n".join(names).encode()).hexdigest(),
        "operator_hash": hashlib.sha256("\n".join(f"{n}:{r}" for n, r in ops).encode()).hexdigest(),
        "rule_hash": hashlib.sha256(json.dumps(rule_payload, ensure_ascii=False,
                                               sort_keys=True).encode()).hexdigest(),
        "individual_hash": hashlib.sha256(json.dumps([[n, c] for n, c in inds],
                                                     ensure_ascii=False,
                                                     sort_keys=True).encode()).hexdigest(),
        "concept_count": len(names), "operator_count": len(ops),
        "rule_count": len(rules), "individual_count": len(inds),
    }
    validation = validate_ontology(db)
    version = OntologyVersion(
        batch_id=batch.id, label=label,
        parent_version_id=data.get("parent_version_id") or None,
        import_batch_id=data.get("import_batch_id") or None,
        snapshot=snap, payload=build_version_payload(db, batch, label),
        counts={"concepts": len(names), "operators": len(ops),
                "rules": len(rules), "individuals": len(inds)},
        validation=validation, merge_log=[], created_by=actor["username"],
        note=str(data.get("note") or ""),
    )
    db.add(version)
    db.flush()
    from ..models import KeDocument
    for doc in db.scalars(select(KeDocument).where(
            KeDocument.annotation_batch_id == batch.id)).all():
        doc.ontology_version_id = version.id
    db.flush()
    log_change(db, "ontology_version", version.id, "create", None,
               snapshot(version), actor["username"])
    db.commit()
    db.refresh(version)
    return version


def diff_version_payloads(base: dict, target: dict) -> dict:
    """比较两个版本 payload 的概念/算子/规则/个体增删改。"""
    def clean(x):
        return {k: v for k, v in (x or {}).items() if k not in {"created_at", "updated_at"}}

    def idx(payload, kind):
        if kind == "concepts":
            return {x.get("name"): x for x in payload.get("concepts") or []}
        if kind == "operators":
            return {(x.get("name"), x.get("subject_name")): x
                    for x in payload.get("operators") or []}
        if kind == "rules":
            return {x.get("name") or x.get("canonical_name"): x
                    for x in payload.get("rules") or []}
        return {(x.get("concept"), x.get("name") or x.get("canonical_name")): x
                for x in payload.get("individuals") or []}

    out = {}
    for kind in ("concepts", "operators", "rules", "individuals"):
        a, b = idx(base, kind), idx(target, kind)
        added = sorted([k for k in b if k not in a], key=str)
        removed = sorted([k for k in a if k not in b], key=str)
        changed = sorted([k for k in a if k in b and clean(a[k]) != clean(b[k])], key=str)
        out[kind] = {
            "added": len(added), "removed": len(removed), "changed": len(changed),
            "added_keys": added[:100], "removed_keys": removed[:100],
            "changed_keys": changed[:100],
        }
    return out


def rollback_version(db: Session, target: OntologyVersion, actor: dict) -> OntologyVersion:
    """生成一个指向旧快照的回滚草稿；不覆盖全局、不修改历史版本。"""
    batch = get_batch_or_404(db, target.batch_id)
    require_manage(db, batch, actor)
    if target.status not in {"frozen", "released"}:
        raise HTTPException(400, "只有冻结/已发布版本可作为回滚源")
    if not target.payload:
        raise HTTPException(400, "回滚源没有完整快照")
    label = f"RB-{target.label[:40]}-{datetime.now():%Y%m%d%H%M%S}"
    if db.scalar(select(OntologyVersion).where(OntologyVersion.label == label)):
        raise HTTPException(409, f"版本「{label}」已存在")
    v = OntologyVersion(
        batch_id=target.batch_id, label=label, status="draft",
        parent_version_id=target.id, import_batch_id=target.import_batch_id,
        snapshot=target.snapshot, payload=target.payload,
        counts=target.counts, validation=target.validation, merge_log=[],
        created_by=actor["username"],
        note=f"由版本 {target.label} 生成的回滚草稿（未自动改全局）",
    )
    db.add(v)
    db.flush()
    log_change(db, "ontology_version", v.id, "rollback_draft", None, snapshot(v), actor["username"])
    db.commit()
    db.refresh(v)
    return v


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
    current = validate_ontology(db)
    if not current.get("ok"):
        raise HTTPException(400, "当前本体校验未通过，不能发布版本")
    before = snapshot(version)
    version.status = "released"
    version.released_at = datetime.now()
    log_change(db, "ontology_version", version.id, "release", before,
               snapshot(version), actor["username"])
    db.commit()




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
        pending = [u.unit_id for u in units
                   if current_alignment(db, u.id).conclusion == "pending"]
        if pending:
            raise HTTPException(400, f"还有 {len(pending)} 个单元处于待裁决（pending）")
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
        for u in units:
            if u.source_type not in {"sql", "lean", "sparql"}:
                continue
            for side in ("A", "B"):
                ke = db.scalar(select(KeAnnotation).where(
                    KeAnnotation.unit_id == u.id, KeAnnotation.side == side,
                    KeAnnotation.is_current.is_(True)))
                run = db.scalar(select(QueryRun).where(
                    QueryRun.unit_id == u.id, QueryRun.side == side,
                    QueryRun.status == "passed").order_by(QueryRun.id.desc()))
                if not ((ke and ke.execution) or run):
                    raise HTTPException(400, f"单元 {u.unit_id} 的 {side} 侧缺执行/编译结果")
        batch.state = "gold_merge"
    elif action == "start_cross":
        if batch.state != "gold_merge":
            raise HTTPException(400, "当前不在 GOLD 合并状态")
        missing = [u.unit_id for u in units if not current_gold(db, u.id)]
        if missing:
            raise HTTPException(400, f"还有 {len(missing)} 个单元缺 GOLD")
        cross_members = [m for m in _members(db, batch.id) if m.role == "cross_checker"]
        cross = {m.username for m in cross_members}
        if len(cross) < 2:
            raise HTTPException(400, "交叉检查组至少需要 2 名成员")
        annotators = {m.username for m in _members(db, batch.id) if m.role == "annotator"}
        if cross & annotators:
            raise HTTPException(400, "交叉检查组成员不能同时是 A/B 标注者")
        annotator_groups = {m.group_name for m in _members(db, batch.id)
                            if m.role == "annotator" and m.group_name}
        cross_groups = {m.group_name for m in cross_members if m.group_name}
        if not (cross_groups - annotator_groups):
            raise HTTPException(400, "交叉检查组必须包含来自其他组的成员")
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


def bulk_delete_units(db: Session, batch: AnnotationBatch, unit_ids: list[int],
                      actor: dict) -> dict:
    """草稿批次批量删除单元及依赖（不触碰已冻结批次）。"""
    require_manage(db, batch, actor)
    if batch.state != "draft":
        raise HTTPException(400, "只有草稿批次可批量删除单元")
    units = db.scalars(select(AnnotationUnit).where(
        AnnotationUnit.batch_id == batch.id, AnnotationUnit.id.in_(unit_ids))).all()
    if not units:
        raise HTTPException(400, "没有匹配的单元")
    ids = [u.id for u in units]
    removed = {}
    for model, label, cond in (
        (AnnotationEvidence, "evidence", AnnotationEvidence.unit_id.in_(ids)),
        (CoAnnotation, "co", CoAnnotation.unit_id.in_(ids)),
        (CoAlignment, "align", CoAlignment.unit_id.in_(ids)),
        (KeAnnotation, "ke", KeAnnotation.unit_id.in_(ids)),
        (GoldKe, "gold", GoldKe.unit_id.in_(ids)),
        (QueryRun, "runs", QueryRun.unit_id.in_(ids)),
    ):
        rows = db.scalars(select(model).where(cond)).all()
        for row in rows:
            db.delete(row)
        removed[label] = len(rows)
    for u in units:
        db.delete(u)
    removed["units"] = len(units)
    log_change(db, "annotation_batch", batch.id, "unit_bulk_delete",
               {"unit_ids": ids}, removed, actor["username"])
    db.commit()
    return removed










_OP_RE = re.compile(r"^([^\s(]+)\s*\(([^)]*)\)\s*:\s*(.+)$")


def _op_rows(alignment: CoAlignment) -> list[dict]:
    aligned = alignment.aligned or {}
    rows = aligned.get("operator_rows")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict) and r.get("name") and r.get("subject")]
    out = []
    for line in aligned.get("operators") or []:
        m = _OP_RE.match(str(line).strip())
        if m:
            out.append({"name": m.group(1).strip(), "subject": m.group(2).strip(),
                        "return_type": m.group(3).strip()})
    return out


def _concept_rows(alignment: CoAlignment) -> list[dict]:
    aligned = alignment.aligned or {}
    rows = aligned.get("concept_rows")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict) and r.get("name")]
    return [{"name": n} for n in (aligned.get("concepts") or []) if str(n).strip()]


def apply_alignments(db: Session, batch: AnnotationBatch, actor: dict,
                     label: str | None = None) -> dict:
    """把逐题对齐结果合并进全局本体，并生成一个 CO_M 草稿版本。"""
    require_manage(db, batch, actor)
    if batch.state not in {"co_merge", "rework"}:
        raise HTTPException(400, "只有本体合并阶段可应用对齐结果")
    units = list_units(db, batch)
    aligns = [(u, current_alignment(db, u.id)) for u in units]
    aligns = [(u, a) for u, a in aligns if a]
    if not aligns:
        raise HTTPException(400, "没有可应用的逐题对齐结果")
    pending = [u.unit_id for u, a in aligns if a.conclusion == "pending"]
    if pending:
        raise HTTPException(400, f"还有 {len(pending)} 个单元处于待裁决（pending），不能应用")
    stats = {"concepts_created": 0, "concepts_updated": 0, "operators_created": 0,
             "operators_updated": 0, "skipped": [], "constants": 0, "units": len(aligns)}
    all_names = set(db.scalars(select(Concept.name)).all())
    parent_wanted: list[tuple[str, str]] = []
    # 第一遍：所有概念（跨单元先建齐，保证算子/父类可解析）
    for unit, a in aligns:
        stats["constants"] += len((a.aligned or {}).get("constants") or [])
        for row in _concept_rows(a):
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            existing = db.scalar(select(Concept).where(Concept.name == name))
            definition = str(row.get("definition") or "")
            aliases = [str(x).strip() for x in (row.get("aliases") or []) if str(x).strip()]
            if existing is None:
                existing = Concept(name=name, domain=batch.domain or "通用",
                                   aliases=aliases, definition=definition,
                                   description=definition or f"概念 {name}",
                                   status="approved", created_by=actor["username"],
                                   reviewed_by=actor["username"],
                                   ontology_module=f"annotation:{batch.batch_id}")
                from ..services import assign_local_code
                assign_local_code(db, existing)
                db.add(existing)
                db.flush()
                log_change(db, "concept", existing.id, "create", None, snapshot(existing),
                           actor["username"])
                stats["concepts_created"] += 1
                all_names.add(name)
            else:
                before = snapshot(existing)
                changed = False
                if definition and not (existing.definition or ""):
                    existing.definition = definition
                    changed = True
                if aliases:
                    merged = list(existing.aliases or [])
                    for x in aliases:
                        if x not in merged:
                            merged.append(x)
                    if merged != (existing.aliases or []):
                        existing.aliases = merged
                        changed = True
                if changed:
                    log_change(db, "concept", existing.id, "import", before,
                               snapshot(existing), actor["username"])
                    stats["concepts_updated"] += 1
            parent = str(row.get("parent") or "").strip()
            if parent:
                parent_wanted.append((name, parent))
    db.flush()
    # 第二遍：父类
    for name, parent_name in parent_wanted:
        c = db.scalar(select(Concept).where(Concept.name == name))
        p = db.scalar(select(Concept).where(Concept.name == parent_name))
        if not c or not p:
            stats["skipped"].append(f"父类 {name} <: {parent_name}：概念不存在")
            continue
        current = [x["id"] for x in concept_parents_map(db, [c.id]).get(c.id, [])]
        if p.id in current:
            continue
        try:
            set_concept_parents(db, c, [p.id] + current)
        except HTTPException as e:
            stats["skipped"].append(f"父类 {name} <: {parent_name}：{e.detail}")
    # 第三遍：算子
    for unit, a in aligns:
        for row in _op_rows(a):
            name = str(row.get("name") or "").strip()
            subj_name = str(row.get("subject") or "").strip()
            ret = str(row.get("return_type") or "").strip()
            subj = db.scalar(select(Concept).where(Concept.name == subj_name))
            if not subj or not ret:
                stats["skipped"].append(f"算子 {name}({subj_name})：主体/返回类型缺失")
                continue
            if check_type_expr(ret, all_names):
                stats["skipped"].append(f"算子 {name}({subj_name})：返回类型 {ret} 不合法")
                continue
            op = db.scalar(select(Operator).where(Operator.name == name,
                                                  Operator.subject_concept_id == subj.id))
            if op is None:
                op = Operator(name=name, subject_concept_id=subj.id,
                              owner_concept_id=None, param_types=[],
                              return_type=ret,
                              description=f"算子 {name}：{subj.name} → {ret}",
                              status="approved",
                              source_uri=None, ontology_module=f"annotation:{batch.batch_id}",
                              created_by=actor["username"])
                db.add(op)
                db.flush()
                sync_operator_inputs(db, op, [subj_name])
                db.flush()
                from ..validation import run as validate
                validate(db, "operator", op, stage="annotation_merge",
                         actor=actor["username"])
                log_change(db, "operator", op.id, "create", None, snapshot(op), actor["username"])
                stats["operators_created"] += 1
            elif op.return_type != ret:
                before = snapshot(op)
                op.return_type = ret
                sync_operator_inputs(db, op, op.param_types or [])
                from ..validation import run as validate
                validate(db, "operator", op, stage="annotation_merge",
                         actor=actor["username"])
                log_change(db, "operator", op.id, "import", before, snapshot(op),
                           actor["username"])
                stats["operators_updated"] += 1
        db.add(CoMergeLog(batch_id=batch.id, action="merge",
                          source_units=[unit.unit_id], target="CO_M",
                          reason=f"apply alignment #{a.id}", created_by=actor["username"]))
    db.flush()
    label = (label or "").strip() or f"CO_M-{batch.batch_id}-{uuid.uuid4().hex[:6]}"
    latest = db.scalar(select(OntologyVersion).where(
        OntologyVersion.status == "released").order_by(OntologyVersion.id.desc()))
    version = create_version(db, batch, {
        "label": label, "parent_version_id": latest.id if latest else None,
        "note": "由逐题对齐结果自动应用",
    }, actor)
    version.merge_log = [stats]
    db.commit()
    return {"stats": stats, "version_id": version.id, "label": version.label}




