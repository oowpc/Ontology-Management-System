"""FastAPI 装配：中间件、路由、启动建表与种子。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import SessionLocal, init_db
from .gold.items import router as gold_items_router
from .gold.router import router as gold_router
from .ke.review import router as ke_review_router
from .ke.router import router as ke_router
from .issues.router import router as issues_router
from .questions.router import router as questions_router
from .routers import (agent, auth, concepts, graph, health, io, keol, llm,
                      operators, pages, paste, similarity, submissions, tmp,
                      tokens, users)
from .security import LoginRequired
from .seed import seed_if_empty

_scheduler = None


def _scheduled_health():
    """定时体检（HEALTH_CRON，默认每日 03:30）。"""
    from .healthcheck import run_health
    db = SessionLocal()
    try:
        run_health(db, "scheduled")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    init_db()
    seed_if_empty()
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(_scheduled_health, CronTrigger.from_crontab(settings.health_cron),
                           id="health", replace_existing=True)
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
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")

app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "未登录"}, status_code=401)
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


app.include_router(auth.router)
app.include_router(concepts.router)
app.include_router(operators.router)
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
app.include_router(pages.router)


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/concepts")
