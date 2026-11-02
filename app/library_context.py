"""当前本体库上下文。

所有带 library_id 的 ORM 查询由 db.py 的全局事件自动加过滤；
请求中间件/脚本用 library_scope 设置当前库。None = 跨库维护模式（仅限迁移/管理脚本）。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

DEFAULT_LIBRARY_ID = 2          # co-baseline
DEFAULT_LIBRARY_KEY = "co-baseline"
LIBRARY_COOKIE = "ontology_library"

current_library_id: ContextVar[int | None] = ContextVar(
    "current_library_id", default=DEFAULT_LIBRARY_ID)


@contextmanager
def library_scope(library_id: int | None):
    token = current_library_id.set(library_id)
    try:
        yield
    finally:
        current_library_id.reset(token)


@contextmanager
def library_session(library_id: int | None):
    """显式库作用域的短会话；后台任务必须用它，避免落到默认库。"""
    from .db import SessionLocal
    with library_scope(library_id):
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()


def library_scoped(fn):
    """允许 service 函数接收显式 library_id；未传时保持原请求上下文行为。"""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        library_id = kwargs.pop("library_id", None)
        if library_id is None:
            return fn(*args, **kwargs)
        with library_scope(library_id):
            return fn(*args, **kwargs)
    return wrapper
