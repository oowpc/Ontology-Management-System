"""数据库引擎与会话。SQLite / PostgreSQL 只换 DB_URL。

结构迁移由 Alembic 负责（migrations/，M28 起）：
- 新库：`alembic upgrade head` 建全部表
- 既有库（有业务表、无 alembic_version）：自动 stamp 到基线版本再 upgrade，无需手工干预
SQLite 连接启用 WAL + busy_timeout（P2 #10：定时体检 / 多用户 / Agent 并发下减少锁冲突）。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker, with_loader_criteria

from .config import settings
from .library_context import DEFAULT_LIBRARY_ID, current_library_id

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


class LibrarySession(Session):
    """db.get 也会按当前库校验，避免跨库按 ID 读取。"""

    def get(self, entity, ident, **kwargs):
        obj = super().get(entity, ident, **kwargs)
        if obj is not None and hasattr(entity, "library_id"):
            lib = current_library_id.get()
            if lib is not None and getattr(obj, "library_id", None) != lib:
                return None
        return obj


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                            class_=LibrarySession)


def _library_classes():
    # 延迟 import，避免 db <-> models 循环
    from . import models
    return (models.Concept, models.ConceptParent, models.Operator, models.OperatorInput,
            models.LibraryMember, models.OntologySource,
            models.ImportBatch, models.AnnotationBatch, models.AnnotationMember,
            models.AnnotationUnit, models.AnnotationEvidence, models.CoAnnotation,
            models.CoAlignment, models.KeAnnotation, models.GoldKe,
            models.OntologyVersion, models.OntologySuggestion, models.CoMergeLog,
            models.AnnotationCrossReview, models.Nl2keRun, models.DatamakerJob, models.QueryRun, models.OntologyRule,
            models.OntologyIndividual, models.HealthReport)


@event.listens_for(Session, "do_orm_execute")
def _apply_library_scope(state):
    """给所有 ORM SELECT 自动加 library_id 条件；维护脚本可 library_scope(None) 关闭。"""
    if not state.is_select:
        return
    lib = current_library_id.get()
    if lib is None:
        return
    for cls in _library_classes():
        state.statement = state.statement.options(
            with_loader_criteria(cls, cls.library_id == lib, include_aliases=True))


@event.listens_for(Session, "before_flush")
def _stamp_library(_session, _flush_context, _instances):
    lib = current_library_id.get()
    if lib is None:
        lib = DEFAULT_LIBRARY_ID
    for obj in _session.new:
        if hasattr(type(obj), "library_id") and getattr(obj, "library_id", None) is None:
            obj.library_id = lib


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
