"""规则校验：结构 + 概念/算子引用 + 基础 arity。"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Concept, OntologyRule, Operator
from .types import ValidationResult

_STATUS = {"draft", "pending", "approved", "disabled"}
_CONNECTIVES = {"AND", "OR", "NOT", "IMPLIES", "IFF"}
_OID = re.compile(r"^oms-op-(\d+)$")
_CID = re.compile(r"^oms-c-(\d+)$")


def _get(rule, name, default=None):
    if isinstance(rule, dict):
        return rule.get(name, default)
    return getattr(rule, name, default)


def _concept_ref(db: Session, ref, out: ValidationResult, label: str) -> None:
    if isinstance(ref, list):
        for item in ref:
            _concept_ref(db, item, out, label)
        return
    if ref is None or ref == "":
        out.warn("missing_ref", f"{label}: 缺少概念引用")
        return
    s = str(ref)
    m = _CID.match(s)
    if m:
        if db.get(Concept, int(m.group(1))) is None:
            out.error("unknown_concept", f"{label}: 概念引用不存在 {s}")
        return
    c = db.scalar(select(Concept).where(Concept.name == s))
    if c is None:
        out.warn("unknown_concept", f"{label}: 概念「{s}」不在当前库")


def _operator_ref(db: Session, ref, args, out: ValidationResult, label: str) -> None:
    if not isinstance(args, list):
        out.error("bad_arguments", f"{label}: arguments 必须是数组")
        return
    if ref is None or ref == "":
        out.error("missing_operator", f"{label}: 缺少 operator 引用")
        return
    s = str(ref)
    m = _OID.match(s)
    op = None
    if m:
        op = db.get(Operator, int(m.group(1)))
        if op is None:
            out.error("unknown_operator", f"{label}: 算子引用不存在 {s}")
            return
    else:
        candidates = db.scalars(select(Operator).where(Operator.name == s)).all()
        if not candidates:
            out.warn("unknown_operator", f"{label}: 算子「{s}」不在当前库")
            return
        op = candidates[0]
    expected = 1 + len(op.param_types or [])
    if len(args) != expected:
        out.error("arity_mismatch",
                  f"{label}: 算子「{op.name}」实参 {len(args)} 个，签名要求 {expected} 个")
    for i, arg in enumerate(args):
        _term(db, arg, out, f"{label}.arguments[{i}]")


def _term(db: Session, term, out: ValidationResult, label: str) -> None:
    if isinstance(term, str):
        return
    if not isinstance(term, dict):
        out.error("bad_term", f"{label}: 项必须是变量/对象")
        return
    if "operator" in term:
        _operator_ref(db, term.get("operator"), term.get("arguments"), out, label)
        return
    if "symbol" in term:
        _concept_ref(db, term.get("belong_concepts"), out, label)
        return
    out.error("bad_term", f"{label}: 未知识别项形状")


def _node(db: Session, node, out: ValidationResult, label: str) -> None:
    if not isinstance(node, dict):
        out.error("bad_node", f"{label}: 节点必须是 JSON 对象")
        return
    if "connective" in node:
        if node.get("connective") not in _CONNECTIVES:
            out.error("bad_connective", f"{label}: 非法连接词 {node.get('connective')!r}")
        if "formula_left" not in node:
            out.error("missing_formula_left", f"{label}: Formula 缺 formula_left")
        else:
            _node(db, node["formula_left"], out, f"{label}.formula_left")
        if node.get("formula_right") is not None:
            _node(db, node["formula_right"], out, f"{label}.formula_right")
        return
    if "lhs" not in node:
        out.error("bad_node", f"{label}: 缺 lhs/connective")
        return
    _term(db, node.get("lhs"), out, f"{label}.lhs")
    if node.get("rhs") is not None:
        _term(db, node.get("rhs"), out, f"{label}.rhs")


def validate_rule(db: Session, rule: OntologyRule | dict, *, exclude_id: int | None = None,
                  stage: str = "") -> ValidationResult:
    if exclude_id is None:
        exclude_id = _get(rule, "id")
    name = str(_get(rule, "name") or "").strip()
    status = str(_get(rule, "status") or "approved").strip()
    head = _get(rule, "head")
    body = _get(rule, "body")
    out = ValidationResult("rule", name, object_id=_get(rule, "id"),
                           stage=stage, engine="oms-rule-v1")
    out.metadata = {"status": status}
    if not name:
        out.error("missing_name", "规则名称不能为空")
    if len(name) > 128:
        out.error("name_too_long", "规则名称超过 128 字符")
    if status not in _STATUS:
        out.error("bad_status", f"非法状态：{status}")
    if body is not None and not isinstance(body, dict):
        out.error("bad_body", "body 必须是对象或 null")
    if not isinstance(head, list):
        out.error("bad_head", "head 必须是数组")
        head = []
    if status == "approved" and not head:
        out.error("empty_head", "approved 规则的 head 不能为空")
    q = select(OntologyRule).where(OntologyRule.name == name)
    if exclude_id is not None:
        q = q.where(OntologyRule.id != exclude_id)
    if name and db.scalar(q):
        out.error("duplicate_name", f"规则「{name}」已存在")
    if body is not None:
        _node(db, body, out, "body")
    for i, node in enumerate(head):
        _node(db, node, out, f"head[{i}]")
    return out
