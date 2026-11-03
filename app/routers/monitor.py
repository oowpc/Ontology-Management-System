"""DataAgent Monitor 只读页面与代理 API。"""
from __future__ import annotations

from secrets import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import MonitorEvent
from ..monitor import MonitorUnavailable, dashboard as fetch_dashboard, health
from ..monitor_events import (STATUSES, create_issue_for_event, event_dict,
                              ingest, sync_dashboard)
from ..security import current_user, require_role
from ..templating import flash, templates

router = APIRouter(tags=["monitor"])


@router.get("/api/monitor/health")
def monitor_health(user: dict = Depends(require_role())):
    return health()


@router.get("/api/monitor/dashboard")
def monitor_dashboard(run_limit: int = Query(20, ge=1, le=100),
                      event_limit: int = Query(20, ge=1, le=100),
                      user: dict = Depends(require_role())):
    try:
        return fetch_dashboard(run_limit, event_limit)
    except MonitorUnavailable as e:
        raise HTTPException(503, str(e)) from e


class EventStatusIn(BaseModel):
    status: str


def _webhook_authorized(request: Request) -> bool:
    token = settings.dataagent_monitor_webhook_token
    if token:
        auth = request.headers.get("authorization", "")
        return auth.lower().startswith("bearer ") and compare_digest(
            auth[7:].strip(), token)
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "testclient"}


@router.post("/api/monitor/events")
def monitor_webhook(request: Request, payload: dict,
                    db: Session = Depends(get_db)):
    if not _webhook_authorized(request):
        raise HTTPException(401, "monitor webhook unauthorized")
    row, duplicate = ingest(db, payload)
    return {"id": row.id, "duplicate": duplicate,
            "status": row.status, "issue_id": row.issue_id}


@router.get("/api/monitor/events")
def monitor_events(status: str | None = None, severity: str | None = None,
                   page: int = 1, size: int = Query(50, ge=1, le=200),
                   db: Session = Depends(get_db), user: dict = Depends(require_role())):
    stmt = select(MonitorEvent)
    if status:
        stmt = stmt.where(MonitorEvent.status == status)
    if severity:
        stmt = stmt.where(MonitorEvent.severity == severity)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(MonitorEvent.id.desc())
                      .offset((max(1, page) - 1) * size).limit(size)).all()
    return {"items": [event_dict(x) for x in rows], "total": total,
            "page": page, "size": size}


@router.get("/api/monitor/events/{eid}")
def monitor_event(eid: int, db: Session = Depends(get_db),
                  user: dict = Depends(require_role())):
    row = db.get(MonitorEvent, eid)
    if row is None:
        raise HTTPException(404, "事件不存在")
    return event_dict(row)


@router.post("/api/monitor/events/{eid}/issue")
def monitor_event_issue(eid: int, db: Session = Depends(get_db),
                        user: dict = Depends(require_role("reviewer", "admin"))):
    row = db.get(MonitorEvent, eid)
    if row is None:
        raise HTTPException(404, "事件不存在")
    issue_id = create_issue_for_event(db, row, user["username"])
    return {"event": event_dict(row), "issue_id": issue_id}


@router.post("/api/monitor/events/{eid}/status")
def monitor_event_status(eid: int, data: EventStatusIn,
                         db: Session = Depends(get_db),
                         user: dict = Depends(require_role("reviewer", "admin"))):
    if data.status not in STATUSES - {"issue"}:
        raise HTTPException(400, "status 取值：triaged / ignored / resolved")
    row = db.get(MonitorEvent, eid)
    if row is None:
        raise HTTPException(404, "事件不存在")
    row.status = data.status
    db.commit()
    return event_dict(row)


@router.post("/api/monitor/sync")
def monitor_sync(db: Session = Depends(get_db),
                 user: dict = Depends(require_role("reviewer", "admin"))):
    try:
        data = fetch_dashboard(100, 100)
        res = sync_dashboard(db, data)
        return {"remote_events": len(data.get("events") or []), **res}
    except MonitorUnavailable as e:
        raise HTTPException(503, str(e)) from e


@router.post("/monitor/sync")
def monitor_sync_page(request: Request, db: Session = Depends(get_db),
                      user: dict = Depends(current_user)):
    if user.get("role") not in {"reviewer", "admin"}:
        raise HTTPException(403, "同步事件需审核员/管理员")
    try:
        data = fetch_dashboard(100, 100)
        res = sync_dashboard(db, data)
        flash(request, f"同步 {res['imported']} 条，重复 {res['duplicate']} 条")
    except MonitorUnavailable as e:
        flash(request, f"监控服务不可用：{e}", "err")
    return RedirectResponse("/monitor", status_code=303)


@router.get("/monitor")
def monitor_page(request: Request,
                 run_limit: int = Query(20, ge=1, le=100),
                 event_limit: int = Query(20, ge=1, le=100),
                 status: str | None = None,
                 db: Session = Depends(get_db),
                 user: dict = Depends(current_user)):
    data = None
    error = ""
    try:
        data = fetch_dashboard(run_limit, event_limit)
    except MonitorUnavailable as e:
        error = str(e)
    stmt = select(MonitorEvent).order_by(MonitorEvent.id.desc())
    if status:
        stmt = stmt.where(MonitorEvent.status == status)
    local_events = db.scalars(stmt.limit(300)).all()
    counts = {k: n for k, n in db.execute(select(MonitorEvent.status, func.count())
                                          .group_by(MonitorEvent.status)).all()}
    return templates.TemplateResponse(request=request, name="monitor.html", context={
        "request": request, "user": user, "data": data, "error": error,
        "run_limit": run_limit, "event_limit": event_limit,
        "local_events": local_events, "local_counts": counts,
        "status_filter": status,
    })


@router.post("/monitor/events/{eid}/issue")
def monitor_event_issue_page(eid: int, request: Request, db: Session = Depends(get_db),
                             user: dict = Depends(current_user)):
    if user.get("role") not in {"reviewer", "admin"}:
        raise HTTPException(403, "转 Issue 需审核员/管理员")
    row = db.get(MonitorEvent, eid)
    if row is None:
        raise HTTPException(404, "事件不存在")
    iid = create_issue_for_event(db, row, user["username"])
    flash(request, f"已转为 Issue #{iid}")
    return RedirectResponse("/monitor", status_code=303)


@router.post("/monitor/events/{eid}/status")
async def monitor_event_status_page(eid: int, request: Request,
                                    db: Session = Depends(get_db),
                                    user: dict = Depends(current_user)):
    if user.get("role") not in {"reviewer", "admin"}:
        raise HTTPException(403, "处理事件需审核员/管理员")
    form = await request.form()
    state = str(form.get("status") or "")
    if state not in STATUSES - {"issue"}:
        raise HTTPException(400, "非法状态")
    row = db.get(MonitorEvent, eid)
    if row is None:
        raise HTTPException(404, "事件不存在")
    row.status = state
    db.commit()
    flash(request, "事件状态已更新")
    return RedirectResponse("/monitor", status_code=303)
