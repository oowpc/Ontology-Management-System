"""题目级视图服务层：回填、列表查询（筛选+排序+聚合计数）、CSV 导出。"""
from __future__ import annotations

import csv
import difflib
import io
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import diff_entries, entity_history, log_change
from ..models import (Concept, GoldItem, KeReview, KeTask, Operator,
                      Question, QuestionConcept, QuestionIssue,
                      QuestionKeVersion)

QUESTION_STATUS = ["not_started", "drafting", "pending_review", "approved", "rejected"]
STATUS_LABELS = {
    "not_started": "未开始",
    "drafting": "标注中",
    "pending_review": "待复核",
    "approved": "已通过",
    "rejected": "已退回",
}
# ke_tasks 状态 → questions 状态
KE_TO_QUESTION_STATUS = {
    "待标注": "not_started",
    "标注中": "drafting",
    "待复核": "pending_review",
    "已完成": "approved",
    "已退回": "rejected",
}


# ---------------- 回填 ----------------

def _latest_reviewers(db: Session) -> dict[tuple[str, str], str]:
    """ke_reviews 每组每题最新一条的 reviewer。"""
    rows = db.scalars(select(KeReview).order_by(KeReview.id)).all()
    latest: dict[tuple[str, str], str] = {}
    for r in rows:  # id 升序，后者覆盖前者
        latest[(r.group_name, r.ke_id)] = r.reviewer
    return latest


def backfill_from_ke_tasks(db: Session, *, link_concepts: bool = True) -> dict:
    """从 ke_tasks 生成/更新 questions（ext_id 幂等，可重复运行）。

    ext_id：ke_id 全库唯一 → 直接用；跨组重号 → f"{group}/{ke_id}"（确定性，重复运行命中既有行）。
    """
    tasks = db.scalars(select(KeTask)).all()
    id_count: dict[str, int] = {}
    for t in tasks:
        id_count[t.ke_id] = id_count.get(t.ke_id, 0) + 1
    reviewers = _latest_reviewers(db)
    existing = {q.ext_id: q for q in db.scalars(select(Question)).all()}
    stats = {"tasks": len(tasks), "created": 0, "updated": 0,
             "collisions": 0, "linked": 0, "total": 0}

    for t in tasks:
        ext = t.ke_id if id_count[t.ke_id] == 1 else f"{t.group_name}/{t.ke_id}"
        if id_count[t.ke_id] > 1:
            stats["collisions"] += 1
        fields = {
            "domain": t.domain or "未分类",
            "group_name": t.group_name,
            "text": t.summary or "",
            "status": KE_TO_QUESTION_STATUS.get(t.status, "not_started"),
            "assignee": "" if t.person in (None, "", "未分配") else t.person,
            "reviewer": reviewers.get((t.group_name, t.ke_id)),
        }
        q = existing.get(ext)
        if q is None:
            q = Question(ext_id=ext, **fields)
            db.add(q)
            db.flush()
            existing[ext] = q
            stats["created"] += 1
        else:
            changed = False
            for k, v in fields.items():
                if getattr(q, k) != v:
                    setattr(q, k, v)
                    changed = True
            if changed:
                stats["updated"] += 1

    if link_concepts:
        stats["linked"] = _auto_link_concepts(db, existing)
    db.commit()
    stats["total"] = len(existing)
    return stats


def _auto_link_concepts(db: Session, questions_by_ext: dict[str, Question]) -> int:
    """用 gold_items.concepts（引用名列表）匹配 concept/operator 名，自动挂关联（去重）。"""
    concept_ids = {c.name: c.id for c in db.scalars(select(Concept))}
    operator_ids = {o.name: o.id for o in db.scalars(select(Operator))}
    existing_links = {(l.question_id, l.concept_id, l.operator_id)
                      for l in db.scalars(select(QuestionConcept))}
    linked = 0
    for item in db.scalars(select(GoldItem)).all():
        # gold item 的 ke_id 对回到题目 ext_id（兼容消歧命名）
        candidates = [item.ke_id, f"{item.group_name}/{item.ke_id}"]
        q = next((questions_by_ext[c] for c in candidates if c in questions_by_ext), None)
        if not q:
            continue
        for name in (item.concepts or []):
            cid, oid = concept_ids.get(name), operator_ids.get(name)
            if cid and (q.id, cid, None) not in existing_links:
                db.add(QuestionConcept(question_id=q.id, concept_id=cid, note="回填自动关联"))
                existing_links.add((q.id, cid, None))
                linked += 1
            elif oid and (q.id, None, oid) not in existing_links:
                db.add(QuestionConcept(question_id=q.id, operator_id=oid, note="回填自动关联"))
                existing_links.add((q.id, None, oid))
                linked += 1
    return linked


# ---------------- 列表查询 ----------------

def _open_issue_count():
    return (select(func.count(QuestionIssue.id))
            .where(QuestionIssue.question_id == Question.id,
                   QuestionIssue.status == "open")
            .correlate(Question).scalar_subquery())


def _ke_version_count():
    return (select(func.count(QuestionKeVersion.id))
            .where(QuestionKeVersion.question_id == Question.id)
            .correlate(Question).scalar_subquery())


def _co_count():
    return (select(func.count(QuestionConcept.id))
            .where(QuestionConcept.question_id == Question.id)
            .correlate(Question).scalar_subquery())


def query_questions(db: Session, *, domain=None, group_name=None, status=None,
                    has_open_issue=None, q=None, page=1, size=50):
    """筛选 + 默认排序（未解决 issue 数降序 → updated_at 降序）+ 聚合计数。"""
    open_cnt, ke_cnt, co_cnt = _open_issue_count(), _ke_version_count(), _co_count()
    stmt = select(Question, open_cnt.label("open_issues"),
                  ke_cnt.label("ke_versions"), co_cnt.label("co_count"))
    if domain:
        stmt = stmt.where(Question.domain == domain)
    if group_name:
        stmt = stmt.where(Question.group_name == group_name)
    if status:
        if status not in QUESTION_STATUS:
            from fastapi import HTTPException
            raise HTTPException(400, "非法状态")
        stmt = stmt.where(Question.status == status)
    if has_open_issue:
        stmt = stmt.where(open_cnt > 0)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(Question.ext_id.like(like) | Question.text.like(like))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(open_cnt.desc(), Question.updated_at.desc())
        .offset((page - 1) * size).limit(size)
    ).all()
    return rows, total


def row_to_dict(question: Question, open_issues: int, ke_versions: int, co_count: int) -> dict:
    return {
        "id": question.id,
        "ext_id": question.ext_id,
        "domain": question.domain,
        "group_name": question.group_name,
        "text": question.text,
        "summary60": (question.text or "")[:60],
        "difficulty": question.difficulty,
        "status": question.status,
        "status_label": STATUS_LABELS.get(question.status, question.status),
        "assignee": question.assignee,
        "reviewer": question.reviewer,
        "open_issues": open_issues,
        "ke_versions": ke_versions,
        "co_count": co_count,
        "updated_at": question.updated_at,
    }


def export_csv(rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["题号", "领域", "组", "原文摘要", "CO数", "KE版本数", "未解决issue",
                "状态", "负责人", "复核人", "更新时间"])
    for q, open_issues, ke_versions, co_count in rows:
        w.writerow([q.ext_id, q.domain, q.group_name, (q.text or "")[:60],
                    co_count, ke_versions, open_issues,
                    STATUS_LABELS.get(q.status, q.status), q.assignee, q.reviewer or "",
                    q.updated_at.strftime("%Y-%m-%d %H:%M") if q.updated_at else ""])
    return "﻿" + buf.getvalue()


# ================= 详情页（第②步） =================

REVIEW_STATUSES = {"approved", "rejected"}  # 仅 reviewer/admin 可流转至此


def get_question_or_404(db: Session, qid: int) -> Question:
    q = db.get(Question, qid)
    if not q:
        raise HTTPException(404, "题目不存在")
    return q


def _q_audit(db: Session, q: Question, action: str, actor: str, **fields):
    """统一留痕：before/after 至少含 status 与本次关键字段。"""
    before = {"status": q.status, **{k: v[0] for k, v in fields.items() if isinstance(v, tuple)}}
    after = {"status": q.status, **{k: v[1] for k, v in fields.items() if isinstance(v, tuple)}}
    after.update({k: v for k, v in fields.items() if not isinstance(v, tuple)})
    log_change(db, "question", q.id, action, before, after, actor)


def change_status(db: Session, q: Question, new_status: str, actor: str, role: str) -> Question:
    if new_status not in QUESTION_STATUS:
        raise HTTPException(400, "非法状态")
    if new_status in REVIEW_STATUSES and role not in ("reviewer", "admin"):
        raise HTTPException(403, "通过 / 退回需审核员及以上")
    old = q.status
    q.status = new_status
    db.flush()
    _q_audit(db, q, "status", actor, status=(old, new_status))
    db.commit()
    db.refresh(q)
    return q


def add_link(db: Session, q: Question, *, concept_id=None, operator_id=None, note="", actor) -> QuestionConcept:
    if bool(concept_id) == bool(operator_id):
        raise HTTPException(400, "概念 / 算子必须且只能选一个")
    if concept_id is not None:
        target = db.get(Concept, concept_id)
        kind, name = "concept", target.name if target else None
    else:
        target = db.get(Operator, operator_id)
        kind, name = "operator", target.name if target else None
    if not target:
        raise HTTPException(404, "关联目标不存在")
    dup = db.scalar(select(QuestionConcept).where(
        QuestionConcept.question_id == q.id,
        QuestionConcept.concept_id == concept_id,
        QuestionConcept.operator_id == operator_id))
    if dup:
        raise HTTPException(409, "该关联已存在")
    link = QuestionConcept(question_id=q.id, concept_id=concept_id,
                           operator_id=operator_id, note=(note or "").strip())
    db.add(link)
    db.flush()
    _q_audit(db, q, "add_concept", actor, added=f"{kind}:{name}")
    db.commit()
    db.refresh(link)
    return link


def add_ke_version(db: Session, q: Question, content: str, actor: str) -> QuestionKeVersion:
    content = (content or "").rstrip()
    if not content:
        raise HTTPException(400, "KE 内容不能为空")
    next_v = (db.scalar(select(func.max(QuestionKeVersion.version))
                        .where(QuestionKeVersion.question_id == q.id)) or 0) + 1
    db.execute(
        QuestionKeVersion.__table__.update()
        .where(QuestionKeVersion.question_id == q.id)
        .values(is_current=False))
    v = QuestionKeVersion(question_id=q.id, content=content, version=next_v,
                          author=actor, is_current=True)
    db.add(v)
    db.flush()
    _q_audit(db, q, "add_ke_version", actor, version=next_v, content_length=len(content))
    db.commit()
    db.refresh(v)
    return v


def create_issue(db: Session, q: Question, *, title, body="", tags=(), actor) -> QuestionIssue:
    title = (title or "").strip()
    if not title:
        raise HTTPException(400, "issue 标题不能为空")
    i = QuestionIssue(question_id=q.id, title=title, body=body or "",
                      tags=list(tags or []), author=actor)
    db.add(i)
    db.flush()
    _q_audit(db, q, "create_issue", actor, issue_id=i.id, title=title)
    db.commit()
    db.refresh(i)
    return i


def resolve_issue(db: Session, q: Question, iid: int, actor: str) -> QuestionIssue:
    i = db.get(QuestionIssue, iid)
    if not i or i.question_id != q.id:
        raise HTTPException(404, "issue 不存在")
    if i.status == "resolved":
        raise HTTPException(400, "该 issue 已解决")
    old = i.status
    i.status = "resolved"
    i.resolved_at = datetime.now()
    db.flush()
    _q_audit(db, q, "resolve_issue", actor, issue_id=iid, issue_status=(old, "resolved"))
    db.commit()
    db.refresh(i)
    return i


# ---------------- 详情聚合 ----------------

def issue_dict(i: QuestionIssue) -> dict:
    days = (datetime.now() - i.created_at).days if i.created_at else 0
    return {
        "id": i.id, "title": i.title, "body": i.body, "status": i.status,
        "tags": i.tags or [], "author": i.author, "days_open": days,
        "stale": i.status == "open" and days > 3,
        "created_at": i.created_at, "resolved_at": i.resolved_at,
    }


def version_dict(v: QuestionKeVersion) -> dict:
    return {"id": v.id, "version": v.version, "author": v.author,
            "is_current": v.is_current, "created_at": v.created_at,
            "content": v.content}


def detail_payload(db: Session, q: Question) -> dict:
    """详情五块 + 审计尾（页面与 API 共用）。"""
    links = db.scalars(select(QuestionConcept)
                       .where(QuestionConcept.question_id == q.id)
                       .order_by(QuestionConcept.id)).all()
    cids = [l.concept_id for l in links if l.concept_id]
    oids = [l.operator_id for l in links if l.operator_id]
    concepts = {c.id: c for c in db.scalars(select(Concept).where(Concept.id.in_(cids)))} if cids else {}
    operators = {o.id: o for o in db.scalars(select(Operator).where(Operator.id.in_(oids)))} if oids else {}
    parent_names = {c.id: c.name for c in concepts.values()}
    parent_names.update({c.id: c.name for c in db.scalars(
        select(Concept).where(Concept.id.in_([c.parent_id for c in concepts.values() if c.parent_id])))})
    subject_names = {c.id: c.name for c in db.scalars(
        select(Concept).where(Concept.id.in_([o.subject_concept_id for o in operators.values()])))}
    usage = dict(db.execute(
        select(QuestionConcept.concept_id, func.count())
        .where(QuestionConcept.concept_id.is_not(None))
        .group_by(QuestionConcept.concept_id)).all())
    co_items = []
    for l in links:
        if l.concept_id and l.concept_id in concepts:
            c = concepts[l.concept_id]
            co_items.append({
                "kind": "concept", "link_id": l.id, "id": c.id, "name": c.name,
                "parent_name": parent_names.get(c.parent_id), "status": c.status,
                "used_by": usage.get(c.id, 0), "note": l.note,
            })
        elif l.operator_id and l.operator_id in operators:
            o = operators[l.operator_id]
            co_items.append({
                "kind": "operator", "link_id": l.id, "id": o.id,
                "name": o.name, "signature": f"{o.name}({', '.join(o.param_types or [])}) : {o.return_type}",
                "subject_name": subject_names.get(o.subject_concept_id), "note": l.note,
            })
    versions = db.scalars(select(QuestionKeVersion)
                          .where(QuestionKeVersion.question_id == q.id)
                          .order_by(QuestionKeVersion.version.desc())).all()
    issues = db.scalars(select(QuestionIssue)
                        .where(QuestionIssue.question_id == q.id)).all()
    issues_sorted = sorted(issues, key=lambda i: ({"open": 0, "escalated": 1, "resolved": 2}[i.status],
                                                  -i.id))
    hist = entity_history(db, "question", q.id, limit=10)
    return {
        "question": {
            "id": q.id, "ext_id": q.ext_id, "domain": q.domain,
            "group_name": q.group_name, "text": q.text, "difficulty": q.difficulty,
            "status": q.status, "status_label": STATUS_LABELS.get(q.status, q.status),
            "assignee": q.assignee, "reviewer": q.reviewer,
            "created_at": q.created_at, "updated_at": q.updated_at,
        },
        "concepts": [x for x in co_items if x["kind"] == "concept"],
        "operators": [x for x in co_items if x["kind"] == "operator"],
        "versions": [version_dict(v) for v in versions],
        "current_version": next((version_dict(v) for v in versions if v.is_current), None),
        "issues": [issue_dict(i) for i in issues_sorted],
        "audit_tail": [
            {"id": h.id, "action": h.action, "actor": h.actor,
             "created_at": h.created_at, "diff": diff_entries(h.before, h.after)}
            for h in hist
        ],
    }


def diff_lines(old: str, new: str) -> list[dict]:
    """difflib 逐行 diff：ctx / add / del 三态（前端只着色）。"""
    out = []
    for line in difflib.unified_diff(
            (old or "").splitlines(), (new or "").splitlines(), lineterm="", n=3):
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("+"):
            out.append({"kind": "add", "text": line[1:]})
        elif line.startswith("-"):
            out.append({"kind": "del", "text": line[1:]})
        else:
            out.append({"kind": "ctx", "text": line[1:] if line.startswith(" ") else line})
    return out


def versions_for_diff(db: Session, q: Question, v1: int | None, v2: int | None):
    """任选两版对比；缺省「上一版 vs 当前版」。"""
    versions = db.scalars(select(QuestionKeVersion)
                          .where(QuestionKeVersion.question_id == q.id)
                          .order_by(QuestionKeVersion.version)).all()
    if len(versions) < 2:
        return None
    by_v = {v.version: v for v in versions}
    if v1 is None or v2 is None:
        v2 = max(by_v)
        v1 = sorted(by_v)[-2]
    if v1 not in by_v or v2 not in by_v:
        raise HTTPException(404, "指定版本不存在")
    a, b = by_v[min(v1, v2)], by_v[max(v1, v2)]
    return {"v1": version_dict(a), "v2": version_dict(b),
            "lines": diff_lines(a.content, b.content)}

