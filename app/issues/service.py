"""Issue 服务层：编号、校验规则、创建/更新、列表聚合、CSV 导出。"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..audit import diff_entries, entity_history, log_change, snapshot
from ..models import (Concept, GoldItem, Issue, IssueCase, IssueComment,
                      IssueImpact, IssueLink, IssueQuestion, IssueViewpoint,
                      Operator, Question, QuestionKeVersion)

ISSUE_STATUS = ["open", "discussing", "resolved", "escalated", "archived"]
ISSUE_STATUS_LABELS = {
    "open": "开放", "discussing": "讨论中", "resolved": "已结案",
    "escalated": "已上会", "archived": "已归档",
}
ISSUE_TYPE_TAGS = ["模糊表达建模", "指代消解", "口径不一致", "Why/How 建模"]
LEVELS = ["高", "中", "低"]

# 校验1：标题必须是「需要决定的问题」，不能是话题名
TITLE_BAD_PREFIXES = ("关于", "讨论", "记录")


def next_issue_code(db: Session) -> str:
    current_max = db.scalar(select(func.max(Issue.code)))
    n = int(current_max.split("-")[1]) + 1 if current_max else 1
    return f"IS-{n:04d}"


def validate_title(title: str):
    t = (title or "").strip()
    if not t:
        raise HTTPException(400, "标题不能为空")
    if t.startswith(TITLE_BAD_PREFIXES):
        raise HTTPException(
            400, f"标题「{t[:20]}…」是话题名而非待决问题。请改写为问句，如「__X 应如何建模？」")
    return t


def validate_cases(cases: list[dict], *, required: bool = True):
    """校验2：有 Case 时每条必须有原文 + 至少一种写法；新建时至少 1 条。"""
    cleaned = []
    for c in cases or []:
        if not any((c.get(k) or "").strip() for k in ("source", "original_text", "variant_a", "variant_b", "diff_note")):
            continue  # 全空行跳过
        if not (c.get("original_text") or "").strip():
            raise HTTPException(400, "每条 Case 必须有原文")
        if not (c.get("variant_a") or "").strip() and not (c.get("variant_b") or "").strip():
            raise HTTPException(400, "每条 Case 至少填写一种写法")
        cleaned.append(c)
    if required and not cleaned:
        raise HTTPException(400, "至少需要 1 条 Case（原文 + 至少一种写法）")
    return cleaned


def create_issue(db: Session, *, title, type_tag, description, domain="通用", group_name="",
                 importance=None, urgency=None, owner="", cases=(), entity_link=None,
                 annotation_batch_id=None, annotation_unit_id=None, annotation_stage="",
                 annotation_versions=None, annotation_evidence=None, actor) -> Issue:
    title = validate_title(title)
    type_tag = (type_tag or "").strip()
    if not type_tag:
        raise HTTPException(400, "类型标签必填")
    if not (description or "").strip():
        raise HTTPException(400, "问题描述必填（先例子、再矛盾、最后提问）")
    cases = validate_cases(list(cases), required=True)
    if importance not in (None, "", *LEVELS):
        raise HTTPException(400, "重要性取值：高/中/低")
    if urgency not in (None, "", *LEVELS):
        raise HTTPException(400, "紧急程度取值：高/中/低")
    issue = Issue(code=next_issue_code(db), title=title, type_tag=type_tag,
                  description=description.strip(), domain=(domain or "通用").strip() or "通用",
                  group_name=(group_name or "").strip(),
                  importance=importance or None, urgency=urgency or None,
                  owner=(owner or "").strip() or actor, created_by=actor,
                  annotation_batch_id=annotation_batch_id,
                  annotation_unit_id=annotation_unit_id,
                  annotation_stage=(annotation_stage or "").strip() or None,
                  annotation_versions=annotation_versions or None,
                  annotation_evidence=annotation_evidence or None)
    db.add(issue)
    db.flush()
    for seq, c in enumerate(cases, 1):
        db.add(IssueCase(issue_id=issue.id, seq=seq,
                         source=(c.get("source") or "").strip(),
                         original_text=c["original_text"].strip(),
                         variant_a=(c.get("variant_a") or "").strip() or None,
                         variant_b=(c.get("variant_b") or "").strip() or None,
                         diff_note=(c.get("diff_note") or "").strip()))
    if entity_link and entity_link.get("entity_type") and entity_link.get("entity_id"):
        db.add(IssueLink(issue_id=issue.id, entity_type=entity_link["entity_type"],
                         entity_id=int(entity_link["entity_id"]),
                         note=(entity_link.get("note") or "")))
    db.flush()
    log_change(db, "issue", issue.id, "create", None, snapshot(issue), actor)
    db.commit()
    db.refresh(issue)
    return issue


EDITABLE = {"title", "type_tag", "domain", "group_name", "importance", "urgency",
            "description", "owner", "open_items"}


def update_issue(db: Session, issue: Issue, changes: dict, actor: str) -> Issue:
    before = snapshot(issue)
    for k, v in changes.items():
        if k not in EDITABLE:
            continue
        if k == "title":
            v = validate_title(v)
        if k in ("importance", "urgency") and v not in (None, "", *LEVELS):
            raise HTTPException(400, f"{k} 取值：高/中/低")
        setattr(issue, k, v)
    db.flush()
    log_change(db, "issue", issue.id, "update", before, snapshot(issue), actor)
    db.commit()
    db.refresh(issue)
    return issue


# ---------------- 列表 ----------------

def _link_count():
    return (select(func.count(IssueLink.id))
            .where(IssueLink.issue_id == Issue.id)
            .correlate(Issue).scalar_subquery())


def _undone_impact_count():
    return (select(func.count(IssueImpact.id))
            .where(IssueImpact.issue_id == Issue.id, IssueImpact.done == 0)
            .correlate(Issue).scalar_subquery())


def query_issues(db: Session, *, domain=None, status=None, type_tag=None, importance=None,
                 urgency=None, closed=None, q=None, page=1, size=50):
    """排序：未结案在前 → 紧急度 高>中>低 → updated_at 倒序。"""
    link_cnt, undone_cnt = _link_count(), _undone_impact_count()
    stmt = select(Issue, link_cnt.label("link_count"), undone_cnt.label("undone_count"))
    if domain:
        stmt = stmt.where(Issue.domain == domain)
    if status:
        if status not in ISSUE_STATUS:
            raise HTTPException(400, "非法状态")
        stmt = stmt.where(Issue.status == status)
    if type_tag:
        stmt = stmt.where(Issue.type_tag == type_tag)
    if importance:
        stmt = stmt.where(Issue.importance == importance)
    if urgency:
        stmt = stmt.where(Issue.urgency == urgency)
    if closed is True:
        stmt = stmt.where(Issue.status.in_(["resolved", "archived"]))
    elif closed is False:
        stmt = stmt.where(~Issue.status.in_(["resolved", "archived"]))
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(Issue.title.like(like) | Issue.code.like(like) |
                          Issue.description.like(like))
    unresolved_rank = case((Issue.status.in_(["resolved", "archived"]), 1), else_=0)
    urgency_rank = case((Issue.urgency == "高", 0), (Issue.urgency == "中", 1),
                        (Issue.urgency == "低", 2), else_=3)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(unresolved_rank, urgency_rank, Issue.updated_at.desc())
        .offset((page - 1) * size).limit(size)).all()
    return rows, total


def row_to_dict(i: Issue, link_count: int, undone_count: int) -> dict:
    return {
        "id": i.id, "code": i.code, "title": i.title, "type_tag": i.type_tag,
        "domain": i.domain, "group_name": i.group_name, "status": i.status,
        "status_label": ISSUE_STATUS_LABELS.get(i.status, i.status),
        "importance": i.importance, "urgency": i.urgency,
        "link_count": link_count, "undone_count": undone_count,
        "owner": i.owner, "created_by": i.created_by,
        "created_at": i.created_at, "updated_at": i.updated_at,
        "resolved_at": i.resolved_at,
    }


def export_csv(rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["编号", "标题", "类型标签", "领域", "组", "状态", "重要性", "紧急程度",
                "关联对象数", "受影响未完成数", "负责人", "更新时间"])
    for i, lc, uc in rows:
        w.writerow([i.code, i.title, i.type_tag, i.domain, i.group_name,
                    ISSUE_STATUS_LABELS.get(i.status, i.status), i.importance or "",
                    i.urgency or "", lc, uc, i.owner,
                    i.updated_at.strftime("%Y-%m-%d %H:%M") if i.updated_at else ""])
    return "﻿" + buf.getvalue()


# ================= 详情页（第②步） =================

def get_issue_or_404(db: Session, iid: int) -> Issue:
    i = db.get(Issue, iid)
    if not i:
        raise HTTPException(404, "issue 不存在")
    return i


ENTITY_MODEL_MAP = {
    "concept": Concept, "operator": Operator, "question": Question,
    "ke_version": QuestionKeVersion, "gold_item": GoldItem,
}


def entity_ref(db: Session, entity_type: str, entity_id: int) -> dict:
    """关联对象的可读引用（label + 跳转 url）。"""
    model = ENTITY_MODEL_MAP.get(entity_type)
    obj = db.get(model, entity_id) if model else None
    if not obj:
        return {"entity_type": entity_type, "entity_id": entity_id,
                "label": f"{entity_type}#{entity_id}（已删除）", "url": None}
    if entity_type == "concept":
        return {"entity_type": entity_type, "entity_id": entity_id,
                "label": obj.name, "url": f"/concepts/{obj.id}"}
    if entity_type == "operator":
        return {"entity_type": entity_type, "entity_id": entity_id,
                "label": obj.name, "url": f"/operators/{obj.id}"}
    if entity_type == "question":
        return {"entity_type": entity_type, "entity_id": entity_id,
                "label": obj.ext_id, "url": f"/questions/{obj.id}"}
    if entity_type == "ke_version":
        q = db.get(Question, obj.question_id)
        return {"entity_type": entity_type, "entity_id": entity_id,
                "label": f"{q.ext_id if q else '?'} v{obj.version}",
                "url": f"/questions/{obj.question_id}"}
    # gold_item
    return {"entity_type": entity_type, "entity_id": entity_id,
            "label": f"{obj.article_code} / {obj.ke_id}", "url": "/gold/items"}


def _audit(db: Session, issue: Issue, action: str, actor: str, extra: dict | None = None,
           before: dict | None = None):
    after = {"status": issue.status, **(extra or {})}
    log_change(db, "issue", issue.id, action, before, after, actor)


def add_case(db: Session, issue: Issue, data: dict, actor: str) -> IssueCase:
    c = validate_cases([data], required=True)[0]
    seq = (db.scalar(select(func.max(IssueCase.seq)).where(IssueCase.issue_id == issue.id)) or 0) + 1
    row = IssueCase(issue_id=issue.id, seq=seq, source=(data.get("source") or "").strip(),
                    original_text=c["original_text"].strip(),
                    variant_a=(c.get("variant_a") or "").strip() or None,
                    variant_b=(c.get("variant_b") or "").strip() or None,
                    diff_note=(c.get("diff_note") or "").strip())
    db.add(row)
    db.flush()
    _audit(db, issue, "add_case", actor, {"case_seq": seq})
    db.commit()
    db.refresh(row)
    return row


def add_question(db: Session, issue: Issue, text: str, source: str, actor: str) -> IssueQuestion:
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "问题内容不能为空")
    if source not in ("human", "ai"):
        raise HTTPException(400, "来源取值：human / ai")
    seq = (db.scalar(select(func.max(IssueQuestion.seq))
                   .where(IssueQuestion.issue_id == issue.id)) or 0) + 1
    row = IssueQuestion(issue_id=issue.id, seq=seq, text=text, source=source)
    db.add(row)
    db.flush()
    _audit(db, issue, "add_question", actor, {"question_seq": seq})
    db.commit()
    db.refresh(row)
    return row


def add_viewpoint(db: Session, issue: Issue, person: str, stance: str, body: str,
                  actor: str) -> IssueViewpoint:
    if not (person or "").strip() or not (body or "").strip():
        raise HTTPException(400, "观点需填人与正文")
    row = IssueViewpoint(issue_id=issue.id, person=person.strip(),
                         stance=(stance or "补充").strip(), body=body.strip())
    db.add(row)
    db.flush()
    _audit(db, issue, "add_viewpoint", actor, {"person": row.person})
    db.commit()
    db.refresh(row)
    return row


def add_comment(db: Session, issue: Issue, body: str, actor: str) -> IssueComment:
    body = (body or "").strip()
    if not body:
        raise HTTPException(400, "评论不能为空")
    row = IssueComment(issue_id=issue.id, author=actor, body=body)
    db.add(row)
    db.flush()
    _audit(db, issue, "comment", actor, {"comment_id": row.id})
    db.commit()
    db.refresh(row)
    return row


def add_link(db: Session, issue: Issue, entity_type: str, entity_id: int, note: str,
             actor: str) -> IssueLink:
    if entity_type not in ENTITY_MODEL_MAP:
        raise HTTPException(400, f"entity_type 取值：{'/'.join(ENTITY_MODEL_MAP)}")
    if not db.get(ENTITY_MODEL_MAP[entity_type], entity_id):
        raise HTTPException(404, "关联目标不存在")
    dup = db.scalar(select(IssueLink).where(IssueLink.issue_id == issue.id,
                                            IssueLink.entity_type == entity_type,
                                            IssueLink.entity_id == entity_id))
    if dup:
        raise HTTPException(409, "该关联已存在")
    row = IssueLink(issue_id=issue.id, entity_type=entity_type,
                    entity_id=entity_id, note=(note or "").strip())
    db.add(row)
    db.flush()
    _audit(db, issue, "link", actor, {"linked": f"{entity_type}#{entity_id}"})
    db.commit()
    db.refresh(row)
    return row


def set_status(db: Session, issue: Issue, status: str, actor: str) -> Issue:
    if status not in ISSUE_STATUS:
        raise HTTPException(400, "非法状态")
    if status == "resolved":
        raise HTTPException(400, "结案请走 /close（需结论+适用范围+影响清单）")
    old = issue.status
    before = snapshot(issue)
    issue.status = status
    db.flush()
    _audit(db, issue, "status", actor, {"status": (old, status)}, before=before)
    db.commit()
    db.refresh(issue)
    return issue


def close_issue(db: Session, issue: Issue, *, conclusion, scope, open_items="",
                impacts=(), impact_none=False, actor) -> Issue:
    """结案：结论+适用范围必填；影响清单为空须显式声明；同事务生成回填清单。"""
    conclusion = (conclusion or "").strip()
    scope = (scope or "").strip()
    if not conclusion:
        raise HTTPException(400, "结案必须填写结论")
    if not scope:
        raise HTTPException(400, "结案必须填写适用范围")
    impacts = list(impacts or [])
    if not impacts and not impact_none:
        raise HTTPException(400, "受影响条目为空时，必须显式声明「无受影响条目」")
    if issue.status == "resolved":
        raise HTTPException(400, "该 issue 已结案（可重开后再次结案）")
    before = snapshot(issue)
    issue.conclusion = conclusion
    issue.scope = scope
    issue.open_items = (open_items or "").strip() or None
    issue.impact_none = not impacts  # 有清单则以清单为准
    issue.status = "resolved"
    issue.resolved_at = datetime.now()
    for imp in impacts:
        et, eid = imp.get("entity_type"), imp.get("entity_id")
        if et not in ENTITY_MODEL_MAP or not db.get(ENTITY_MODEL_MAP[et], eid or 0):
            raise HTTPException(400, f"影响条目不存在：{et}#{eid}")
        if imp.get("action", "需复核") not in ("需修改", "需复核", "无需改"):
            raise HTTPException(400, "action 取值：需修改 / 需复核 / 无需改")
        db.add(IssueImpact(issue_id=issue.id, entity_type=et, entity_id=int(eid),
                           action=imp.get("action") or "需复核"))
    db.flush()
    _audit(db, issue, "close", actor,
           {"conclusion": conclusion, "scope": scope, "impact_count": len(impacts)},
           before=before)
    db.commit()
    db.refresh(issue)
    return issue


def reopen_issue(db: Session, issue: Issue, actor: str) -> Issue:
    if issue.status != "resolved":
        raise HTTPException(400, "仅已结案的可重新打开")
    before = snapshot(issue)
    issue.status = "discussing"
    issue.resolved_at = None
    db.flush()
    _audit(db, issue, "reopen", actor, {"status": ("resolved", "discussing")}, before=before)
    db.commit()
    db.refresh(issue)
    return issue


def toggle_impact(db: Session, issue: Issue, impact_id: int, actor: str) -> IssueImpact:
    row = db.get(IssueImpact, impact_id)
    if not row or row.issue_id != issue.id:
        raise HTTPException(404, "影响条目不存在")
    row.done = not row.done
    row.done_by = actor if row.done else None
    row.done_at = datetime.now() if row.done else None
    db.flush()
    _audit(db, issue, "impact_toggle", actor,
           {"impact_id": impact_id, "done": row.done})
    db.commit()
    db.refresh(row)
    return row


# ---------------- 详情聚合 ----------------

def detail_payload(db: Session, issue: Issue) -> dict:
    cases = db.scalars(select(IssueCase).where(IssueCase.issue_id == issue.id)
                       .order_by(IssueCase.seq)).all()
    questions = db.scalars(select(IssueQuestion).where(IssueQuestion.issue_id == issue.id)
                           .order_by(IssueQuestion.seq)).all()
    viewpoints = db.scalars(select(IssueViewpoint).where(IssueViewpoint.issue_id == issue.id)
                            .order_by(IssueViewpoint.person, IssueViewpoint.id)).all()
    comments = db.scalars(select(IssueComment).where(IssueComment.issue_id == issue.id)
                          .order_by(IssueComment.id)).all()
    links = db.scalars(select(IssueLink).where(IssueLink.issue_id == issue.id)
                       .order_by(IssueLink.id)).all()
    impacts = db.scalars(select(IssueImpact).where(IssueImpact.issue_id == issue.id)
                         .order_by(IssueImpact.id)).all()
    # 观点按人聚合
    vp_grouped: dict[str, list] = {}
    for v in viewpoints:
        vp_grouped.setdefault(v.person, []).append(
            {"stance": v.stance, "body": v.body, "created_at": v.created_at})
    hist = entity_history(db, "issue", issue.id, limit=10)
    impact_done = sum(1 for i in impacts if i.done)
    return {
        "issue": {
            "id": issue.id, "code": issue.code, "domain": issue.domain,
            "group_name": issue.group_name, "type_tag": issue.type_tag,
            "title": issue.title, "status": issue.status,
            "status_label": ISSUE_STATUS_LABELS.get(issue.status, issue.status),
            "importance": issue.importance, "urgency": issue.urgency,
            "description": issue.description, "conclusion": issue.conclusion,
            "scope": issue.scope, "open_items": issue.open_items,
            "impact_none": issue.impact_none,
            "owner": issue.owner, "created_by": issue.created_by,
            "created_at": issue.created_at, "updated_at": issue.updated_at,
            "resolved_at": issue.resolved_at,
        },
        "cases": [{"id": c.id, "seq": c.seq, "source": c.source,
                   "original_text": c.original_text, "variant_a": c.variant_a,
                   "variant_b": c.variant_b, "diff_note": c.diff_note} for c in cases],
        "questions": [{"id": q.id, "seq": q.seq, "text": q.text, "source": q.source}
                      for q in questions],
        "viewpoints": [{"person": p, "items": items} for p, items in vp_grouped.items()],
        "links": [{**entity_ref(db, l.entity_type, l.entity_id),
                   "link_id": l.id, "note": l.note} for l in links],
        "impacts": [{**entity_ref(db, i.entity_type, i.entity_id),
                     "impact_id": i.id, "action": i.action, "done": i.done,
                     "done_by": i.done_by, "done_at": i.done_at} for i in impacts],
        "impact_progress": {"done": impact_done, "total": len(impacts)},
        "comments": [{"id": c.id, "author": c.author, "body": c.body,
                      "created_at": c.created_at} for c in comments],
        "audit_tail": [{"id": h.id, "action": h.action, "actor": h.actor,
                        "created_at": h.created_at,
                        "diff": diff_entries(h.before, h.after)} for h in hist],
    }


def related_issues(db: Session, entity_type: str, entity_id: int, limit: int = 5) -> list[dict]:
    """某对象的未结案 issue（前 N 条，更新时间倒序）——集成块用。"""
    rows = db.execute(
        select(Issue)
        .join(IssueLink, IssueLink.issue_id == Issue.id)
        .where(IssueLink.entity_type == entity_type, IssueLink.entity_id == entity_id,
               ~Issue.status.in_(["resolved", "archived"]))
        .order_by(Issue.updated_at.desc()).limit(limit)).scalars().all()
    return [{"id": i.id, "code": i.code, "title": i.title, "type_tag": i.type_tag,
             "status": i.status, "status_label": ISSUE_STATUS_LABELS.get(i.status, i.status),
             "updated_at": i.updated_at} for i in rows]
