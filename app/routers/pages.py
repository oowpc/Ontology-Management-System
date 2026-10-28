"""服务端渲染页面：概念 / 算子 / 提交审核 / TMP / 历史 / 体检 / 导入导出。"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import diff_entries, entity_history
from ..audit import rollback as audit_rollback
from ..backfill import backfill, tmp_mapping_dict
from ..db import get_db
from ..healthcheck import KIND_LABELS, run_health
from ..importer import csv_to_payload, import_payload, parse_field_map
from ..llm import enabled as llm_enabled
from ..models import (AuditLog, BackfillRecord, Concept, ConceptParent,
                      GoldItem, GoldSample, HealthReport, Issue, KeolEntity,
                      KeTask, KeUpload, Operator, Question, QuestionConcept,
                      Submission, TmpMapping, User)
from ..review import (SUB_STATUS, approve, batch_action, create_submission,
                      get_submission_or_404, merge, reject, submission_dict)
from ..security import current_user
from ..services import (concept_parents_map, create_concept, create_operator,
                        get_concept_or_404, get_operator_or_404, name_map,
                        query_concepts, set_concept_status, set_operator_status,
                        update_concept, update_operator, usage_map)
from ..similarity import SimilarityBlock
from ..templating import flash, templates

router = APIRouter(tags=["pages"])

EDIT_ROLES = ("reviewer", "admin")
PAGE_SIZE = 20


def _can_edit(user: dict) -> bool:
    return user["role"] in EDIT_ROLES


def _forbid(user: dict):
    if not _can_edit(user):
        raise HTTPException(403, "权限不足")


def _split_multi(raw: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,，、;；\n]+", raw or "") if x.strip()]


def _int_or_none(raw) -> int | None:
    s = str(raw or "").strip()
    return int(s) if s.isdigit() else None


def _parent_options(db: Session, exclude_id: int | None = None):
    stmt = select(Concept).order_by(Concept.id)
    if exclude_id is not None:
        stmt = stmt.where(Concept.id != exclude_id)
    return db.scalars(stmt).all()


# ================= 工作台（落地页） =================

@router.get("/")
def workbench(request: Request, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    """角色落地页：今天该干什么一屏看清。
    标注员=我的组今日上传 + 我的题目；审核员=待办队列（提交/复核/定稿/抽检）；admin=系统概况。"""
    from datetime import date

    from ..ke import service as ke_service
    today = date.today()
    role = user["role"]
    banner = ke_service.group_status(db, today)
    my_questions = db.scalars(
        select(Question).where(Question.assignee == user["username"],
                               Question.status.notin_(["approved"]))
        .order_by(Question.updated_at.desc()).limit(8)).all()
    ctx = {
        "request": request, "user": user, "today": today,
        "banner": banner,
        "my_group_status": next((g for g in banner if g["name"] == user.get("group")), None),
        "recent_uploads": ke_service.upload_history(db, limit=5),
        "my_questions": my_questions,
        "my_questions_total": db.scalar(
            select(func.count(Question.id)).where(Question.assignee == user["username"],
                                                  Question.status.notin_(["approved"]))) or 0,
        "open_issues": db.scalar(select(func.count(Issue.id))
                                 .where(Issue.status.in_(["open", "discussing"]))) or 0,
        "tmp_open": db.scalar(select(func.count(TmpMapping.id))
                              .where(TmpMapping.status == "open")) or 0,
    }
    if role in EDIT_ROLES:
        ctx["todo"] = {
            "pending_subs": db.scalar(select(func.count(Submission.id))
                                      .where(Submission.status == "pending")) or 0,
            "ke_pending": db.scalar(select(func.count(KeTask.id))
                                    .where(KeTask.status == "待复核")) or 0,
            "gold_draft": db.scalar(select(func.count(GoldItem.id))
                                    .where(GoldItem.status == "draft")) or 0,
            "gold_reviewed": db.scalar(select(func.count(GoldItem.id))
                                       .where(GoldItem.status == "reviewed")) or 0,
            "samples_open": db.scalar(select(func.count(GoldSample.id))
                                      .where(GoldSample.status == "open")) or 0,
        }
    if role == "admin":
        ctx["sys"] = {
            "users": db.scalar(select(func.count(User.id))) or 0,
            "concepts": db.scalar(select(func.count(Concept.id))) or 0,
            "operators": db.scalar(select(func.count(Operator.id))) or 0,
            "gold_items": db.scalar(select(func.count(GoldItem.id))) or 0,
        }
    return templates.TemplateResponse(request=request, name="workbench.html", context=ctx)


# ================= 概念页面 =================

@router.get("/concepts")
def concepts_page(request: Request, status: str | None = None, parent_id: int | None = None,
                  q: str | None = None, domain: str | None = None, page: int = 1,
                  db: Session = Depends(get_db), user: dict = Depends(current_user)):
    items, total = query_concepts(db, status=status, parent_id=parent_id, q=q,
                                  domain=domain, page=page, size=PAGE_SIZE)
    all_concepts = db.scalars(select(Concept).order_by(Concept.id)).all()
    parent_details = concept_parents_map(db, [c.id for c in items])
    domains = sorted({(c.domain or "通用") for c in all_concepts})
    selected_parent = next(({"id": c.id, "name": c.name} for c in all_concepts
                            if c.id == parent_id), None) if parent_id is not None else None
    return templates.TemplateResponse(request=request, name="concepts/list.html", context={
        "request": request, "user": user, "items": items, "total": total,
        "page": page, "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        "q": q, "status": status, "parent_id": parent_id, "domain": domain, "domains": domains,
        "parents": all_concepts, "parent_names": {c.id: c.name for c in all_concepts},
        "selected_parent": selected_parent,
        "parent_details": parent_details,
        "stats": usage_map(db, [c.id for c in items]),
    })


@router.get("/concepts/new")
def concept_new_page(request: Request, db: Session = Depends(get_db),
                     user: dict = Depends(current_user)):
    _forbid(user)
    return templates.TemplateResponse(request=request, name="concepts/form.html", context={
        "request": request, "user": user, "c": None,
        "all_parents": [], "selected_primary": None, "selected_extras": [], "error": None,
    })


async def _concept_form(request: Request) -> dict:
    form = await request.form()
    primary = _int_or_none(form.get("parent_id"))
    extras = [int(x) for x in form.getlist("extra_parent_ids") if str(x).isdigit()]
    parents = []
    for pid in ([primary] if primary is not None else []) + extras:
        if pid not in parents:
            parents.append(pid)
    return {
        "name": str(form.get("name", "")),
        "domain": str(form.get("domain", "")).strip() or "通用",
        "aliases": _split_multi(str(form.get("aliases", ""))),
        "definition": str(form.get("definition", "")),
        "parent_id": primary,
        "parents": parents,
        "status": str(form.get("status", "approved")),
    }


def _concept_refs(db: Session, ids) -> list[dict]:
    ids = [i for i in dict.fromkeys(ids or []) if i]
    if not ids:
        return []
    by_id = {c.id: c for c in db.scalars(select(Concept).where(Concept.id.in_(ids))).all()}
    return [{"id": i, "name": by_id[i].name} for i in ids if i in by_id]


def _selected_parents(db: Session, parent_ids, primary_id):
    refs = _concept_refs(db, parent_ids)
    primary = next((p for p in refs if p["id"] == primary_id), None)
    extras = [p for p in refs if p["id"] != primary_id]
    return primary, extras


@router.post("/concepts/new")
async def concept_new(request: Request, db: Session = Depends(get_db),
                      user: dict = Depends(current_user)):
    _forbid(user)
    data = await _concept_form(request)
    error = None
    try:
        c = create_concept(db, actor=user["username"], check_similar=True, **data)
    except SimilarityBlock as e:
        error, c = str(e), None
    except HTTPException as e:
        error, c = e.detail, None
    if c is None:
        primary, extras = _selected_parents(db, data.get("parents"), data.get("parent_id"))
        return templates.TemplateResponse(request=request, name="concepts/form.html", context={
            "request": request, "user": user, "c": data,
            "all_parents": [],
            "selected_primary": primary, "selected_extras": extras,
            "error": error,
        }, status_code=400)
    flash(request, "概念已创建")
    return RedirectResponse(f"/concepts/{c.id}", status_code=303)


@router.get("/concepts/{cid}")
def concept_detail_page(cid: int, request: Request, db: Session = Depends(get_db),
                        user: dict = Depends(current_user)):
    c = get_concept_or_404(db, cid)
    child_ids = select(ConceptParent.concept_id).where(ConceptParent.parent_id == cid)
    children = db.scalars(select(Concept).where(Concept.id.in_(child_ids)).order_by(Concept.id)).all()
    ops = db.scalars(select(Operator).where(Operator.subject_concept_id == cid).order_by(Operator.id)).all()
    names = name_map(db, ([c.parent_id] if c.parent_id else []) + [o.owner_concept_id for o in ops])
    all_parents = concept_parents_map(db, [cid]).get(cid, [])
    subs = db.scalars(
        select(Submission).where(Submission.concept_id == cid).order_by(Submission.id.desc())
    ).all()
    hist = entity_history(db, "concept", cid, limit=50)
    linked_questions = db.execute(
        select(Question, QuestionConcept.note)
        .join(QuestionConcept, QuestionConcept.question_id == Question.id)
        .where(QuestionConcept.concept_id == cid)
        .order_by(Question.updated_at.desc()).limit(20)
    ).all()
    from ..issues.service import related_issues
    keol_entity_count = db.scalar(
        select(func.count(KeolEntity.id)).where(KeolEntity.type == c.name)) or 0
    return templates.TemplateResponse(request=request, name="concepts/detail.html", context={
        "request": request, "user": user, "c": c, "children": children, "operators": ops,
        "parent_name": names.get(c.parent_id) if c.parent_id else None,
        "all_parents": all_parents,
        "owner_names": names, "usage": usage_map(db, [cid]).get(cid),
        "keol_entity_count": keol_entity_count,
        "submissions": [submission_dict(db, s) for s in subs],
        "history": [{"entry": h, "diff": diff_entries(h.before, h.after) if h.action != "create" else []}
                    for h in hist],
        "linked_questions": [{"id": q.id, "ext_id": q.ext_id, "text": (q.text or "")[:60],
                              "status": q.status, "note": note}
                             for q, note in linked_questions],
        "related_issues": related_issues(db, "concept", cid),
    })


@router.get("/concepts/{cid}/edit")
def concept_edit_page(cid: int, request: Request, db: Session = Depends(get_db),
                      user: dict = Depends(current_user)):
    _forbid(user)
    c = get_concept_or_404(db, cid)
    all_parents = concept_parents_map(db, [cid]).get(cid, [])
    primary = next((p for p in all_parents if p["id"] == c.parent_id), None)
    extras = [p for p in all_parents if p["id"] != c.parent_id]
    return templates.TemplateResponse(request=request, name="concepts/form.html", context={
        "request": request, "user": user, "c": c,
        "all_parents": all_parents,
        "selected_primary": primary, "selected_extras": extras, "error": None,
    })


@router.post("/concepts/{cid}/edit")
async def concept_edit(cid: int, request: Request, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    _forbid(user)
    c = get_concept_or_404(db, cid)
    data = await _concept_form(request)
    changes = {k: v for k, v in data.items() if k != "status"}
    try:
        update_concept(db, c, changes, user["username"])
    except HTTPException as e:
        shown = {"id": c.id, **changes}
        all_parents = concept_parents_map(db, [cid]).get(cid, [])
        primary, extras = _selected_parents(db, changes.get("parents"), changes.get("parent_id"))
        return templates.TemplateResponse(request=request, name="concepts/form.html", context={
            "request": request, "user": user, "c": shown,
            "all_parents": all_parents,
            "selected_primary": primary, "selected_extras": extras, "error": e.detail,
        }, status_code=400)
    flash(request, "已保存")
    return RedirectResponse(f"/concepts/{cid}", status_code=303)


@router.post("/concepts/{cid}/disable")
def concept_disable(cid: int, request: Request, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    _forbid(user)
    c = get_concept_or_404(db, cid)
    set_concept_status(db, c, "disabled", user["username"])
    flash(request, "已停用")
    return RedirectResponse(f"/concepts/{cid}", status_code=303)


@router.post("/concepts/{cid}/restore")
def concept_restore(cid: int, request: Request, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    _forbid(user)
    c = get_concept_or_404(db, cid)
    set_concept_status(db, c, "approved", user["username"])
    flash(request, "已恢复")
    return RedirectResponse(f"/concepts/{cid}", status_code=303)


@router.post("/concepts/{cid}/rollback/{audit_id}")
def concept_rollback(cid: int, audit_id: int, request: Request, db: Session = Depends(get_db),
                     user: dict = Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "仅管理员可回滚")
    entry = db.get(AuditLog, audit_id)
    if not entry or entry.entity_type != "concept" or entry.entity_id != cid:
        raise HTTPException(404, "历史记录不存在")
    try:
        audit_rollback(db, audit_id, user["username"])
    except HTTPException as e:
        flash(request, e.detail, "err")
        return RedirectResponse(f"/concepts/{cid}", status_code=303)
    flash(request, "已回滚")
    return RedirectResponse(f"/concepts/{cid}", status_code=303)


# ================= 算子页面 =================

@router.get("/operators")
def operators_page(request: Request, subject_concept_id: int | None = None, q: str | None = None,
                   page: int = 1, db: Session = Depends(get_db),
                   user: dict = Depends(current_user)):
    size = 50
    stmt = select(Operator)
    if subject_concept_id is not None:
        stmt = stmt.where(Operator.subject_concept_id == subject_concept_id)
    if q and q.strip():
        stmt = stmt.where(Operator.name.like(f"%{q.strip()}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    ops = db.scalars(stmt.order_by(Operator.id).offset((page - 1) * size).limit(size)).all()
    names = name_map(db, [o.subject_concept_id for o in ops] + [o.owner_concept_id for o in ops])
    selected_subject = None
    if subject_concept_id is not None:
        sc = db.get(Concept, subject_concept_id)
        if sc:
            selected_subject = {"id": sc.id, "name": sc.name}
    return templates.TemplateResponse(request=request, name="operators/list.html", context={
        "request": request, "user": user, "operators": ops, "names": names,
        "selected_subject": selected_subject, "total": total,
        "page": page, "pages": max(1, (total + size - 1) // size),
        "subject_concept_id": subject_concept_id, "q": q,
    })


async def _operator_form(request: Request) -> dict:
    form = await request.form()
    subject = _int_or_none(form.get("subject_concept_id"))
    if subject is None:
        raise HTTPException(400, "必须选择主体概念")
    return {
        "name": str(form.get("name", "")),
        "subject_concept_id": subject,
        "owner_concept_id": _int_or_none(form.get("owner_concept_id")),
        "param_types": _split_multi(str(form.get("param_types", ""))),
        "return_type": str(form.get("return_type", "")),
    }


@router.get("/operators/new")
def operator_new_page(request: Request, db: Session = Depends(get_db),
                      user: dict = Depends(current_user)):
    _forbid(user)
    return templates.TemplateResponse(request=request, name="operators/form.html", context={
        "request": request, "user": user, "o": None,
        "selected_subject": None, "selected_owner": None, "error": None,
    })


@router.post("/operators/new")
async def operator_new(request: Request, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    _forbid(user)
    data = None
    try:
        data = await _operator_form(request)
        o = create_operator(db, actor=user["username"], **data)
    except HTTPException as e:
        subject, _ = _selected_parents(db, [data.get("subject_concept_id")] if data else [],
                                       data.get("subject_concept_id") if data else None)
        owner, _ = _selected_parents(db, [data.get("owner_concept_id")] if data else [],
                                     data.get("owner_concept_id") if data else None)
        return templates.TemplateResponse(request=request, name="operators/form.html", context={
            "request": request, "user": user, "o": data,
            "selected_subject": subject, "selected_owner": owner, "error": e.detail,
        }, status_code=400)
    flash(request, "算子已创建")
    return RedirectResponse("/operators", status_code=303)


@router.get("/operators/{oid}")
def operator_detail_page(oid: int, request: Request, db: Session = Depends(get_db),
                         user: dict = Depends(current_user)):
    """算子详情页（Issue 集成入口之一；纯增量）。"""
    o = get_operator_or_404(db, oid)
    names = name_map(db, [o.subject_concept_id, o.owner_concept_id])
    from ..issues.service import related_issues
    return templates.TemplateResponse(request=request, name="operators/detail.html", context={
        "request": request, "user": user, "o": o,
        "subject_name": names.get(o.subject_concept_id),
        "owner_name": names.get(o.owner_concept_id or o.subject_concept_id),
        "related_issues": related_issues(db, "operator", oid),
    })


@router.get("/operators/{oid}/edit")
def operator_edit_page(oid: int, request: Request, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    _forbid(user)
    o = get_operator_or_404(db, oid)
    subject, _ = _selected_parents(db, [o.subject_concept_id], o.subject_concept_id)
    owner, _ = _selected_parents(db, [o.owner_concept_id], o.owner_concept_id)
    return templates.TemplateResponse(request=request, name="operators/form.html", context={
        "request": request, "user": user, "o": o,
        "selected_subject": subject, "selected_owner": owner, "error": None,
    })


@router.post("/operators/{oid}/edit")
async def operator_edit(oid: int, request: Request, db: Session = Depends(get_db),
                        user: dict = Depends(current_user)):
    _forbid(user)
    o = get_operator_or_404(db, oid)
    data = None
    try:
        data = await _operator_form(request)
        update_operator(db, o, data, user["username"])
    except HTTPException as e:
        subject, _ = _selected_parents(db, [data.get("subject_concept_id")] if data else [],
                                       data.get("subject_concept_id") if data else None)
        owner, _ = _selected_parents(db, [data.get("owner_concept_id")] if data else [],
                                     data.get("owner_concept_id") if data else None)
        shown = {"id": oid, **data} if data else o
        return templates.TemplateResponse(request=request, name="operators/form.html", context={
            "request": request, "user": user, "o": shown,
            "selected_subject": subject, "selected_owner": owner, "error": e.detail,
        }, status_code=400)
    flash(request, "已保存")
    return RedirectResponse("/operators", status_code=303)


@router.post("/operators/{oid}/disable")
def operator_disable(oid: int, request: Request, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    _forbid(user)
    o = get_operator_or_404(db, oid)
    set_operator_status(db, o, "disabled", user["username"])
    flash(request, "已停用")
    return RedirectResponse("/operators", status_code=303)


# ================= 提交与审核页面（M2） =================

def _forbid_review(user: dict):
    if user["role"] not in EDIT_ROLES:
        raise HTTPException(403, "权限不足")


@router.get("/submit")
def submit_page(request: Request, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    return templates.TemplateResponse(request=request, name="submit.html", context={
        "request": request, "user": user,
        "selected_primary": None, "selected_extras": [],
        "values": None, "error": None, "llm_enabled": llm_enabled(),
    })


@router.post("/submit")
async def submit_create(request: Request, db: Session = Depends(get_db),
                        user: dict = Depends(current_user)):
    form = await request.form()
    primary = _int_or_none(form.get("suggested_parent_id"))
    extras = [int(x) for x in form.getlist("extra_parent_ids") if str(x).isdigit()]
    parent_ids = []
    for pid in ([primary] if primary is not None else []) + extras:
        if pid is not None and pid not in parent_ids:
            parent_ids.append(pid)
    values = {
        "name": str(form.get("name", "")),
        "domain": str(form.get("domain", "")).strip() or "通用",
        "aliases": _split_multi(str(form.get("aliases", ""))),
        "definition": str(form.get("definition", "")),
        "suggested_parent_id": primary,
        "suggested_parent_ids": parent_ids,
        "reason": str(form.get("reason", "")),
        "sample_question": str(form.get("sample_question", "")),
    }
    try:
        sub = create_submission(db, actor=user["username"], **values)
    except HTTPException as e:
        primary, extras = _selected_parents(
            db, values.get("suggested_parent_ids"), values.get("suggested_parent_id"))
        return templates.TemplateResponse(request=request, name="submit.html", context={
            "request": request, "user": user,
            "selected_primary": primary, "selected_extras": extras,
            "values": values, "error": e.detail, "llm_enabled": llm_enabled(),
        }, status_code=400)
    tmp = db.scalar(select(TmpMapping).where(TmpMapping.submission_id == sub.id))
    flash(request, f"已提交审核{('，临时 ID：' + tmp.tmp_id) if tmp else ''}")
    return RedirectResponse("/submit", status_code=303)


@router.get("/review")
def review_page(request: Request, status: str = "pending",
                db: Session = Depends(get_db), user: dict = Depends(current_user)):
    _forbid_review(user)
    if status and status not in SUB_STATUS:
        raise HTTPException(400, "非法状态")
    stmt = select(Submission).order_by(Submission.id.desc()).limit(200)
    if status:
        stmt = stmt.where(Submission.status == status)
    subs = db.scalars(stmt).all()
    return templates.TemplateResponse(request=request, name="review.html", context={
        "request": request, "user": user,
        "items": [submission_dict(db, s) for s in subs],
        "status": status,
        "llm_enabled": llm_enabled(),
    })


# 注意：/review/batch 必须先于 /review/{sid} 声明
@router.post("/review/batch")
async def review_batch(request: Request, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    _forbid_review(user)
    form = await request.form()
    ids = [int(x) for x in form.getlist("ids") if str(x).isdigit()]
    action = str(form.get("action", ""))
    comment = str(form.get("comment", ""))
    if not ids:
        flash(request, "未选择任何提交单", "err")
        return RedirectResponse("/review", status_code=303)
    try:
        processed, skipped = batch_action(db, ids, action, comment, user["username"])
    except HTTPException as e:
        flash(request, e.detail, "err")
        return RedirectResponse("/review", status_code=303)
    flash(request, f"已处理 {len(processed)} 条（跳过 {len(skipped)} 条）")
    return RedirectResponse("/review", status_code=303)


@router.post("/review/{sid}")
async def review_action(sid: int, request: Request, db: Session = Depends(get_db),
                        user: dict = Depends(current_user)):
    _forbid_review(user)
    form = await request.form()
    action = str(form.get("action", ""))
    comment = str(form.get("comment", ""))
    sub = get_submission_or_404(db, sid)
    try:
        if action == "approve":
            approve(db, sub, comment, user["username"])
        elif action == "reject":
            reject(db, sub, comment, user["username"])
        elif action == "merge":
            target = _int_or_none(form.get("merge_target_id"))
            if target is None:
                raise HTTPException(400, "请选择合并目标概念")
            merge(db, sub, comment, target, user["username"])
        else:
            raise HTTPException(400, "未知操作")
    except HTTPException as e:
        flash(request, e.detail, "err")
        return RedirectResponse("/review", status_code=303)
    flash(request, "已处理")
    return RedirectResponse("/review", status_code=303)


# ================= TMP 回填页面（M3） =================

def _tmp_context(db: Session, status: str | None) -> dict:
    stmt = select(TmpMapping).order_by(TmpMapping.id.desc()).limit(500)
    if status:
        if status not in ("open", "resolved", "merged"):
            raise HTTPException(400, "非法状态")
        stmt = stmt.where(TmpMapping.status == status)
    maps = db.scalars(stmt).all()
    names = name_map(db, [m.concept_id for m in maps])
    records = db.scalars(
        select(BackfillRecord).order_by(BackfillRecord.id.desc()).limit(50)
    ).all()
    return {
        "mappings": [tmp_mapping_dict(m, names=names) for m in maps],
        "records": records, "status": status,
    }


@router.get("/tmp")
def tmp_page(request: Request, status: str | None = None,
             db: Session = Depends(get_db), user: dict = Depends(current_user)):
    return templates.TemplateResponse(request=request, name="tmp.html", context={
        "request": request, "user": user, "result": None, **_tmp_context(db, status),
    })


@router.post("/tmp/backfill")
async def tmp_backfill(request: Request, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    form = await request.form()
    result = backfill(
        db, text=str(form.get("text", "")),
        source_label=str(form.get("source_label", "")), actor=user["username"],
    )
    return templates.TemplateResponse(request=request, name="tmp.html", context={
        "request": request, "user": user, "result": result, **_tmp_context(db, None),
    })


# ================= 变更日志 / 体检 / 导入导出（M4-M6） =================

@router.get("/history")
def history_page(request: Request, entity_type: str | None = None, actor: str | None = None,
                 db: Session = Depends(get_db), user: dict = Depends(current_user)):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(200)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)
    rows = db.scalars(stmt).all()
    names = name_map(db, [r.entity_id for r in rows if r.entity_type == "concept"])
    return templates.TemplateResponse(request=request, name="history.html", context={
        "request": request, "user": user, "rows": rows, "names": names,
        "entity_type": entity_type, "actor": actor,
        "diffs": {r.id: (diff_entries(r.before, r.after) if r.action != "create" else []) for r in rows},
    })


@router.get("/health")
def health_page(request: Request, kind: str = "unused",
                db: Session = Depends(get_db), user: dict = Depends(current_user)):
    _forbid_review(user)
    if kind not in KIND_LABELS:
        raise HTTPException(400, "非法类别")
    report = db.scalar(select(HealthReport).order_by(HealthReport.id.desc()).limit(1))
    runs = db.scalars(select(HealthReport).order_by(HealthReport.id.desc()).limit(10)).all()
    return templates.TemplateResponse(request=request, name="health.html", context={
        "request": request, "user": user, "report": report, "runs": runs,
        "kind": kind, "kinds": KIND_LABELS,
    })


@router.post("/health/run")
def health_run_page(request: Request, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    _forbid_review(user)
    run_health(db, "manual")
    flash(request, "已生成本体体检报告")
    return RedirectResponse("/health", status_code=303)


def _forbid_admin(user: dict):
    if user["role"] != "admin":
        raise HTTPException(403, "仅管理员可用")


@router.get("/io")
def io_page(request: Request, user: dict = Depends(current_user)):
    _forbid_admin(user)
    return templates.TemplateResponse(request=request, name="io.html", context={
        "request": request, "user": user, "result": None, "error": None,
    })


@router.post("/io/import")
async def io_import(request: Request, db: Session = Depends(get_db),
                    user: dict = Depends(current_user)):
    _forbid_admin(user)
    form = await request.form()
    fmt = str(form.get("format", "json"))
    text = str(form.get("text", "") or "")
    upload = form.get("file")
    if upload is not None and getattr(upload, "filename", ""):
        text = (await upload.read()).decode("utf-8-sig")
    result, error = None, None
    try:
        if not text.strip():
            raise HTTPException(400, "请上传文件或粘贴文本")
        fm = parse_field_map(str(form.get("field_map", "")))
        if fmt == "json":
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise HTTPException(400, "JSON 应为 {concepts: [...], operators: [...]}")
        else:
            payload = csv_to_payload(text, "operators" if fmt == "csv_ops" else "concepts", fm)
        result = import_payload(db, payload, user["username"], fm)
    except HTTPException as e:
        error = e.detail
    except json.JSONDecodeError as e:
        error = f"JSON 解析失败：{e}"
    return templates.TemplateResponse(request=request, name="io.html", context={
        "request": request, "user": user, "result": result, "error": error,
    }, status_code=200 if result else 400)
