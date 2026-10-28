"""M10 · KE 逐条复核：待复核队列 → 逐条/批量打标 → 写回任务 → 留痕 → 上传重放。

口径：系统是复核结论的权威来源。复核动作写回 ke_tasks（一致→已完成；不一致→已退回
并自动加「复核退回」标签，进看板问题面板）；下次全量上传时 apply_latest_reviews()
自动重放系统结论——组内表格自报不覆盖系统复核。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import GoldItem, KeReview, KeTask
from ..security import current_user, require_role
from ..templating import flash, templates
from .service import list_groups

router = APIRouter(tags=["ke-review"])
REVIEW_ROLES = ("reviewer", "admin")
CONCLUSIONS = ("一致", "不一致")
RETURN_TAG = "复核退回"


# ---------------- 服务层 ----------------

def latest_reviews(db: Session, group: str | None = None) -> dict:
    """每题最新一条复核结论：{(group_name, ke_id): KeReview}（id 升序覆盖 = 最新）。"""
    stmt = select(KeReview).order_by(KeReview.id)
    if group:
        stmt = stmt.where(KeReview.group_name == group)
    latest = {}
    for r in db.scalars(stmt).all():
        latest[(r.group_name, r.ke_id)] = r
    return latest


def apply_latest_reviews(db: Session, group: str, rows: list[dict], warnings: list[str]) -> int:
    """上传重灌时重放系统复核结论（作用于入库前的行 dict）。返回覆盖条数。"""
    reviews = latest_reviews(db, group)
    n = 0
    for r in rows:
        rv = reviews.get((group, r["id"]))
        if not rv:
            continue
        new_status = "已完成" if rv.conclusion == "一致" else "已退回"
        if r["status"] != new_status or r["review"] != rv.conclusion:
            n += 1
        r["review"] = rv.conclusion
        r["status"] = new_status
        if rv.conclusion == "一致" and not r["finished"]:
            r["finished"] = datetime.now()
        if rv.conclusion == "不一致" and RETURN_TAG not in r["tags"]:
            r["tags"] = r["tags"] + [RETURN_TAG]
    if n:
        warnings.append(f"{n} 条已按系统复核结论覆盖（复核以系统为准，表内自报不覆盖）")
    return n


def review_queue(db: Session, group: str | None = None, only_pending: bool = True):
    """复核队列：默认仅「待复核」；all=1 可查全部（含已复核的）。"""
    stmt = select(KeTask).order_by(KeTask.group_name, KeTask.ke_id)
    if only_pending:
        stmt = stmt.where(KeTask.status == "待复核")
    if group:
        stmt = stmt.where(KeTask.group_name == group)
    return db.scalars(stmt).all()


def attach_contents(db: Session, tasks) -> dict:
    """题目 → 成品库 KE 内容（有篇编号且已入库的），复核界面直接看内容打标。"""
    out = {}
    for t in tasks:
        if not t.article_code:
            continue
        it = db.scalar(select(GoldItem).where(GoldItem.article_code == t.article_code,
                                              GoldItem.ke_id == t.ke_id))
        if it:
            out[(t.group_name, t.ke_id)] = it.ke_text
    return out


def task_dict(t: KeTask, contents: dict | None = None) -> dict:
    d = {"group_name": t.group_name, "ke_id": t.ke_id, "domain": t.domain,
         "summary": t.summary, "status": t.status, "person": t.person,
         "review": t.review, "tags": t.tags or [], "article_code": t.article_code or ""}
    if contents is not None:
        d["ke_text"] = contents.get((t.group_name, t.ke_id), "")
    return d


def review_one(db: Session, *, group: str, ke_id: str, conclusion: str,
               comment: str, reviewer: str) -> KeTask:
    if conclusion not in CONCLUSIONS:
        raise HTTPException(400, f"结论取值：{' / '.join(CONCLUSIONS)}")
    t = db.scalar(select(KeTask).where(KeTask.group_name == group, KeTask.ke_id == ke_id))
    if not t:
        raise HTTPException(404, f"题目不存在：{group} / {ke_id}")
    db.add(KeReview(group_name=group, ke_id=ke_id, reviewer=reviewer,
                    conclusion=conclusion, comment=(comment or "").strip()))
    t.review = conclusion                       # 写回任务当前行
    if conclusion == "一致":
        t.status = "已完成"
        if not t.finished_dt:
            t.finished_dt = datetime.now()
    else:
        t.status = "已退回"
        tags = list(t.tags or [])
        if RETURN_TAG not in tags:
            tags.append(RETURN_TAG)
        t.tags = tags
    db.commit()
    return t


# ---------------- API ----------------

@router.get("/api/ke/review/queue")
def api_queue(group: str | None = None, all: int = 0,
              db: Session = Depends(get_db), user: dict = Depends(require_role())):
    tasks = review_queue(db, group, only_pending=not all)
    contents = attach_contents(db, tasks)
    return {"items": [task_dict(t, contents) for t in tasks], "total": len(tasks)}


class ReviewIn(BaseModel):
    group_name: str
    ke_id: str
    conclusion: str
    comment: str = ""


@router.post("/api/ke/review", status_code=201)
def api_review(data: ReviewIn, db: Session = Depends(get_db),
               user: dict = Depends(require_role(*REVIEW_ROLES))):
    t = review_one(db, group=data.group_name.strip(), ke_id=data.ke_id.strip(),
                   conclusion=data.conclusion, comment=data.comment,
                   reviewer=user["username"])
    return task_dict(t)


class BatchReviewIn(BaseModel):
    items: list[dict] = Field(min_length=1)   # [{"group_name": ..., "ke_id": ...}]
    conclusion: str
    comment: str = ""


@router.post("/api/ke/review/batch")
def api_review_batch(data: BatchReviewIn, db: Session = Depends(get_db),
                     user: dict = Depends(require_role(*REVIEW_ROLES))):
    done, errors = [], []
    for it in data.items:
        try:
            t = review_one(db, group=str(it.get("group_name", "")).strip(),
                           ke_id=str(it.get("ke_id", "")).strip(),
                           conclusion=data.conclusion, comment=data.comment,
                           reviewer=user["username"])
            done.append(f"{t.group_name}/{t.ke_id}")
        except HTTPException as e:
            errors.append({"item": it, "detail": e.detail})
    return {"processed": done, "errors": errors}


@router.get("/api/ke/review/history")
def api_review_history(group: str | None = None, ke_id: str | None = None,
                       db: Session = Depends(get_db), user: dict = Depends(require_role())):
    stmt = select(KeReview).order_by(KeReview.id.desc()).limit(200)
    if group:
        stmt = stmt.where(KeReview.group_name == group)
    if ke_id:
        stmt = stmt.where(KeReview.ke_id == ke_id)
    rows = db.scalars(stmt).all()
    return {"items": [{"id": r.id, "group_name": r.group_name, "ke_id": r.ke_id,
                       "reviewer": r.reviewer, "conclusion": r.conclusion,
                       "comment": r.comment, "created_at": r.created_at} for r in rows]}


# ---------------- 页面 ----------------

@router.get("/ke/review")
def review_page(request: Request, group: str | None = None,
                db: Session = Depends(get_db), user: dict = Depends(current_user)):
    if user["role"] not in REVIEW_ROLES:
        raise HTTPException(403, "复核仅审核员 / 管理员")
    tasks = review_queue(db, group)
    return templates.TemplateResponse(request=request, name="ke/review.html", context={
        "request": request, "user": user, "items": tasks,
        "contents": attach_contents(db, tasks),
        "groups": list_groups(db), "group": group,
    })


@router.post("/ke/review/one")
async def review_one_form(request: Request, db: Session = Depends(get_db),
                          user: dict = Depends(current_user)):
    if user["role"] not in REVIEW_ROLES:
        raise HTTPException(403, "复核仅审核员 / 管理员")
    form = await request.form()
    try:
        review_one(db, group=str(form.get("group_name", "")).strip(),
                   ke_id=str(form.get("ke_id", "")).strip(),
                   conclusion=str(form.get("conclusion", "")),
                   comment=str(form.get("comment", "")), reviewer=user["username"])
    except HTTPException as e:
        flash(request, e.detail, "err")
        return RedirectResponse("/ke/review", status_code=303)
    flash(request, "已复核")
    return RedirectResponse("/ke/review", status_code=303)


@router.post("/ke/review/batch")
async def review_batch_form(request: Request, db: Session = Depends(get_db),
                            user: dict = Depends(current_user)):
    if user["role"] not in REVIEW_ROLES:
        raise HTTPException(403, "复核仅审核员 / 管理员")
    form = await request.form()
    conclusion = str(form.get("conclusion", ""))
    comment = str(form.get("comment", ""))
    keys = [x for x in form.getlist("ids") if "|" in x]
    if not keys:
        flash(request, "未选择任何题目", "err")
        return RedirectResponse("/ke/review", status_code=303)
    n = 0
    for x in keys:
        group, ke_id = x.split("|", 1)
        try:
            review_one(db, group=group, ke_id=ke_id, conclusion=conclusion,
                       comment=comment, reviewer=user["username"])
            n += 1
        except HTTPException:
            continue
    flash(request, f"已批量复核 {n} 条")
    return RedirectResponse("/ke/review", status_code=303)
