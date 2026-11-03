"""规则静态一致性：引用、重复、依赖环。"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import OntologyRule, Operator
from .validation.rules import validate_rule

_OP = re.compile(r"^oms-op-(\d+)$")


def _walk(node, operators: set[int], concepts: set[str], labels: set[str]) -> None:
    if not isinstance(node, dict):
        return
    if "connective" in node:
        _walk(node.get("formula_left"), operators, concepts, labels)
        _walk(node.get("formula_right"), operators, concepts, labels)
        return
    for side in ("lhs", "rhs"):
        term = node.get(side)
        _walk_term(term, operators, concepts, labels)


def _walk_term(term, operators: set[int], concepts: set[str], labels: set[str]) -> None:
    if isinstance(term, str):
        return
    if not isinstance(term, dict):
        return
    if "operator" in term:
        ref = str(term.get("operator") or "")
        m = _OP.match(ref)
        if m:
            operators.add(int(m.group(1)))
        else:
            labels.add(ref)
        for arg in term.get("arguments") or []:
            _walk_term(arg, operators, concepts, labels)
        return
    if "symbol" in term:
        ref = term.get("belong_concepts")
        for x in (ref if isinstance(ref, list) else [ref]):
            if x:
                concepts.add(str(x))


def check_rules(db: Session) -> dict:
    rows = db.scalars(select(OntologyRule).order_by(OntologyRule.id)).all()
    errors: list[dict] = []
    warnings: list[dict] = []
    sign: dict[str, int] = {}
    details: list[dict] = []
    for r in rows:
        result = validate_rule(db, r, stage="consistency")
        for x in result.errors:
            errors.append({"rule_id": r.id, "rule": r.name, **x})
        for x in result.warnings:
            warnings.append({"rule_id": r.id, "rule": r.name, **x})
        ops: set[int] = set()
        concepts: set[str] = set()
        labels: set[str] = set()
        _walk(r.body, ops, concepts, labels)
        head_ops: set[int] = set()
        for n in r.head or []:
            _walk(n, head_ops, concepts, labels)
        norm = json.dumps({"body": r.body, "head": r.head},
                          ensure_ascii=False, sort_keys=True)
        if norm in sign:
            warnings.append({"rule_id": r.id, "rule": r.name, "kind": "duplicate_rule",
                             "message": f"与规则 #{sign[norm]} 的 body/head 完全相同"})
        else:
            sign[norm] = r.id
        details.append({"id": r.id, "name": r.name, "body_ops": sorted(ops),
                        "head_ops": sorted(head_ops), "unknown_refs": sorted(labels)})

    # 以 head 算子 -> body 算子建依赖边；环表示规则之间循环支撑。
    graph: dict[int, set[int]] = defaultdict(set)
    for d in details:
        for h in d["head_ops"]:
            graph[h].update(d["body_ops"])
    color: dict[int, int] = {}
    cycles: list[list[int]] = []

    def dfs(n: int, path: list[int]) -> None:
        color[n] = 1
        path.append(n)
        for nxt in graph.get(n, ()):
            if color.get(nxt) == 1:
                i = path.index(nxt) if nxt in path else 0
                cycles.append(path[i:] + [nxt])
            elif color.get(nxt, 0) == 0:
                dfs(nxt, path)
        path.pop()
        color[n] = 2

    for n in list(graph):
        if color.get(n, 0) == 0:
            dfs(n, [])
    for cycle in cycles[:20]:
        errors.append({"rule_id": None, "rule": "", "kind": "rule_cycle",
                       "message": "规则依赖环：" + " -> ".join(f"oms-op-{x}" for x in cycle)})
    return {"ok": not errors, "rules": len(rows),
            "errors": errors, "warnings": warnings,
            "cycles": cycles, "details": details}
