"""题目级视图：/questions（页面）与 /api/questions/*（agent 对称）。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Concept, Operator, Question
from ..security import current_user, require_role
from ..templating import templates
from . import service
from .service import QUESTION_STATUS, STATUS_LABELS

router = APIRouter(tags=["questions"])
PAGE_SIZE = 50


def _list_context(db: Session, *, domain=None, group_name=None, status=None,
                  has_open_issue=None, q=None, page=1):
    rows, total = service.query_questions(
        db, domain=domain, group_name=group_name, status=status,
        has_open_issue=has_open_issue, q=q, page=page, size=PAGE_SIZE)
    domains = db.scalars(select(Question.domain).distinct().order_by(Question.domain)).all()
    groups = db.scalars(select(Question.group_name).distinct().order_by(Question.group_name)).all()
    return {
        "rows": [service.row_to_dict(q, oi, kv, cc) for q, oi, kv, cc in rows],
        "total": total, "page": page, "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        "domains": domains, "groups": [g for g in groups if g],
    }


# ---------------- API ----------------

@router.get("/api/questions")
def api_list(domain: str | None = None, group_name: str | None = None,
             status: str | None = None, has_open_issue: bool = False,
             q: str | None = None, page: int = 1,
             db: Session = Depends(get_db), user: dict = Depends(require_role())):
    ctx = _list_context(db, domain=domain, group_name=group_name, status=status,
                        has_open_issue=has_open_issue, q=q, page=page)
    return {"items": ctx["rows"], "total": ctx["total"], "page": ctx["page"],
            "size": PAGE_SIZE, "pages": ctx["pages"]}


@router.post("/api/questions/sync")
def api_sync(db: Session = Depends(get_db), user: dict = Depends(require_role("reviewer", "admin"))):
    """从 ke_tasks 回填/刷新 questions（幂等，可重复调用）。"""
    return service.backfill_from_ke_tasks(db)


# ---------------- 页面 ----------------

@router.get("/questions")
def questions_page(request: Request, domain: str | None = None, group_name: str | None = None,
                   status: str | None = None, has_open_issue: bool = False,
                   q: str | None = None, page: int = 1,
                   db: Session = Depends(get_db), user: dict = Depends(current_user)):
    ctx = _list_context(db, domain=domain, group_name=group_name, status=status,
                        has_open_issue=has_open_issue, q=q, page=page)
    return templates.TemplateResponse(request=request, name="questions/list.html", context={
        "request": request, "user": user, **ctx,
        "domain": domain, "group_name": group_name, "status": status,
        "has_open_issue": has_open_issue, "q": q,
        "statuses": QUESTION_STATUS, "status_labels": STATUS_LABELS,
    })


@router.get("/questions/export.csv")
def questions_export(request: Request, domain: str | None = None, group_name: str | None = None,
                     status: str | None = None, has_open_issue: bool = False, q: str | None = None,
                     db: Session = Depends(get_db), user: dict = Depends(current_user)):
    rows, _ = service.query_questions(
        db, domain=domain, group_name=group_name, status=status,
        has_open_issue=has_open_issue, q=q, page=1, size=100000)
    return Response(
        service.export_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=utf-8''" + quote("questions.csv")},
    )


@router.post("/questions/sync")
def questions_sync(db: Session = Depends(get_db), user: dict = Depends(current_user)):
    if user["role"] not in ("reviewer", "admin"):
        raise HTTPException(403, "同步需审核员 / 管理员")
    stats = service.backfill_from_ke_tasks(db)
    return RedirectResponse(
        f"/questions?ok=同步完成：任务 {stats['tasks']} 行 → 新建 {stats['created']} · "
        f"更新 {stats['updated']} · 重号 {stats['collisions']} · 自动关联 {stats['linked']}",
        status_code=303)


# ================= 详情页（第②步） =================

def _role(user: dict) -> str:
    return user["role"]


class StatusIn(BaseModel):
    status: str


class LinkIn(BaseModel):
    concept_id: int | None = None
    operator_id: int | None = None
    note: str = ""


class KeVersionIn(BaseModel):
    content: str = Field(min_length=1)


class IssueIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    body: str = ""
    tags: list[str] = []


# ---------- API ----------

@router.get("/api/questions/{qid}")
def api_detail(qid: int, db: Session = Depends(get_db), user: dict = Depends(require_role())):
    return service.detail_payload(db, service.get_question_or_404(db, qid))


@router.post("/api/questions/{qid}/status")
def api_status(qid: int, data: StatusIn, db: Session = Depends(get_db),
               user: dict = Depends(require_role())):
    q = service.get_question_or_404(db, qid)
    q = service.change_status(db, q, data.status, user["username"], user["role"])
    return {"id": q.id, "status": q.status}


@router.post("/api/questions/{qid}/concepts", status_code=201)
def api_add_link(qid: int, data: LinkIn, db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    q = service.get_question_or_404(db, qid)
    link = service.add_link(db, q, concept_id=data.concept_id,
                            operator_id=data.operator_id, note=data.note,
                            actor=user["username"])
    return {"id": link.id, "question_id": qid, "concept_id": link.concept_id,
            "operator_id": link.operator_id}


@router.post("/api/questions/{qid}/ke-versions", status_code=201)
def api_add_ke_version(qid: int, data: KeVersionIn, db: Session = Depends(get_db),
                       user: dict = Depends(require_role("annotator", "admin"))):
    q = service.get_question_or_404(db, qid)
    v = service.add_ke_version(db, q, data.content, user["username"])
    return service.version_dict(v)


@router.get("/api/questions/{qid}/ke-versions/diff")
def api_ke_diff(qid: int, v1: int | None = None, v2: int | None = None,
                db: Session = Depends(get_db), user: dict = Depends(require_role())):
    q = service.get_question_or_404(db, qid)
    result = service.versions_for_diff(db, q, v1, v2)
    if result is None:
        raise HTTPException(400, "不足两个版本，无法对比")
    return result


@router.post("/api/questions/{qid}/issues", status_code=201)
def api_create_issue(qid: int, data: IssueIn, db: Session = Depends(get_db),
                     user: dict = Depends(require_role())):
    q = service.get_question_or_404(db, qid)
    i = service.create_issue(db, q, title=data.title, body=data.body,
                             tags=data.tags, actor=user["username"])
    return service.issue_dict(i)


@router.post("/api/questions/{qid}/issues/{iid}/resolve")
def api_resolve_issue(qid: int, iid: int, db: Session = Depends(get_db),
                      user: dict = Depends(require_role())):
    q = service.get_question_or_404(db, qid)
    return service.issue_dict(service.resolve_issue(db, q, iid, user["username"]))


# ---------- 页面 ----------

@router.get("/questions/{qid}")
def question_detail(qid: int, request: Request, v1: int | None = None, v2: int | None = None,
                    db: Session = Depends(get_db), user: dict = Depends(current_user)):
    q = service.get_question_or_404(db, qid)
    payload = service.detail_payload(db, q)
    diff = service.versions_for_diff(db, q, v1, v2) if (v1 is not None or v2 is not None) else \
        service.versions_for_diff(db, q, None, None)
    concepts = db.scalars(select(Concept).where(Concept.status == "approved").order_by(Concept.id)).all()
    operators = db.scalars(select(Operator).where(Operator.status == "approved").order_by(Operator.id)).all()
    return templates.TemplateResponse(request=request, name="questions/detail.html", context={
        "request": request, "user": user, "p": payload, "diff": diff,
        "all_concepts": concepts, "all_operators": operators,
        "statuses": QUESTION_STATUS, "status_labels": STATUS_LABELS,
        "review_statuses": service.REVIEW_STATUSES,
    })


@router.post("/questions/{qid}/status")
async def page_status(qid: int, request: Request, db: Session = Depends(get_db),
                      user: dict = Depends(current_user)):
    form = await request.form()
    try:
        service.change_status(db, service.get_question_or_404(db, qid),
                              str(form.get("status", "")), user["username"], user["role"])
    except HTTPException as e:
        return RedirectResponse(f"/questions/{qid}?err={e.detail}", status_code=303)
    return RedirectResponse(f"/questions/{qid}?ok=状态已更新", status_code=303)


@router.post("/questions/{qid}/concepts")
async def page_add_link(qid: int, request: Request, db: Session = Depends(get_db),
                        user: dict = Depends(current_user)):
    form = await request.form()
    kind = str(form.get("kind", "concept"))
    target = str(form.get("target_id", ""))
    try:
        tid = int(target) if target.isdigit() else None
        service.add_link(db, service.get_question_or_404(db, qid),
                         concept_id=tid if kind == "concept" else None,
                         operator_id=tid if kind == "operator" else None,
                         note=str(form.get("note", "")), actor=user["username"])
    except HTTPException as e:
        return RedirectResponse(f"/questions/{qid}?err={e.detail}", status_code=303)
    return RedirectResponse(f"/questions/{qid}?ok=已添加关联", status_code=303)


@router.post("/questions/{qid}/ke-versions")
async def page_add_ke_version(qid: int, request: Request, db: Session = Depends(get_db),
                              user: dict = Depends(current_user)):
    if user["role"] not in ("annotator", "admin"):
        raise HTTPException(403, "仅标注员 / 管理员可保存新版本")
    form = await request.form()
    try:
        service.add_ke_version(db, service.get_question_or_404(db, qid),
                               str(form.get("content", "")), user["username"])
    except HTTPException as e:
        return RedirectResponse(f"/questions/{qid}?err={e.detail}", status_code=303)
    return RedirectResponse(f"/questions/{qid}?ok=已保存新版本", status_code=303)


@router.post("/questions/{qid}/issues")
async def page_create_issue(qid: int, request: Request, db: Session = Depends(get_db),
                            user: dict = Depends(current_user)):
    form = await request.form()
    tags = [t.strip() for t in str(form.get("tags", "")).replace("，", ",").split(",") if t.strip()]
    try:
        service.create_issue(db, service.get_question_or_404(db, qid),
                             title=str(form.get("title", "")), body=str(form.get("body", "")),
                             tags=tags, actor=user["username"])
    except HTTPException as e:
        return RedirectResponse(f"/questions/{qid}?err={e.detail}", status_code=303)
    return RedirectResponse(f"/questions/{qid}?ok=issue 已登记", status_code=303)


@router.post("/questions/{qid}/issues/{iid}/resolve")
def page_resolve_issue(qid: int, iid: int, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    try:
        service.resolve_issue(db, service.get_question_or_404(db, qid), iid, user["username"])
    except HTTPException as e:
        return RedirectResponse(f"/questions/{qid}?err={e.detail}", status_code=303)
    return RedirectResponse(f"/questions/{qid}?ok=issue 已解决", status_code=303)
