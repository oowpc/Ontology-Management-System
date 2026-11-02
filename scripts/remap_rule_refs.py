"""把规则/个体的 OMS 实例 ID 从 source 导出重映射到 target 导出。

用法：
  python scripts/remap_rule_refs.py \
      --source ontology-old-portable.json \
      --target ontology-new-portable.json \
      --out rules-portable.json

输入必须是 `/api/export/portable` 导出（含 concept/operator id）。
输出只含 rules/individuals 与 warnings，可直接 POST /api/import/json。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _index(data: dict):
    concepts = {int(c["id"]): c["name"] for c in data.get("concepts") or [] if c.get("id") is not None}
    operators = {}
    for o in data.get("operators") or []:
        if o.get("id") is None:
            continue
        operators[int(o["id"])] = (o.get("name"), o.get("subject"))
    return concepts, operators


def _parse_ref(ref: str, prefix: str):
    if not isinstance(ref, str) or not ref.startswith(prefix):
        return None
    tail = ref[len(prefix):]
    return int(tail) if tail.isdigit() else None


def _map_term(term, source_c, source_o, target_c, target_o, warnings, label):
    if isinstance(term, str):
        return term
    if not isinstance(term, dict):
        warnings.append(f"{label}: 非法项")
        return term
    if "operator" in term:
        ref = str(term.get("operator") or "")
        oid = _parse_ref(ref, "oms-op-")
        if oid is not None and oid in source_o:
            key = source_o[oid]
            tid = target_o.get(key)
            if tid is None:
                warnings.append(f"{label}: 找不到目标算子 {key}")
            else:
                term = {**term, "operator": f"oms-op-{tid}"}
        elif oid is not None:
            warnings.append(f"{label}: 源算子不存在 {ref}")
        args = term.get("arguments") or []
        term = {**term, "arguments": [
            _map_term(a, source_c, source_o, target_c, target_o, warnings, label) for a in args]}
        return term
    if "symbol" in term:
        ref = term.get("belong_concepts")
        if isinstance(ref, list):
            out = []
            for r in ref:
                cid = _parse_ref(r, "oms-c-")
                if cid is not None and cid in source_c:
                    tid = target_c.get(source_c[cid])
                    if tid is None:
                        warnings.append(f"{label}: 找不到目标概念 {source_c[cid]}")
                        out.append(r)
                    else:
                        out.append(f"oms-c-{tid}")
                else:
                    out.append(r)
            return {**term, "belong_concepts": out}
        cid = _parse_ref(ref, "oms-c-")
        if cid is not None and cid in source_c:
            tid = target_c.get(source_c[cid])
            if tid is None:
                warnings.append(f"{label}: 找不到目标概念 {source_c[cid]}")
            else:
                return {**term, "belong_concepts": f"oms-c-{tid}"}
        elif cid is not None:
            warnings.append(f"{label}: 源概念不存在 {ref}")
        return term
    warnings.append(f"{label}: 未知项形状")
    return term


def _map_node(node, source_c, source_o, target_c, target_o, warnings, label):
    if not isinstance(node, dict):
        warnings.append(f"{label}: 非法节点")
        return node
    if "connective" in node:
        return {
            "formula_left": _map_node(node.get("formula_left"), source_c, source_o,
                                      target_c, target_o, warnings, label),
            "formula_right": _map_node(node.get("formula_right"), source_c, source_o,
                                        target_c, target_o, warnings, label)
            if node.get("formula_right") is not None else None,
            "connective": node.get("connective"),
        }
    if "lhs" in node:
        return {
            "lhs": _map_term(node["lhs"], source_c, source_o, target_c, target_o, warnings, label),
            "rhs": _map_term(node["rhs"], source_c, source_o, target_c, target_o, warnings, label)
            if node.get("rhs") is not None else None,
        }
    warnings.append(f"{label}: 未知节点")
    return node


def remap(source: dict, target: dict) -> dict:
    source_c, source_o = _index(source)
    target_c_id, target_o_id = _index(target)
    target_c = {name: cid for cid, name in target_c_id.items()}
    target_o = {key: oid for oid, key in target_o_id.items()}
    warnings = []
    rules = []
    for r in source.get("rules") or []:
        name = r.get("name") or r.get("canonical_name") or "?"
        rules.append({
            "name": name, "description": r.get("description") or "",
            "body": _map_node(r["body"], source_c, source_o, target_c, target_o,
                              warnings, f"规则 {name} body")
            if r.get("body") is not None else None,
            "head": [_map_node(n, source_c, source_o, target_c, target_o, warnings,
                               f"规则 {name} head") for n in r.get("head") or []],
            "sources": r.get("sources") or [], "supply": r.get("supply") or {},
            "domain": r.get("domain") or "KEOL", "status": r.get("status") or "approved",
        })
    individuals = []
    for i in source.get("individuals") or []:
        name = i.get("name") or i.get("canonical_name")
        concept = i.get("concept")
        if concept not in target_c:
            warnings.append(f"个体 {name}: 目标概念不存在 {concept}")
            continue
        individuals.append({
            "name": name, "concept": concept,
            "description": i.get("description") or "",
            "sources": i.get("sources") or [], "supply": i.get("supply") or {},
            "domain": i.get("domain") or "KEOL", "status": i.get("status") or "approved",
        })
    return {"version": 1, "rules": rules, "individuals": individuals, "warnings": warnings}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    source = json.loads(Path(args.source).read_text(encoding="utf-8"))
    target = json.loads(Path(args.target).read_text(encoding="utf-8"))
    out = remap(source, target)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"rules={len(out['rules'])} individuals={len(out['individuals'])} warnings={len(out['warnings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
