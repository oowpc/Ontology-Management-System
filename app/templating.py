"""Jinja2 实例与模板全局量。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import settings

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_DIR / "templates")


def flash(request: Request, message: str, kind: str = "ok") -> None:
    """一次性消息：存 session，下次页面渲染消费即清（替代 ?ok=/?err= 拼 URL——
    刷新不重现、链接不带假消息。base.html 同时兼容旧的 query 传法）。"""
    request.session.setdefault("_flash", []).append({"kind": kind, "msg": message})

STATUS_LABELS = {
    "draft": "草稿",
    "pending": "待审",
    "approved": "已入库",
    "disabled": "停用",
}
ROLE_LABELS = {"annotator": "标注员", "reviewer": "审核员", "admin": "管理员"}
TMP_STATUS_LABELS = {"open": "待审中", "resolved": "已通过", "merged": "已合并"}
SUB_STATUS_LABELS = {"pending": "待审", "approved": "已通过", "rejected": "已驳回", "merged": "已合并"}
ACTION_LABELS = {"create": "创建", "update": "修改", "status": "状态变更",
                 "merge": "合并", "rollback": "回滚", "import": "导入"}


def fmt_dt(value) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        return value[:16]
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
templates.env.globals["ROLE_LABELS"] = ROLE_LABELS
templates.env.globals["TMP_STATUS_LABELS"] = TMP_STATUS_LABELS
templates.env.globals["SUB_STATUS_LABELS"] = SUB_STATUS_LABELS
templates.env.globals["ACTION_LABELS"] = ACTION_LABELS
templates.env.globals["KEOL_CONSOLE_URL"] = settings.keol_console_url
templates.env.filters["dt"] = fmt_dt


def dump_json(value) -> str:
    """JSON 展示（保留中文、转义 HTML，用于历史快照 diff）。"""
    import json as _json
    return (_json.dumps(value, ensure_ascii=False)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


templates.env.filters["dj"] = dump_json
