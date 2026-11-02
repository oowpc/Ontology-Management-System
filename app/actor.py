"""当前请求的 agent token 上下文（审计用）。"""
from contextvars import ContextVar

actor_token: ContextVar[str | None] = ContextVar("actor_token", default=None)
