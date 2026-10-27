"""Issue 管理：/issues（页面）与 /api/issues/*（第①步：列表 + 新建/编辑）。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Issue
from ..security import current_user, require_role
from ..templating import templates
from . import service
from .service import ISSUE_STATUS, ISSUE_STATUS_LABELS, ISSUE_TYPE_TAGS, LEVELS

router = APIRouter(tags=["issues"])
PAGE_SIZE = 50


def _list_ctx(db: Session, **kw):
    rows, total = service.query_issues(db, size=PAGE_SIZE, **kw)
    return {
        "rows": [service.row_to_dict(i, lc, uc) for i, lc, uc in rows],
        "total": total, "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
    }


def _filters(db: Session) -> dict:
    domains = db.scalars(select(Issue.domain).distinct().order_by(Issue.domain)).all()
    tags = db.scalars(select(Issue.type_tag).distinct().order_by(Issue.type_tag)).all()
    return {"domains": domains, "type_tags": sorted(set(tags) | set(ISSUE_TYPE_TAGS)),
            "levels": LEVELS, "statuses": ISSUE_STATUS, "status_labels": ISSUE_STATUS_LABELS}


# ---------------- API ----------------

class CaseIn(BaseModel):
    source: str = ""
    original_text: str = ""
    variant_a: str = ""
    variant_b: str = ""
    diff_note: str = ""


class IssueCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    type_tag: str = Field(min_length=1)
    description: str = Field(min_length=1)
    domain: str = "通用"
    group_name: str = ""
    importance: str | None = None
    urgency: str | None = None
    owner: str = ""
    cases: list[CaseIn] = Field(min_length=1)
    entity_link: dict | None = None  # {entity_type, entity_id, note?}
    annotation_batch_id: int | None = None
    annotation_unit_id: int | None = None
    annotation_stage: str = ""
    annotation_versions: list | None = None
    annotation_evidence: dict | None = None


class IssuePatchIn(BaseModel):
    title: str | None = None
    type_tag: str | None = None
    domain: str | None = None
    group_name: str | None = None
    importance: str | None = None
    urgency: str | None = None
    owner: str | None = None
    description: str | None = None
    open_items: str | None = None


@router.get("/api/issues")
def api_list(domain: str | None = None, status: str | None = None, type_tag: str | None = None,
             importance: str | None = None, urgency: str | None = None,
             closed: bool | None = None, q: str | None = None, page: int = 1,
             db: Session = Depends(get_db), user: dict = Depends(require_role())):
    ctx = _list_ctx(db, domain=domain, status=status, type_tag=type_tag,
                    importance=importance, urgency=urgency, closed=closed, q=q, page=page)
    return {"items": ctx["rows"], "total": ctx["total"], "page": page, "pages": ctx["pages"]}


@router.post("/api/issues", status_code=201)
def api_create(data: IssueCreateIn, db: Session = Depends(get_db),
               user: dict = Depends(require_role())):
    i = service.create_issue(
        db, title=data.title, type_tag=data.type_tag, description=data.description,
        domain=data.domain, group_name=data.group_name, importance=data.importance,
        urgency=data.urgency, owner=data.owner,
        cases=[c.model_dump() for c in data.cases], entity_link=data.entity_link,
        annotation_batch_id=data.annotation_batch_id,
        annotation_unit_id=data.annotation_unit_id,
        annotation_stage=data.annotation_stage,
        annotation_versions=data.annotation_versions,
        annotation_evidence=data.annotation_evidence,
        actor=user["username"])
    return {"id": i.id, "code": i.code, "status": i.status}


@router.get("/api/issues/{iid}")
def api_detail(iid: int, db: Session = Depends(get_db), user: dict = Depends(require_role())):
    return service.detail_payload(db, service.get_issue_or_404(db, iid))


@router.patch("/api/issues/{iid}")
def api_patch(iid: int, data: IssuePatchIn, db: Session = Depends(get_db),
              user: dict = Depends(require_role())):
    i = db.get(Issue, iid)
    if not i:
        raise HTTPException(404, "issue 不存在")
    i = service.update_issue(db, i, data.model_dump(exclude_unset=True), user["username"])
    return {"id": i.id, "code": i.code, "status": i.status, "updated_at": i.updated_at}


# ---------------- 页面 ----------------

@router.get("/issues")
def issues_page(request: Request, domain: str | None = None, status: str | None = None,
                type_tag: str | None = None, importance: str | None = None,
                urgency: str | None = None, closed: bool | None = None,
                q: str | None = None, page: int = 1,
                db: Session = Depends(get_db), user: dict = Depends(current_user)):
    ctx = _list_ctx(db, domain=domain, status=status, type_tag=type_tag,
                    importance=importance, urgency=urgency, closed=closed, q=q, page=page)
    return templates.TemplateResponse(request=request, name="issues/list.html", context={
        "request": request, "user": user, **ctx, **_filters(db),
        "domain": domain, "status": status, "type_tag": type_tag,
        "importance": importance, "urgency": urgency, "closed": closed, "q": q, "page": page,
    })


@router.get("/issues/export.csv")
def issues_export(domain: str | None = None, status: str | None = None, type_tag: str | None = None,
                  importance: str | None = None, urgency: str | None = None,
                  closed: bool | None = None, q: str | None = None,
                  db: Session = Depends(get_db), user: dict = Depends(current_user)):
    rows, _ = service.query_issues(db, domain=domain, status=status, type_tag=type_tag,
                                   importance=importance, urgency=urgency, closed=closed,
                                   q=q, page=1, size=100000)
    return Response(service.export_csv(rows), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename*=utf-8''" + quote("issues.csv")})


def _form_ctx(db: Session, request: Request, user: dict, **kw) -> dict:
    return {"request": request, "user": user, **_filters(db), **kw}


@router.get("/issues/new")
def issue_new_page(request: Request, entity_type: str | None = None, entity_id: int | None = None,
                   annotation_batch_id: int | None = None,
                   annotation_unit_id: int | None = None,
                   annotation_stage: str | None = None,
                   db: Session = Depends(get_db), user: dict = Depends(current_user)):
    return templates.TemplateResponse(request=request, name="issues/form.html", context=_form_ctx(
        db, request, user, i=None, error=None,
        prefill={"entity_type": entity_type or "", "entity_id": entity_id or "",
                 "annotation_batch_id": annotation_batch_id or "",
                 "annotation_unit_id": annotation_unit_id or "",
                 "annotation_stage": annotation_stage or ""},
    ))


def _parse_cases(form) -> list[dict]:
    """表单里最多 3 条 Case 块（case_N_* 命名）。"""
    cases = []
    for n in (1, 2, 3):
        cases.append({
            "source": str(form.get(f"case_{n}_source", "")),
            "original_text": str(form.get(f"case_{n}_original", "")),
            "variant_a": str(form.get(f"case_{n}_a", "")),
            "variant_b": str(form.get(f"case_{n}_b", "")),
            "diff_note": str(form.get(f"case_{n}_diff", "")),
        })
    return cases


@router.post("/issues/new")
async def issue_new(request: Request, db: Session = Depends(get_db),
                    user: dict = Depends(current_user)):
    form = await request.form()
    entity_link = None
    if str(form.get("entity_type", "")) and str(form.get("entity_id", "")).isdigit():
        entity_link = {"entity_type": str(form["entity_type"]),
                       "entity_id": int(form["entity_id"])}
    try:
        i = service.create_issue(
            db, title=str(form.get("title", "")), type_tag=str(form.get("type_tag", "")),
            description=str(form.get("description", "")),
            domain=str(form.get("domain", "通用")), group_name=str(form.get("group_name", "")),
            importance=str(form.get("importance", "")) or None,
            urgency=str(form.get("urgency", "")) or None,
            owner=str(form.get("owner", "")),
            cases=_parse_cases(form), entity_link=entity_link,
            annotation_batch_id=int(form["annotation_batch_id"]) if str(form.get("annotation_batch_id", "")).isdigit() else None,
            annotation_unit_id=int(form["annotation_unit_id"]) if str(form.get("annotation_unit_id", "")).isdigit() else None,
            annotation_stage=str(form.get("annotation_stage", "")),
            actor=user["username"])
    except HTTPException as e:
        return templates.TemplateResponse(request=request, name="issues/form.html", context=_form_ctx(
            db, request, user, i=None, error=e.detail, prefill=None, values=form,
        ), status_code=400)
    return RedirectResponse(f"/issues/{i.id}?ok=issue 已创建（{i.code}）", status_code=303)


@router.get("/issues/{iid}/edit")
def issue_edit_page(iid: int, request: Request, db: Session = Depends(get_db),
                    user: dict = Depends(current_user)):
    i = db.get(Issue, iid)
    if not i:
        raise HTTPException(404, "issue 不存在")
    return templates.TemplateResponse(request=request, name="issues/form.html", context=_form_ctx(
        db, request, user, i=i, error=None, prefill=None, values=None,
    ))


@router.post("/issues/{iid}/edit")
async def issue_edit(iid: int, request: Request, db: Session = Depends(get_db),
                     user: dict = Depends(current_user)):
    i = db.get(Issue, iid)
    if not i:
        raise HTTPException(404, "issue 不存在")
    form = await request.form()
    changes = {k: str(form.get(k, "")) for k in
               ("title", "type_tag", "domain", "group_name", "owner", "description", "open_items")}
    changes["importance"] = str(form.get("importance", "")) or None
    changes["urgency"] = str(form.get("urgency", "")) or None
    try:
        service.update_issue(db, i, changes, user["username"])
    except HTTPException as e:
        return templates.TemplateResponse(request=request, name="issues/form.html", context=_form_ctx(
            db, request, user, i=i, error=e.detail, prefill=None, values=form,
        ), status_code=400)
    return RedirectResponse(f"/issues/{iid}?ok=已保存", status_code=303)


# ================= 详情页与闭环动作（第②步） =================

class QuestionIn(BaseModel):
    text: str = Field(min_length=1)
    source: str = "human"


class ViewpointIn(BaseModel):
    person: str = Field(min_length=1)
    stance: str = "补充"
    body: str = Field(min_length=1)


class CommentIn(BaseModel):
    body: str = Field(min_length=1)


class LinkIn(BaseModel):
    entity_type: str
    entity_id: int
    note: str = ""


class StatusIn(BaseModel):
    status: str


class ImpactIn(BaseModel):
    entity_type: str
    entity_id: int
    action: str = "需复核"


class CloseIn(BaseModel):
    conclusion: str
    scope: str
    open_items: str = ""
    impacts: list[ImpactIn] = []
    impact_none: bool = False


# ---------- API ----------

@router.post("/api/issues/{iid}/cases", status_code=201)
def api_add_case(iid: int, data: CaseIn, db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    issue = service.get_issue_or_404(db, iid)
    row = service.add_case(db, issue, data.model_dump(), user["username"])
    return {"id": row.id, "seq": row.seq}


@router.post("/api/issues/{iid}/questions", status_code=201)
def api_add_question(iid: int, data: QuestionIn, db: Session = Depends(get_db),
                     user: dict = Depends(require_role())):
    row = service.add_question(db, service.get_issue_or_404(db, iid),
                               data.text, data.source, user["username"])
    return {"id": row.id, "seq": row.seq}


@router.post("/api/issues/{iid}/viewpoints", status_code=201)
def api_add_viewpoint(iid: int, data: ViewpointIn, db: Session = Depends(get_db),
                      user: dict = Depends(require_role())):
    row = service.add_viewpoint(db, service.get_issue_or_404(db, iid),
                                data.person, data.stance, data.body, user["username"])
    return {"id": row.id}


@router.post("/api/issues/{iid}/comments", status_code=201)
def api_add_comment(iid: int, data: CommentIn, db: Session = Depends(get_db),
                    user: dict = Depends(require_role())):
    row = service.add_comment(db, service.get_issue_or_404(db, iid), data.body, user["username"])
    return {"id": row.id}


@router.post("/api/issues/{iid}/links", status_code=201)
def api_add_link(iid: int, data: LinkIn, db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    row = service.add_link(db, service.get_issue_or_404(db, iid),
                           data.entity_type, data.entity_id, data.note, user["username"])
    return {"id": row.id}


@router.post("/api/issues/{iid}/status")
def api_status(iid: int, data: StatusIn, db: Session = Depends(get_db),
               user: dict = Depends(require_role())):
    i = service.set_status(db, service.get_issue_or_404(db, iid), data.status, user["username"])
    return {"id": i.id, "status": i.status}


@router.post("/api/issues/{iid}/close")
def api_close(iid: int, data: CloseIn, db: Session = Depends(get_db),
              user: dict = Depends(require_role())):
    issue = service.get_issue_or_404(db, iid)
    i = service.close_issue(db, issue, conclusion=data.conclusion, scope=data.scope,
                            open_items=data.open_items,
                            impacts=[x.model_dump() for x in data.impacts],
                            impact_none=data.impact_none, actor=user["username"])
    return {"id": i.id, "status": i.status, "resolved_at": i.resolved_at}


@router.post("/api/issues/{iid}/reopen")
def api_reopen(iid: int, db: Session = Depends(get_db), user: dict = Depends(require_role())):
    i = service.reopen_issue(db, service.get_issue_or_404(db, iid), user["username"])
    return {"id": i.id, "status": i.status}


@router.post("/api/issues/{iid}/impacts/{impact_id}/toggle")
def api_toggle_impact(iid: int, impact_id: int, db: Session = Depends(get_db),
                      user: dict = Depends(require_role())):
    row = service.toggle_impact(db, service.get_issue_or_404(db, iid), impact_id,
                                user["username"])
    return {"id": row.id, "done": row.done}


@router.get("/api/issues/{iid}/impacts")
def api_impacts(iid: int, db: Session = Depends(get_db), user: dict = Depends(require_role())):
    payload = service.detail_payload(db, service.get_issue_or_404(db, iid))
    return {"items": payload["impacts"], "progress": payload["impact_progress"]}


# ---------- 页面 ----------

@router.get("/issues/{iid}")
def issue_detail(iid: int, request: Request, db: Session = Depends(get_db),
                 user: dict = Depends(current_user)):
    issue = service.get_issue_or_404(db, iid)
    return templates.TemplateResponse(request=request, name="issues/detail.html", context={
        "request": request, "user": user, "p": service.detail_payload(db, issue),
        "statuses": ISSUE_STATUS, "status_labels": ISSUE_STATUS_LABELS,
        "levels": LEVELS, "entity_types": ["concept", "operator", "question", "ke_version", "gold_item"],
        "actions": ["需修改", "需复核", "无需改"],
    })


def _err_redirect(iid: int, e: HTTPException):
    return RedirectResponse(f"/issues/{iid}?err={e.detail}", status_code=303)


@router.post("/issues/{iid}/cases")
async def page_add_case(iid: int, request: Request, db: Session = Depends(get_db),
                        user: dict = Depends(current_user)):
    form = await request.form()
    try:
        service.add_case(db, service.get_issue_or_404(db, iid), {
            "source": str(form.get("source", "")), "original_text": str(form.get("original_text", "")),
            "variant_a": str(form.get("variant_a", "")), "variant_b": str(form.get("variant_b", "")),
            "diff_note": str(form.get("diff_note", ""))}, user["username"])
    except HTTPException as e:
        return _err_redirect(iid, e)
    return RedirectResponse(f"/issues/{iid}?ok=案例已添加", status_code=303)


@router.post("/issues/{iid}/questions")
async def page_add_question(iid: int, request: Request, db: Session = Depends(get_db),
                            user: dict = Depends(current_user)):
    form = await request.form()
    try:
        service.add_question(db, service.get_issue_or_404(db, iid),
                             str(form.get("text", "")), str(form.get("source", "human")),
                             user["username"])
    except HTTPException as e:
        return _err_redirect(iid, e)
    return RedirectResponse(f"/issues/{iid}?ok=问题已添加", status_code=303)


@router.post("/issues/{iid}/viewpoints")
async def page_add_viewpoint(iid: int, request: Request, db: Session = Depends(get_db),
                             user: dict = Depends(current_user)):
    form = await request.form()
    try:
        service.add_viewpoint(db, service.get_issue_or_404(db, iid),
                              str(form.get("person", "")), str(form.get("stance", "补充")),
                              str(form.get("body", "")), user["username"])
    except HTTPException as e:
        return _err_redirect(iid, e)
    return RedirectResponse(f"/issues/{iid}?ok=观点已添加", status_code=303)


@router.post("/issues/{iid}/comments")
async def page_add_comment(iid: int, request: Request, db: Session = Depends(get_db),
                           user: dict = Depends(current_user)):
    form = await request.form()
    try:
        service.add_comment(db, service.get_issue_or_404(db, iid),
                            str(form.get("body", "")), user["username"])
    except HTTPException as e:
        return _err_redirect(iid, e)
    return RedirectResponse(f"/issues/{iid}?ok=已评论", status_code=303)


@router.post("/issues/{iid}/links")
async def page_add_link(iid: int, request: Request, db: Session = Depends(get_db),
                        user: dict = Depends(current_user)):
    form = await request.form()
    try:
        service.add_link(db, service.get_issue_or_404(db, iid),
                         str(form.get("entity_type", "")),
                         int(str(form.get("entity_id", "0")) or 0),
                         str(form.get("note", "")), user["username"])
    except HTTPException as e:
        return _err_redirect(iid, e)
    except ValueError:
        return RedirectResponse(f"/issues/{iid}?err=对象 ID 必须是数字", status_code=303)
    return RedirectResponse(f"/issues/{iid}?ok=已关联", status_code=303)


@router.post("/issues/{iid}/status")
async def page_status(iid: int, request: Request, db: Session = Depends(get_db),
                      user: dict = Depends(current_user)):
    form = await request.form()
    try:
        service.set_status(db, service.get_issue_or_404(db, iid),
                           str(form.get("status", "")), user["username"])
    except HTTPException as e:
        return _err_redirect(iid, e)
    return RedirectResponse(f"/issues/{iid}?ok=状态已更新", status_code=303)


@router.post("/issues/{iid}/close")
async def page_close(iid: int, request: Request, db: Session = Depends(get_db),
                     user: dict = Depends(current_user)):
    form = await request.form()
    issue = service.get_issue_or_404(db, iid)
    impacts = []
    # 结案表单：勾选关联对象 → 影响条目（action 同行选择）
    for key in form.getlist("impact_ref"):
        if "|" not in key:
            continue
        et, eid = key.split("|", 1)
        action = str(form.get(f"action_{et}_{eid}", "需复核"))
        impacts.append({"entity_type": et, "entity_id": int(eid), "action": action})
    try:
        service.close_issue(db, issue, conclusion=str(form.get("conclusion", "")),
                            scope=str(form.get("scope", "")),
                            open_items=str(form.get("open_items", "")),
                            impacts=impacts,
                            impact_none=form.get("impact_none") is not None,
                            actor=user["username"])
    except HTTPException as e:
        return _err_redirect(iid, e)
    except ValueError:
        return RedirectResponse(f"/issues/{iid}?err=影响条目参数错误", status_code=303)
    return RedirectResponse(f"/issues/{iid}?ok=已结案，回填清单已生成", status_code=303)


@router.post("/issues/{iid}/reopen")
def page_reopen(iid: int, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    try:
        service.reopen_issue(db, service.get_issue_or_404(db, iid), user["username"])
    except HTTPException as e:
        return _err_redirect(iid, e)
    return RedirectResponse(f"/issues/{iid}?ok=已重新打开（讨论中）", status_code=303)


@router.post("/issues/{iid}/impacts/{impact_id}/toggle")
def page_toggle_impact(iid: int, impact_id: int, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    try:
        service.toggle_impact(db, service.get_issue_or_404(db, iid), impact_id, user["username"])
    except HTTPException as e:
        return _err_redirect(iid, e)
    return RedirectResponse(f"/issues/{iid}?ok=已更新", status_code=303)
