"""数据库引擎与会话。SQLite / PostgreSQL 只换 DB_URL。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


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
    return create_engine(url, **kwargs)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from . import models  # noqa: F401  确保表已注册

    Base.metadata.create_all(engine)
    _migrate()


def _migrate():
    """轻量迁移：给已有库补后加的列（create_all 不会改已有表）。"""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "concepts" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("concepts")}
        if "domain" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE concepts ADD COLUMN domain VARCHAR(64) DEFAULT '通用' NOT NULL"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_concepts_domain ON concepts (domain)"))
    _migrate_add_columns()


_NEW_COLUMNS = {  # (表, 列): DDL 类型 —— 已有库补列在此登记（create_all 不会改已有表）
    ("users", "group_name"): "VARCHAR(64)",
    ("ke_tasks", "article_code"): "VARCHAR(32)",
    ("gold_items", "validation"): "JSON",
}


def _migrate_add_columns():
    """轻量迁移：为已有库补新增列。"""
    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            for (table, col), ddl in _NEW_COLUMNS.items():
                existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
                if existing and col not in existing:  # 表不存在时由 create_all 创建
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    else:
        from sqlalchemy import text
        with engine.begin() as conn:
            for (table, col), ddl in _NEW_COLUMNS.items():
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}"))
