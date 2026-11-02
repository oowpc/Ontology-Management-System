"""DataMaker：生成任务页、任务 API、日志与产物。"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DatamakerJob
from ..security import current_user
from ..templating import templates
from .. import datamaker as service

router = APIRouter(tags=["datamaker"])


class StartIn(BaseModel):
    db_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=128)
    n: int = Field(5, ge=1, le=100)
    workers: int = Field(1, ge=1, le=8)
    ke_types: str = Field("", max_length=512)
    resume: bool = False
    mode: str = "gen-ke"
    nl_source: str = "ke"
    cross_check: bool = False
    service_url: str = Field("", max_length=256)
    timeout_s: int | None = Field(None, ge=60, le=86400)


def _job_or_404(db: Session, lib, jid: int) -> DatamakerJob:
    job = db.get(DatamakerJob, jid)
    if job is None or job.library_id != lib.id:
        raise HTTPException(404, "生成任务不存在")
    return job


def _require_runner(user: dict):
    if user.get("role") not in ("reviewer", "admin"):
        raise HTTPException(403, "需要 reviewer 或 admin 权限")


@router.get("/datamaker")
def page(request: Request, db: Session = Depends(get_db),
         user: dict = Depends(current_user)):
    lib = getattr(request.state, "library", None)
    if lib is None:
        raise HTTPException(400, "请先选择本体库")
    jobs = db.scalars(select(DatamakerJob).where(DatamakerJob.library_id == lib.id)
                      .order_by(DatamakerJob.id.desc()).limit(50)).all()
    databases = []
    if lib.key == "archer":
        databases = [d for d in service.bridge.catalog(db) if d.get("has_sqlite")]
    return templates.TemplateResponse(request=request, name="datamaker.html", context={
        "request": request, "user": user, "library": lib,
        "jobs": [service.job_dict(j) for j in jobs],
        "stats": service.aggregate(jobs),
        "databases": databases,
        "models": service.MODELS,
        "default_model": service.LLM_MODEL,
        "max_active": service.MAX_ACTIVE,
        "modes": service.MODES,
        "nl_sources": service.NL_SOURCES,
        "service_url": service.SERVICE_URL,
        "timeout": service.TIMEOUT,
    })


@router.get("/datamaker/jobs/{jid}")
def job_page(jid: int, request: Request, db: Session = Depends(get_db),
             user: dict = Depends(current_user)):
    lib = getattr(request.state, "library", None)
    if lib is None:
        raise HTTPException(400, "请先选择本体库")
    job = _job_or_404(db, lib, jid)
    return templates.TemplateResponse(request=request, name="datamaker_job.html", context={
        "request": request, "user": user, "job": service.job_dict(job),
        "items": service.preview(job), "artifacts": service.artifacts(job),
        "log": service.log_tail(job),
        "quality": service.quality(job),
    })


@router.post("/api/datamaker/jobs")
def start(body: StartIn, request: Request, db: Session = Depends(get_db),
          user: dict = Depends(current_user)):
    _require_runner(user)
    lib = getattr(request.state, "library", None)
    if lib is None:
        raise HTTPException(400, "请先选择本体库")
    service_url = (body.service_url or service.SERVICE_URL).strip()
    u = urlparse(service_url)
    if u.scheme not in ("http", "https") or u.hostname not in ("127.0.0.1", "localhost"):
        raise HTTPException(400, "service_url 只允许本机地址")
    try:
        job = service.start_job(
            db, library=lib, db_id=body.db_id, model=body.model, n=body.n,
            workers=body.workers, ke_types=body.ke_types, resume=body.resume,
            actor=user["username"], mode=body.mode, nl_source=body.nl_source,
            cross_check=body.cross_check, service_url=service_url,
            timeout_s=body.timeout_s,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return service.job_dict(job)


@router.get("/api/datamaker/jobs")
def list_jobs(request: Request, db: Session = Depends(get_db),
              user: dict = Depends(current_user)):
    lib = getattr(request.state, "library", None)
    if lib is None:
        raise HTTPException(400, "请先选择本体库")
    rows = db.scalars(select(DatamakerJob).where(DatamakerJob.library_id == lib.id)
                      .order_by(DatamakerJob.id.desc()).limit(50)).all()
    return {"items": [service.job_dict(j) for j in rows]}


@router.get("/api/datamaker/jobs/{jid}")
def job_json(jid: int, request: Request, db: Session = Depends(get_db),
             user: dict = Depends(current_user)):
    lib = getattr(request.state, "library", None)
    if lib is None:
        raise HTTPException(400, "请先选择本体库")
    return service.job_dict(_job_or_404(db, lib, jid))


@router.post("/api/datamaker/jobs/{jid}/cancel")
def cancel(jid: int, request: Request, db: Session = Depends(get_db),
           user: dict = Depends(current_user)):
    _require_runner(user)
    lib = getattr(request.state, "library", None)
    if lib is None:
        raise HTTPException(400, "请先选择本体库")
    job = _job_or_404(db, lib, jid)
    if job.status not in ("queued", "running"):
        raise HTTPException(400, "任务已结束")
    service.cancel_job(db, job)
    return service.job_dict(job)


@router.post("/api/datamaker/jobs/{jid}/retry")
def retry(jid: int, request: Request, db: Session = Depends(get_db),
          user: dict = Depends(current_user)):
    _require_runner(user)
    lib = getattr(request.state, "library", None)
    if lib is None:
        raise HTTPException(400, "请先选择本体库")
    job = _job_or_404(db, lib, jid)
    try:
        new_job = service.retry_job(db, library=lib, job=job, actor=user["username"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return service.job_dict(new_job)


@router.get("/api/datamaker/jobs/{jid}/log")
def log(jid: int, request: Request, db: Session = Depends(get_db),
        user: dict = Depends(current_user)):
    lib = getattr(request.state, "library", None)
    if lib is None:
        raise HTTPException(400, "请先选择本体库")
    job = _job_or_404(db, lib, jid)
    return Response(service.log_tail(job, 30000), media_type="text/plain; charset=utf-8")


@router.get("/api/datamaker/jobs/{jid}/artifacts/{name}")
def artifact(jid: int, name: str, request: Request, db: Session = Depends(get_db),
             user: dict = Depends(current_user)):
    lib = getattr(request.state, "library", None)
    if lib is None:
        raise HTTPException(400, "请先选择本体库")
    job = _job_or_404(db, lib, jid)
    path = service.artifact_path(job, name)
    if path is None:
        raise HTTPException(404, "产物不存在")
    return FileResponse(path, filename=name)
