"""FastAPI 装配：中间件、路由、启动建表与种子。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .annotation.api import router as annotation_api_router
from .annotation.router import router as annotation_router
from .actor_middleware import ActorTokenMiddleware
from .config import ensure_secret_key, settings
from .csrf import CSRFMiddleware
from .db import SessionLocal, init_db
from .gold.items import router as gold_items_router
from .gold.router import router as gold_router
from .ke.review import router as ke_review_router
from .ke.router import router as ke_router
from .issues.router import router as issues_router
from .libraries import OntologyLibraryMiddleware
from .library_context import library_session
from .questions.router import router as questions_router
from .routers import (agent, auth, concepts, datamaker, graph, health, io, keol,
                      libraries, library, library_api, llm, nl2ke, operators, ops,
                      pages, paste, search, similarity, submissions, suggestions,
                      tmp, tokens, users, validation, monitor, ke_docs)
from .security import LoginRequired
from .seed import seed_if_empty
from .templating import templates

_scheduler = None


def _scheduled_health():
    """定时体检：每个本体库独立执行，不能依赖线程默认上下文。"""
    import logging
    from .healthcheck import run_health
    from .models import OntologyLibrary

    from sqlalchemy import select
    with library_session(None) as db:
        library_ids = [row.id for row in db.scalars(
            select(OntologyLibrary).order_by(OntologyLibrary.id))]
    for library_id in library_ids:
        try:
            with library_session(library_id) as db:
                run_health(db, "scheduled", library_id=library_id)
        except Exception:
            logging.getLogger("app").exception(
                "scheduled health failed for library %s", library_id)


def _scheduled_keol_sync():
    """定时检查 KEOL 源 hash 与差异；只记录，不自动审批。"""
    import logging
    from sqlalchemy import select
    from .keol_sync import check, record
    from .models import OntologyLibrary
    try:
        with library_session(None) as db:
            lib = db.scalar(select(OntologyLibrary).where(
                OntologyLibrary.key == settings.keol_sync_library))
            lib_id = lib.id if lib else None
        if lib_id is None:
            return
        with library_session(lib_id) as db:
            data = check(db)
            record(db, data, "scheduler")
    except Exception:
        logging.getLogger("app").exception("scheduled KEOL sync check failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    ensure_secret_key(settings.secret_key, settings.dev_mode)  # P0：默认密钥非调试拒绝启动
    init_db()
    seed_if_empty()
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(_scheduled_health, CronTrigger.from_crontab(settings.health_cron),
                           id="health", replace_existing=True)
        if settings.keol_sync_cron:
            _scheduler.add_job(_scheduled_keol_sync,
                               CronTrigger.from_crontab(settings.keol_sync_cron),
                               id="keol_sync", replace_existing=True)
        _scheduler.start()
    except Exception:
        _scheduler = None  # 定时任务不可用不阻断启动
    yield
    if _scheduler:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass  # 嵌套 lifespan（测试起多个 client）时调度器可能已被停


app = FastAPI(title="本体管理系统", version="0.1.0", lifespan=lifespan)
app.add_middleware(CSRFMiddleware)  # 后注册的 SessionMiddleware 成为外层：先解 session 再校 CSRF
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")
app.add_middleware(ActorTokenMiddleware)
app.add_middleware(OntologyLibraryMiddleware)

app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "未登录"}, status_code=401)
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """M30：页面路径的错误渲染成人看的页面；/api/ 保持 JSON 契约不变。"""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))
    return templates.TemplateResponse(
        request=request, name="error.html",
        context={"request": request, "user": request.session.get("user"),
                 "code": exc.status_code, "message": exc.detail},
        status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    import logging
    logging.getLogger("app").exception("未处理异常: %s %s", request.method, request.url.path)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "服务器内部错误"}, status_code=500)
    return templates.TemplateResponse(
        request=request, name="error.html",
        context={"request": request, "user": request.session.get("user"),
                 "code": 500, "message": "服务器内部错误，请稍后重试"},
        status_code=500)


app.include_router(auth.router)
app.include_router(libraries.router)
app.include_router(concepts.router)
app.include_router(operators.router)
app.include_router(library.router)
app.include_router(library_api.router)
app.include_router(suggestions.router)
app.include_router(similarity.router)
app.include_router(submissions.router)
app.include_router(tmp.router)
app.include_router(llm.router)
app.include_router(health.router)
app.include_router(io.router)
app.include_router(tokens.router)
app.include_router(users.router)
app.include_router(agent.router)
app.include_router(graph.router)
app.include_router(paste.router)
app.include_router(ke_router)
app.include_router(ke_review_router)
app.include_router(gold_router)
app.include_router(gold_items_router)
app.include_router(questions_router)
app.include_router(issues_router)
app.include_router(keol.router)
app.include_router(nl2ke.router)
app.include_router(datamaker.router)
app.include_router(annotation_router)
app.include_router(annotation_api_router)
app.include_router(search.router)
app.include_router(ops.router)
app.include_router(validation.router)
app.include_router(validation.page_router)
app.include_router(monitor.router)
app.include_router(ke_docs.router)
app.include_router(pages.router)

