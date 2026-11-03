"""统一校验入口与结果留痕。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..models import ValidationResult as ValidationRow
from .ke import validate_gold_item
from .operators import validate_operator
from .rules import validate_rule
from .types import ValidationResult


def _record(db: Session, result: ValidationResult, actor: str) -> ValidationRow:
    row = ValidationRow(
        object_type=result.object_type, object_id=result.object_id,
        object_key=result.object_key, stage=result.stage,
        ok=result.ok, error_count=len(result.errors),
        warning_count=len(result.warnings), engine=result.engine,
        result=result.as_dict(), created_by=actor or "system",
    )
    db.add(row)
    db.flush()
    return row


def run(db: Session, object_type: str, obj, *, stage: str = "", actor: str = "system",
        exclude_id: int | None = None, enforce: bool | None = None,
        record: bool = True) -> ValidationResult:
    if object_type == "rule":
        result = validate_rule(db, obj, exclude_id=exclude_id, stage=stage)
    elif object_type == "operator":
        result = validate_operator(db, obj, exclude_id=exclude_id, stage=stage)
    elif object_type in {"gold_item", "ke"}:
        result = validate_gold_item(db, obj, stage=stage)
        result.object_type = "gold_item"
    else:
        raise HTTPException(400, f"不支持的校验对象：{object_type}")
    if record:
        _record(db, result, actor)
    if (settings.validation_enforce if enforce is None else enforce) and not result.ok:
        raise HTTPException(422, detail={
            "message": f"校验未通过：{len(result.errors)} 个错误",
            "validation": result.as_dict(),
        })
    return result


def revalidate(db: Session, object_type: str, obj, *, stage: str = "revalidate",
               actor: str = "system") -> ValidationResult:
    return run(db, object_type, obj, stage=stage, actor=actor, enforce=False)
