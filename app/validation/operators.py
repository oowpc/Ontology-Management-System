"""算子（method）校验：主体/owner、类型闭包、签名、重名。"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Concept, Operator
from .types import ValidationResult

_BUILTIN = {"string", "number", "bool", "date", "timeinterval", "real", "name", "concept"}
_SET = re.compile(r"^(?:Set|set)\s*[\[(]\s*(.+?)\s*[\])]$")


def _get(op, name, default=None):
    if isinstance(op, dict):
        return op.get(name, default)
    return getattr(op, name, default)


def _type_ok(db: Session, raw: str, out: ValidationResult, label: str) -> None:
    value = (raw or "").strip()
    if not value:
        out.error("empty_type", f"{label}: 类型为空")
        return
    m = _SET.match(value)
    if m:
        _type_ok(db, m.group(1), out, label)
        return
    if value.lower() in _BUILTIN:
        return
    c = db.scalar(select(Concept).where(Concept.name == value))
    if c is None:
        out.warn("unknown_type", f"{label}: 类型「{value}」不在当前库（可按 warning 先入库）")


def validate_operator(db: Session, op: Operator | dict, *, exclude_id: int | None = None,
                      stage: str = "") -> ValidationResult:
    if exclude_id is None:
        exclude_id = _get(op, "id")
    name = str(_get(op, "name") or "").strip()
    subject = _get(op, "subject_concept_id")
    owner = _get(op, "owner_concept_id")
    params = _get(op, "param_types") or []
    ret = str(_get(op, "return_type") or "").strip()
    out = ValidationResult("operator", name, object_id=_get(op, "id"),
                           stage=stage, engine="oms-operator-v1")
    out.metadata = {"params": len(params) if isinstance(params, list) else None}
    if not name:
        out.error("missing_name", "算子名称不能为空")
    if len(name) > 128:
        out.error("name_too_long", "算子名称超过 128 字符")
    if not isinstance(params, list):
        out.error("bad_params", "param_types 必须是数组")
        params = []
    if not ret:
        out.error("empty_return_type", "返回类型不能为空")
    try:
        subject_id = int(subject) if subject is not None else None
    except (TypeError, ValueError):
        subject_id = None
    try:
        owner_id = int(owner) if owner is not None else None
    except (TypeError, ValueError):
        owner_id = None
    if subject_id is None or db.get(Concept, subject_id) is None:
        out.error("missing_subject", f"主体概念不存在：{subject}")
    if owner is not None and (owner_id is None or db.get(Concept, owner_id) is None):
        out.error("missing_owner", f"所属概念不存在：{owner}")
    if name and subject_id is not None:
        q = select(Operator).where(Operator.name == name,
                                   Operator.subject_concept_id == subject_id)
        if exclude_id is not None:
            q = q.where(Operator.id != exclude_id)
        if db.scalar(q):
            out.error("duplicate_operator", "同一主体概念下算子名重复")
    if ret:
        _type_ok(db, ret, out, "return_type")
    for i, t in enumerate(params):
        _type_ok(db, str(t), out, f"param_types[{i}]")
    return out
