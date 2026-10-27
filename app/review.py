"""提交与审核：提交单生命周期、TMP 发放、批量处理。"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import concept_snapshot, log_change, snapshot
from .models import Concept, ConceptParent, Submission, TmpMapping
from .services import get_concept_or_404, name_map
from .similarity import find_similar

SUB_STATUS = {"pending", "approved", "rejected", "merged"}


def next_tmp_id(db: Session) -> str:
    # TMP-0001 全局递增；零填充 4 位（超过 9999 需升级为数值列排序）
    current_max = db.scalar(select(func.max(TmpMapping.tmp_id)))
    n = int(current_max.split("-")[1]) + 1 if current_max else 1
    return f"TMP-{n:04d}"


def get_submission_or_404(db: Session, sid: int) -> Submission:
    sub = db.get(Submission, sid)
    if not sub:
        raise HTTPException(404, "提交单不存在")
    return sub


def create_submission(db: Session, *, name, aliases, definition, suggested_parent_id,
                      reason, sample_question, actor, domain: str = "通用",
                      suggested_parent_ids: list[int] | None = None) -> Submission:
    """标注员提交新概念：同事务建 pending 概念 + 提交单（含查重快照）+ TMP。"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    if not (reason or "").strip():
        raise HTTPException(400, "必须填写「为什么现有概念不够」")
    if not (sample_question or "").strip():
        raise HTTPException(400, "必须填写「附题目」")
    parent_ids: list[int] = []
    for pid in ([suggested_parent_id] if suggested_parent_id is not None else []) + list(suggested_parent_ids or []):
        if pid is None:
            continue
        if pid not in parent_ids:
            parent_ids.append(pid)
    if not parent_ids:
        raise HTTPException(400, "必须选择建议父类")
    for pid in parent_ids:
        get_concept_or_404(db, pid)
    primary_parent = parent_ids[0]
    if db.scalar(select(Concept).where(Concept.name == name)):
        raise HTTPException(409, f"概念名「{name}」已被占用")
    similar = find_similar(db, name=name, aliases=aliases, parent_id=primary_parent)
    concept = Concept(
        name=name, domain=(domain or "通用").strip() or "通用",
        aliases=list(aliases or []), definition=definition or "",
        parent_id=primary_parent, status="pending", created_by=actor,
    )
    db.add(concept)
    db.flush()
    for pos, pid in enumerate(parent_ids):
        db.add(ConceptParent(concept_id=concept.id, parent_id=pid,
                             is_primary=(pos == 0), position=pos))
    db.flush()
    log_change(db, "concept", concept.id, "create", None, concept_snapshot(db, concept), actor)
    sub = Submission(
        concept_id=concept.id, reason=reason.strip(), sample_question=sample_question.strip(),
        suggested_parent_id=primary_parent, suggested_parent_ids=parent_ids,
        similar_snapshot=similar, status="pending", submitted_by=actor,
    )
    db.add(sub)
    db.flush()
    db.add(TmpMapping(tmp_id=next_tmp_id(db), submission_id=sub.id, status="open", created_by=actor))
    db.commit()
    db.refresh(sub)
    return sub


def _settle_comment(comment) -> str:
    comment = (comment or "").strip()
    if not comment:
        raise HTTPException(400, "审核意见必填")
    return comment


def _tmp_of(db: Session, submission_id: int) -> TmpMapping | None:
    return db.scalar(select(TmpMapping).where(TmpMapping.submission_id == submission_id))


def _ensure_pending(sub: Submission):
    if sub.status != "pending":
        raise HTTPException(400, f"提交单 #{sub.id} 已处理（{sub.status}）")


def _settle_concept(db: Session, c: Concept, *, status: str, reviewer: str,
                    comment: str, action: str, merged_into_id=None):
    """审核引起的概念状态变化，统一留审计快照。"""
    before = snapshot(c)
    c.status = status
    c.reviewed_by = reviewer
    c.review_comment = comment
    if merged_into_id is not None:
        c.merged_into_id = merged_into_id
    db.flush()
    log_change(db, "concept", c.id, action, before, snapshot(c), reviewer)


def approve(db: Session, sub: Submission, comment, reviewer) -> Submission:
    _ensure_pending(sub)
    comment = _settle_comment(comment)
    c = get_concept_or_404(db, sub.concept_id)
    _settle_concept(db, c, status="approved", reviewer=reviewer, comment=comment, action="status")
    sub.status = "approved"
    sub.reviewed_by = reviewer
    sub.review_comment = comment
    sub.reviewed_at = datetime.now()
    tmp = _tmp_of(db, sub.id)
    if tmp:
        tmp.status = "resolved"
        tmp.concept_id = c.id
        tmp.resolved_at = datetime.now()
    db.commit()
    db.refresh(sub)
    return sub


def reject(db: Session, sub: Submission, comment, reviewer) -> Submission:
    _ensure_pending(sub)
    comment = _settle_comment(comment)
    c = get_concept_or_404(db, sub.concept_id)
    _settle_concept(db, c, status="draft", reviewer=reviewer, comment=comment, action="status")
    sub.status = "rejected"
    sub.reviewed_by = reviewer
    sub.review_comment = comment
    sub.reviewed_at = datetime.now()
    # TMP 保持 open（未决）：M3 回填时会列入未决清单
    db.commit()
    db.refresh(sub)
    return sub


def merge(db: Session, sub: Submission, comment, merge_target_id, reviewer) -> Submission:
    _ensure_pending(sub)
    comment = _settle_comment(comment)
    if merge_target_id is None:
        raise HTTPException(400, "必须选择合并目标")
    if merge_target_id == sub.concept_id:
        raise HTTPException(400, "不能合并到自身")
    target = get_concept_or_404(db, merge_target_id)
    if target.status != "approved":
        raise HTTPException(400, "合并目标必须是已入库概念")
    c = get_concept_or_404(db, sub.concept_id)
    _settle_concept(db, c, status="disabled", reviewer=reviewer, comment=comment,
                    action="merge", merged_into_id=target.id)
    sub.status = "merged"
    sub.merge_target_id = target.id
    sub.reviewed_by = reviewer
    sub.review_comment = comment
    sub.reviewed_at = datetime.now()
    tmp = _tmp_of(db, sub.id)
    if tmp:
        tmp.status = "merged"
        tmp.concept_id = target.id  # TMP 指向合并目标，回填同样生效
        tmp.resolved_at = datetime.now()
    db.commit()
    db.refresh(sub)
    return sub


def batch_action(db: Session, ids, action, comment, reviewer):
    """批量通过/驳回；跳过非待审单，返回 (processed, skipped)。"""
    if action not in ("approve", "reject"):
        raise HTTPException(400, "批量操作仅支持 approve / reject")
    _settle_comment(comment)
    processed, skipped = [], []
    for sid in ids:
        sub = db.get(Submission, sid)
        if not sub or sub.status != "pending":
            skipped.append(sid)
            continue
        if action == "approve":
            approve(db, sub, comment, reviewer)
        else:
            reject(db, sub, comment, reviewer)
        processed.append(sid)
    return processed, skipped


def submission_dict(db: Session, sub: Submission) -> dict:
    c = db.get(Concept, sub.concept_id)
    tmp = _tmp_of(db, sub.id)
    parent_ids = list(sub.suggested_parent_ids or ([sub.suggested_parent_id] if sub.suggested_parent_id else []))
    names = name_map(db, parent_ids + [sub.merge_target_id])
    return {
        "id": sub.id,
        "concept_id": sub.concept_id,
        "name": c.name if c else None,
        "domain": (c.domain or "通用") if c else None,
        "aliases": (c.aliases or []) if c else [],
        "definition": (c.definition or "") if c else "",
        "concept_status": c.status if c else None,
        "suggested_parent_id": sub.suggested_parent_id,
        "suggested_parent_ids": parent_ids,
        "suggested_parent_name": names.get(sub.suggested_parent_id),
        "suggested_parent_names": [names.get(pid) for pid in parent_ids if names.get(pid)],
        "reason": sub.reason,
        "sample_question": sub.sample_question,
        "similar_snapshot": sub.similar_snapshot or [],
        "status": sub.status,
        "submitted_by": sub.submitted_by,
        "reviewed_by": sub.reviewed_by,
        "review_comment": sub.review_comment,
        "merge_target_id": sub.merge_target_id,
        "merge_target_name": names.get(sub.merge_target_id),
        "tmp_id": tmp.tmp_id if tmp else None,
        "tmp_status": tmp.status if tmp else None,
        "tmp_concept_id": tmp.concept_id if tmp else None,
        "created_at": sub.created_at,
        "reviewed_at": sub.reviewed_at,
    }
