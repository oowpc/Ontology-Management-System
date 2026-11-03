"""KE/GOLD 校验适配器：复用 Syntastic + OMS 现有校验口径。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..ke import ke_validate
from .types import ValidationResult


def _norm(items: list | None, severity: str) -> list[dict]:
    out = []
    for x in items or []:
        item = dict(x)
        item.setdefault("severity", severity)
        out.append(item)
    return out


def validate_gold_item(db: Session, item, *, stage: str = "") -> ValidationResult:
    raw = ke_validate.validate_item(db, item)
    key = f"{item.article_code}/{item.ke_id}" if getattr(item, "article_code", None) else str(item.id)
    out = ValidationResult("gold_item", key, object_id=getattr(item, "id", None),
                           stage=stage, engine=str(raw.get("syntax_engine") or raw.get("engine") or "ke"),
                           ok=bool(raw.get("ok")), checked_at=raw.get("validated_at") or "",
                           metadata={"counts": raw.get("stats") or {}})
    out.errors = _norm(raw.get("errors"), "error")
    out.warnings = _norm(raw.get("warnings"), "warning")
    out.ok = not out.errors
    return out
