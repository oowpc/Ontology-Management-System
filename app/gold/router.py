"""M8 · GOLDKE 管理：/gold/* 页面与 /api/gold/* 接口（《数据标注执行方案》口径）。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import GoldArticle, GoldIssue, GoldIssueComment, GoldSample
from ..security import current_user, require_role
from ..templating import templates
from . import sampling, service
from .service import GROUPS, ISSUE_STATUS_LABELS, STAGE_LABELS, STAGES

router = APIRouter(tags=["gold"])


def _int_or_none(raw) -> int | None:
    s = str(raw or "").strip()
    return int(s) if s.lstrip("-").isdigit() else None


# ==================== API ====================

class ArticleIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=256)
    domain: str = "未分类"
    group_name: str
    ke_target: int = 30
    note: str = ""


class ArticlePatch(BaseModel):
    title: str | None = None
    domain: str | None = None
    group_name: str | None = None
    ke_target: int | None = None
    ke_done: int | None = None
    note: str | None = None


@router.get("/api/gold/stats")
def api_stats(db: Session = Depends(get_db), user: dict = Depends(require_role())):
    return service.dashboard_stats(db)


@router.get("/api/gold/articles")
def api_articles(domain: str | None = None, group_name: str | None = None,
                 stage: str | None = None, db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    stmt = select(GoldArticle).order_by(GoldArticle.id)
    if domain:
        stmt = stmt.where(GoldArticle.domain == domain)
    if group_name:
        stmt = stmt.where(GoldArticle.group_name == group_name)
    if stage:
        if stage not in STAGES:
            raise HTTPException(400, "非法阶段")
        stmt = stmt.where(GoldArticle.stage == stage)
    rows = db.scalars(stmt).all()
    return {"items": [service.article_dict(a) for a in rows], "total": len(rows)}


@router.post("/api/gold/articles", status_code=201)
def api_article_create(data: ArticleIn, db: Session = Depends(get_db),
                       user: dict = Depends(require_role())):
    a = service.create_article(db, code=data.code, title=data.title, domain=data.domain,
                               group_name=data.group_name, ke_target=data.ke_target,
                               note=data.note, actor=user["username"])
    return service.article_dict(a)


@router.patch("/api/gold/articles/{aid}")
def api_article_patch(aid: int, data: ArticlePatch, db: Session = Depends(get_db),
                      user: dict = Depends(require_role())):
    a = service.get_article_or_404(db, aid)
    return service.article_dict(
        service.update_article(db, a, data.model_dump(exclude_unset=True)))


@router.post("/api/gold/articles/{aid}/advance")
def api_article_advance(aid: int, db: Session = Depends(get_db),
                        user: dict = Depends(require_role())):
    return service.article_dict(service.advance_stage(db, service.get_article_or_404(db, aid)))


class CellIn(BaseModel):
    day_index: int
    slot: str
    group_name: str
    task: str = ""
    article_code: str = ""
    status: str = "planned"
    note: str = ""


@router.get("/api/gold/schedule")
def api_schedule(db: Session = Depends(get_db), user: dict = Depends(require_role())):
    return {"groups": GROUPS, "grid": service.schedule_grid(db)}


@router.post("/api/gold/schedule/cell")
def api_schedule_cell(data: CellIn, db: Session = Depends(get_db),
                      user: dict = Depends(require_role())):
    c = service.upsert_cell(db, **data.model_dump())
    return service.schedule_cell_dict(c)


class IssueIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    content: str = ""
    cluster: str = ""
    related_type: str = ""
    related_ref: str = ""


def _issue_dict(db: Session, i: GoldIssue, with_comments=False) -> dict:
    d = {
        "id": i.id, "title": i.title, "content": i.content, "cluster": i.cluster,
        "status": i.status, "related_type": i.related_type, "related_ref": i.related_ref,
        "raised_by": i.raised_by, "resolution": i.resolution,
        "created_at": i.created_at, "resolved_at": i.resolved_at,
    }
    if with_comments:
        cs = db.scalars(select(GoldIssueComment).where(
            GoldIssueComment.issue_id == i.id).order_by(GoldIssueComment.id)).all()
        d["comments"] = [{"id": c.id, "author": c.author, "content": c.content,
                          "created_at": c.created_at} for c in cs]
    return d


@router.get("/api/gold/issues")
def api_issues(status: str | None = None, cluster: str | None = None,
               db: Session = Depends(get_db), user: dict = Depends(require_role())):
    stmt = select(GoldIssue).order_by(GoldIssue.id.desc())
    if status:
        stmt = stmt.where(GoldIssue.status == status)
    if cluster:
        stmt = stmt.where(GoldIssue.cluster == cluster)
    rows = db.scalars(stmt.limit(300)).all()
    return {"items": [_issue_dict(db, i) for i in rows], "total": len(rows)}


@router.post("/api/gold/issues", status_code=201)
def api_issue_create(data: IssueIn, db: Session = Depends(get_db),
                     user: dict = Depends(require_role())):
    i = GoldIssue(title=data.title.strip(), content=data.content, cluster=data.cluster.strip(),
                  related_type=data.related_type.strip(), related_ref=data.related_ref.strip(),
                  raised_by=user["username"])
    db.add(i)
    db.commit()
    db.refresh(i)
    return _issue_dict(db, i)


class CommentIn(BaseModel):
    content: str = Field(min_length=1)


class ResolveIn(BaseModel):
    status: str  # resolved | escalated | open
    resolution: str = ""


@router.post("/api/gold/issues/{iid}/comments", status_code=201)
def api_issue_comment(iid: int, data: CommentIn, db: Session = Depends(get_db),
                      user: dict = Depends(require_role())):
    i = db.get(GoldIssue, iid)
    if not i:
        raise HTTPException(404, "issue 不存在")
    c = GoldIssueComment(issue_id=iid, author=user["username"], content=data.content)
    db.add(c)
    db.commit()
    return _issue_dict(db, i, with_comments=True)


@router.post("/api/gold/issues/{iid}/status")
def api_issue_status(iid: int, data: ResolveIn, db: Session = Depends(get_db),
                     user: dict = Depends(require_role())):
    i = db.get(GoldIssue, iid)
    if not i:
        raise HTTPException(404, "issue 不存在")
    if data.status not in ISSUE_STATUS_LABELS:
        raise HTTPException(400, "状态取值：open / resolved / escalated")
    i.status = data.status
    if data.status in ("resolved", "escalated"):
        i.resolution = data.resolution
        i.resolved_at = datetime.now()
    else:
        i.resolved_at = None
    db.commit()
    return _issue_dict(db, i, with_comments=True)


class SampleIn(BaseModel):
    sample_date: str
    scope: str = Field(min_length=1)
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    note: str = ""


class AutoSampleIn(BaseModel):
    """M21 系统抽样：size 与 rate 二选一（size 优先；rate 默认 0.1）。"""
    group_name: str = ""
    article_code: str = ""
    status: str = ""
    size: int = 0
    rate: float = 0.1
    seed: int | None = None
    note: str = ""


class SampleReviewIn(BaseModel):
    gold_item_id: int
    verdict: str
    note: str = ""


class ArbitrateIn(BaseModel):
    verdict: str
    note: str = ""


@router.get("/api/gold/samples")
def api_samples(db: Session = Depends(get_db), user: dict = Depends(require_role())):
    return {"items": sampling.sample_list(db)}


@router.post("/api/gold/samples", status_code=201)
def api_sample_create(data: SampleIn, db: Session = Depends(get_db),
                      user: dict = Depends(require_role())):
    if data.passed > data.total:
        raise HTTPException(400, "通过数不能大于抽查总数")
    s = GoldSample(sample_date=data.sample_date, scope=data.scope, total=data.total,
                   passed=data.passed, note=data.note, checker=user["username"],
                   kind="manual")
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id, "pass_rate": (s.passed / s.total) if s.total else None}


@router.post("/api/gold/samples/auto", status_code=201)
def api_sample_auto(data: AutoSampleIn, db: Session = Depends(get_db),
                    user: dict = Depends(require_role(*sampling.SAMPLER_ROLES))):
    """M21：系统抽样创建抽查（分层等距，seed 落 params 可复现）。"""
    s = sampling.create_auto_sample(db, actor=user["username"], **data.model_dump())
    return {"id": s.id, "total": s.total, "params": s.params, "scope": s.scope}


@router.get("/api/gold/samples/{sid}")
def api_sample_detail(sid: int, db: Session = Depends(get_db),
                      user: dict = Depends(require_role())):
    s = sampling.get_sample_or_404(db, sid)
    return sampling.sample_detail(db, s, viewer=user["username"], viewer_role=user["role"])


@router.post("/api/gold/samples/{sid}/reviews", status_code=201)
def api_sample_review(sid: int, data: SampleReviewIn, db: Session = Depends(get_db),
                      user: dict = Depends(require_role(*sampling.SAMPLER_ROLES))):
    s = sampling.get_sample_or_404(db, sid)
    r = sampling.add_review(db, s, gold_item_id=data.gold_item_id,
                            reviewer=user["username"], verdict=data.verdict, note=data.note)
    return {"id": r.id, "sample_id": sid, "gold_item_id": r.gold_item_id,
            "verdict": r.verdict}


@router.post("/api/gold/samples/{sid}/items/{gid}/arbitrate")
def api_sample_arbitrate(sid: int, gid: int, data: ArbitrateIn,
                         db: Session = Depends(get_db),
                         user: dict = Depends(require_role(*sampling.SAMPLER_ROLES))):
    s = sampling.get_sample_or_404(db, sid)
    si = sampling.arbitrate(db, s, gold_item_id=gid, arbiter=user["username"],
                            verdict=data.verdict, note=data.note)
    return {"gold_item_id": gid, "final_verdict": si.final_verdict, "arbiter": si.arbiter}


@router.post("/api/gold/samples/{sid}/close")
def api_sample_close(sid: int, db: Session = Depends(get_db),
                     user: dict = Depends(require_role(*sampling.SAMPLER_ROLES))):
    s = sampling.get_sample_or_404(db, sid)
    s = sampling.close_sample(db, s, user["username"])
    return {"id": s.id, "status": s.status, "total": s.total, "passed": s.passed,
            "pass_rate": (s.passed / s.total) if s.total else None}


# ==================== 页面 ====================

@router.get("/gold")
def gold_dashboard(request: Request, db: Session = Depends(get_db),
                   user: dict = Depends(current_user)):
    return templates.TemplateResponse(request=request, name="gold/dashboard.html", context={
        "request": request, "user": user, "stats": service.dashboard_stats(db),
    })


@router.get("/gold/articles")
def gold_articles(request: Request, domain: str | None = None, group_name: str | None = None,
                  stage: str | None = None, db: Session = Depends(get_db),
                  user: dict = Depends(current_user)):
    stmt = select(GoldArticle).order_by(GoldArticle.id)
    if domain:
        stmt = stmt.where(GoldArticle.domain == domain)
    if group_name:
        stmt = stmt.where(GoldArticle.group_name == group_name)
    if stage:
        stmt = stmt.where(GoldArticle.stage == stage)
    rows = db.scalars(stmt).all()
    domains = db.scalars(select(GoldArticle.domain).distinct()).all()
    return templates.TemplateResponse(request=request, name="gold/articles.html", context={
        "request": request, "user": user, "items": rows, "domains": domains,
        "domain": domain, "group_name": group_name, "stage": stage,
        "groups": GROUPS, "stages": STAGES, "stage_labels": STAGE_LABELS,
    })


@router.get("/gold/articles/new")
def gold_article_new_page(request: Request, user: dict = Depends(current_user)):
    return templates.TemplateResponse(request=request, name="gold/article_form.html", context={
        "request": request, "user": user, "a": None, "groups": GROUPS, "error": None,
    })


async def _article_form(request: Request) -> dict:
    form = await request.form()
    return {
        "code": str(form.get("code", "")),
        "title": str(form.get("title", "")),
        "domain": str(form.get("domain", "")),
        "group_name": str(form.get("group_name", "")),
        "ke_target": _int_or_none(form.get("ke_target")) or 30,
        "ke_done": _int_or_none(form.get("ke_done")) or 0,
        "note": str(form.get("note", "")),
    }


@router.post("/gold/articles/new")
async def gold_article_new(request: Request, db: Session = Depends(get_db),
                           user: dict = Depends(current_user)):
    data = await _article_form(request)
    try:
        a = service.create_article(db, actor=user["username"], **data)
    except HTTPException as e:
        return templates.TemplateResponse(request=request, name="gold/article_form.html", context={
            "request": request, "user": user, "a": data, "groups": GROUPS, "error": e.detail,
        }, status_code=400)
    return RedirectResponse("/gold/articles?ok=篇任务已创建", status_code=303)


@router.get("/gold/articles/{aid}/edit")
def gold_article_edit_page(aid: int, request: Request, db: Session = Depends(get_db),
                           user: dict = Depends(current_user)):
    a = service.get_article_or_404(db, aid)
    return templates.TemplateResponse(request=request, name="gold/article_form.html", context={
        "request": request, "user": user, "a": a, "groups": GROUPS, "error": None,
    })


@router.post("/gold/articles/{aid}/edit")
async def gold_article_edit(aid: int, request: Request, db: Session = Depends(get_db),
                            user: dict = Depends(current_user)):
    a = service.get_article_or_404(db, aid)
    data = await _article_form(request)
    changes = {k: v for k, v in data.items() if k != "code"}
    try:
        service.update_article(db, a, changes)
    except HTTPException as e:
        return templates.TemplateResponse(request=request, name="gold/article_form.html", context={
            "request": request, "user": user, "a": a, "groups": GROUPS, "error": e.detail,
        }, status_code=400)
    return RedirectResponse("/gold/articles?ok=已保存", status_code=303)


@router.post("/gold/articles/{aid}/advance")
def gold_article_advance(aid: int, db: Session = Depends(get_db),
                         user: dict = Depends(current_user)):
    try:
        service.advance_stage(db, service.get_article_or_404(db, aid))
    except HTTPException as e:
        return RedirectResponse(f"/gold/articles?err={e.detail}", status_code=303)
    return RedirectResponse("/gold/articles?ok=已推进到下一阶段", status_code=303)


@router.post("/gold/articles/{aid}/ke_done")
async def gold_article_ke_done(aid: int, request: Request, db: Session = Depends(get_db),
                               user: dict = Depends(current_user)):
    a = service.get_article_or_404(db, aid)
    form = await request.form()
    n = _int_or_none(form.get("ke_done"))
    if n is None:
        return RedirectResponse("/gold/articles?err=条数必须是数字", status_code=303)
    service.update_article(db, a, {"ke_done": n})
    return RedirectResponse("/gold/articles?ok=已更新完成条数", status_code=303)


@router.get("/gold/schedule")
def gold_schedule(request: Request, db: Session = Depends(get_db),
                  user: dict = Depends(current_user)):
    return templates.TemplateResponse(request=request, name="gold/schedule.html", context={
        "request": request, "user": user, "grid": service.schedule_grid(db), "groups": GROUPS,
    })


@router.post("/gold/schedule/cell")
async def gold_schedule_cell(request: Request, db: Session = Depends(get_db),
                             user: dict = Depends(current_user)):
    form = await request.form()
    try:
        service.upsert_cell(
            db,
            day_index=_int_or_none(form.get("day_index")) or 0,
            slot=str(form.get("slot", "")),
            group_name=str(form.get("group_name", "")),
            task=str(form.get("task", "")),
            article_code=str(form.get("article_code", "")),
            status=str(form.get("status", "planned")),
            note=str(form.get("note", "")),
        )
    except HTTPException as e:
        return RedirectResponse(f"/gold/schedule?err={e.detail}", status_code=303)
    return RedirectResponse("/gold/schedule?ok=已保存", status_code=303)


@router.get("/gold/issues")
def gold_issues(request: Request, status: str | None = None, cluster: str | None = None,
                db: Session = Depends(get_db), user: dict = Depends(current_user)):
    stmt = select(GoldIssue).order_by(GoldIssue.id.desc())
    if status:
        stmt = stmt.where(GoldIssue.status == status)
    if cluster:
        stmt = stmt.where(GoldIssue.cluster == cluster)
    rows = db.scalars(stmt.limit(300)).all()
    clusters = db.scalars(select(GoldIssue.cluster).distinct().where(GoldIssue.cluster != "")).all()
    return templates.TemplateResponse(request=request, name="gold/issues.html", context={
        "request": request, "user": user, "items": rows, "clusters": clusters,
        "status": status, "cluster": cluster, "status_labels": ISSUE_STATUS_LABELS,
    })


@router.post("/gold/issues/new")
async def gold_issue_new(request: Request, db: Session = Depends(get_db),
                         user: dict = Depends(current_user)):
    form = await request.form()
    title = str(form.get("title", "")).strip()
    if not title:
        return RedirectResponse("/gold/issues?err=标题不能为空", status_code=303)
    i = GoldIssue(title=title, content=str(form.get("content", "")),
                  cluster=str(form.get("cluster", "")).strip(),
                  related_type=str(form.get("related_type", "")).strip(),
                  related_ref=str(form.get("related_ref", "")).strip(),
                  raised_by=user["username"])
    db.add(i)
    db.commit()
    return RedirectResponse(f"/gold/issues/{i.id}?ok=issue 已登记", status_code=303)


@router.get("/gold/issues/{iid}")
def gold_issue_detail(iid: int, request: Request, db: Session = Depends(get_db),
                      user: dict = Depends(current_user)):
    i = db.get(GoldIssue, iid)
    if not i:
        raise HTTPException(404, "issue 不存在")
    cs = db.scalars(select(GoldIssueComment).where(
        GoldIssueComment.issue_id == iid).order_by(GoldIssueComment.id)).all()
    return templates.TemplateResponse(request=request, name="gold/issue_detail.html", context={
        "request": request, "user": user, "i": i, "comments": cs,
        "status_labels": ISSUE_STATUS_LABELS,
    })


@router.post("/gold/issues/{iid}/comments")
async def gold_issue_comment(iid: int, request: Request, db: Session = Depends(get_db),
                             user: dict = Depends(current_user)):
    form = await request.form()
    content = str(form.get("content", "")).strip()
    if not content:
        return RedirectResponse(f"/gold/issues/{iid}?err=评论不能为空", status_code=303)
    if not db.get(GoldIssue, iid):
        raise HTTPException(404, "issue 不存在")
    db.add(GoldIssueComment(issue_id=iid, author=user["username"], content=content))
    db.commit()
    return RedirectResponse(f"/gold/issues/{iid}?ok=已评论", status_code=303)


@router.post("/gold/issues/{iid}/status")
async def gold_issue_status(iid: int, request: Request, db: Session = Depends(get_db),
                            user: dict = Depends(current_user)):
    i = db.get(GoldIssue, iid)
    if not i:
        raise HTTPException(404, "issue 不存在")
    form = await request.form()
    st = str(form.get("status", ""))
    if st not in ISSUE_STATUS_LABELS:
        return RedirectResponse(f"/gold/issues/{iid}?err=非法状态", status_code=303)
    i.status = st
    if st in ("resolved", "escalated"):
        i.resolution = str(form.get("resolution", ""))
        i.resolved_at = datetime.now()
    else:
        i.resolved_at = None
    db.commit()
    return RedirectResponse(f"/gold/issues/{iid}?ok=状态已更新", status_code=303)


@router.get("/gold/samples")
def gold_samples(request: Request, db: Session = Depends(get_db),
                 user: dict = Depends(current_user)):
    from ..models import GoldItem
    return templates.TemplateResponse(request=request, name="gold/samples.html", context={
        "request": request, "user": user, "items": sampling.sample_list(db),
        "today": datetime.now().strftime("%Y-%m-%d"),
        "groups": service.list_groups(db),
        "codes": db.scalars(select(GoldItem.article_code).distinct().order_by(GoldItem.article_code)).all(),
        "sampler_roles": sampling.SAMPLER_ROLES,
    })


@router.post("/gold/samples/new")
async def gold_sample_new(request: Request, db: Session = Depends(get_db),
                          user: dict = Depends(current_user)):
    form = await request.form()
    total = _int_or_none(form.get("total")) or 0
    passed = _int_or_none(form.get("passed")) or 0
    if passed > total:
        return RedirectResponse("/gold/samples?err=通过数不能大于抽查总数", status_code=303)
    s = GoldSample(sample_date=str(form.get("sample_date", "")),
                   scope=str(form.get("scope", "")).strip(),
                   total=total, passed=passed,
                   note=str(form.get("note", "")), checker=user["username"], kind="manual")
    if not s.scope:
        return RedirectResponse("/gold/samples?err=抽查范围不能为空", status_code=303)
    db.add(s)
    db.commit()
    return RedirectResponse("/gold/samples?ok=抽查已登记", status_code=303)


@router.post("/gold/samples/auto")
async def gold_sample_auto(request: Request, db: Session = Depends(get_db),
                           user: dict = Depends(current_user)):
    if user["role"] not in sampling.SAMPLER_ROLES:
        raise HTTPException(403, "系统抽样仅审核员/管理员可创建")
    form = await request.form()
    try:
        s = sampling.create_auto_sample(
            db,
            group_name=str(form.get("group_name", "")),
            article_code=str(form.get("article_code", "")),
            status=str(form.get("status", "")),
            size=_int_or_none(form.get("size")) or 0,
            rate=float(str(form.get("rate") or 0.1)),
            seed=_int_or_none(form.get("seed")),
            note=str(form.get("note", "")),
            actor=user["username"])
    except (HTTPException, ValueError) as e:
        detail = e.detail if isinstance(e, HTTPException) else str(e)
        return RedirectResponse(f"/gold/samples?err={detail}", status_code=303)
    return RedirectResponse(f"/gold/samples/{s.id}?ok=已抽样 {s.total} 条，开始双人盲评", status_code=303)


@router.get("/gold/samples/{sid}")
def gold_sample_detail(sid: int, request: Request, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    s = sampling.get_sample_or_404(db, sid)
    d = sampling.sample_detail(db, s, viewer=user["username"], viewer_role=user["role"])
    return templates.TemplateResponse(request=request, name="gold/sample_detail.html", context={
        "request": request, "user": user, **d,
        "sampler_roles": sampling.SAMPLER_ROLES,
    })


@router.post("/gold/samples/{sid}/reviews")
async def gold_sample_review(sid: int, request: Request, db: Session = Depends(get_db),
                             user: dict = Depends(current_user)):
    if user["role"] not in sampling.SAMPLER_ROLES:
        raise HTTPException(403, "抽检打标仅审核员/管理员（标注员回避）")
    s = sampling.get_sample_or_404(db, sid)
    form = await request.form()
    try:
        sampling.add_review(db, s, gold_item_id=_int_or_none(form.get("gold_item_id")) or 0,
                            reviewer=user["username"], verdict=str(form.get("verdict", "")),
                            note=str(form.get("note", "")))
    except HTTPException as e:
        return RedirectResponse(f"/gold/samples/{sid}?err={e.detail}", status_code=303)
    return RedirectResponse(f"/gold/samples/{sid}?ok=已打标", status_code=303)


@router.post("/gold/samples/{sid}/items/{gid}/arbitrate")
async def gold_sample_arbitrate(sid: int, gid: int, request: Request,
                                db: Session = Depends(get_db),
                                user: dict = Depends(current_user)):
    if user["role"] not in sampling.SAMPLER_ROLES:
        raise HTTPException(403, "仲裁仅审核员/管理员")
    s = sampling.get_sample_or_404(db, sid)
    form = await request.form()
    try:
        sampling.arbitrate(db, s, gold_item_id=gid, arbiter=user["username"],
                           verdict=str(form.get("verdict", "")),
                           note=str(form.get("note", "")))
    except HTTPException as e:
        return RedirectResponse(f"/gold/samples/{sid}?err={e.detail}", status_code=303)
    return RedirectResponse(f"/gold/samples/{sid}?ok=仲裁已落定", status_code=303)


@router.post("/gold/samples/{sid}/close")
def gold_sample_close(sid: int, db: Session = Depends(get_db),
                      user: dict = Depends(current_user)):
    if user["role"] not in sampling.SAMPLER_ROLES:
        raise HTTPException(403, "关闭仅审核员/管理员")
    s = sampling.get_sample_or_404(db, sid)
    try:
        sampling.close_sample(db, s, user["username"])
    except HTTPException as e:
        return RedirectResponse(f"/gold/samples/{sid}?err={e.detail}", status_code=303)
    return RedirectResponse(f"/gold/samples/{sid}?ok=抽查已关闭", status_code=303)
