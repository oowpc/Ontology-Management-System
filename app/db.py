"""数据库引擎与会话。SQLite / PostgreSQL 只换 DB_URL。

结构迁移由 Alembic 负责（migrations/，M28 起）：
- 新库：`alembic upgrade head` 建全部表
- 既有库（有业务表、无 alembic_version）：自动 stamp 到基线版本再 upgrade，无需手工干预
SQLite 连接启用 WAL + busy_timeout（P2 #10：定时体检 / 多用户 / Agent 并发下减少锁冲突）。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_REVISION = "72c8a39d3507"   # migrations/versions/ 下的基线（全表现行结构）


class Base(DeclarativeBase):
    pass


def _sqlite_pragma(dbapi_conn, _record):
    """WAL + busy_timeout（内存库跳过 journal 设置）。"""
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA journal_mode=WAL")
    finally:
        cur.close()


def _make_engine():
    url = settings.db_url
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url.startswith("sqlite:///"):
            p = url[len("sqlite:///"):]
            if p != ":memory:":
                parent = Path(p).expanduser().parent
                if str(parent) not in ("", "."):
                    parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite") and ":memory:" not in url:
        event.listen(eng, "connect", _sqlite_pragma)
    return eng


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _alembic_config():
    from alembic.config import Config
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.db_url)
    return cfg


def init_db():
    from . import models  # noqa: F401  确保表已注册
    from alembic import command
    cfg = _alembic_config()
    tables = set(inspect(engine).get_table_names())
    if "alembic_version" not in tables and "concepts" in tables:
        # 既有库（Alembic 引入前建的）：结构已是基线现状，直接标记，不重建
        command.stamp(cfg, BASELINE_REVISION)
    command.upgrade(cfg, "head")
