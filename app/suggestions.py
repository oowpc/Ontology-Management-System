"""KEOL/agent 本体建议包：创建、审核、应用到全局本体。"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import log_change
from .importer import import_payload
from .models import Concept, OntologyIndividual, OntologyRule, OntologySuggestion, Operator


def _counts(package: dict, refs: dict) -> dict:
    return {
        "concepts": len(package.get("concepts") or []),
        "operators": len(package.get("operators") or []),
        "rules": len(package.get("rules") or []),
        "individuals": len(package.get("individuals") or []),
        "ref_concepts": len((refs or {}).get("concepts") or {}),
        "ref_operators": len((refs or {}).get("operators") or {}),
    }


def suggestion_dict(s: OntologySuggestion) -> dict:
    return {
        "id": s.id, "source": s.source, "origin": s.origin, "title": s.title,
        "status": s.status, "summary": s.summary or {}, "stats": s.stats,
        "note": s.note, "error": s.error, "created_by": s.created_by,
        "reviewed_by": s.reviewed_by, "reviewed_at": s.reviewed_at,
        "applied_at": s.applied_at, "created_at": s.created_at,
        "package": s.package, "refs": s.refs,
    }


def create_suggestion(db: Session, package: dict, refs: dict, actor: str,
                      *, title: str = "", origin: str = "keol",
                      source: str = "keol", note: str = "") -> OntologySuggestion:
    if not isinstance(package, dict):
        raise HTTPException(400, "package 必须是 JSON 对象")
    if not isinstance(refs, dict):
        raise HTTPException(400, "refs 必须是 JSON 对象")
    if not any(package.get(k) for k in ("concepts", "operators", "rules", "individuals")):
        raise HTTPException(400, "建议包为空")
    s = OntologySuggestion(
        source=source, origin=origin, title=title or f"{source} 建议",
        package=package, refs=refs, summary=_counts(package, refs),
        note=note, created_by=actor,
    )
    db.add(s)
    db.flush()
    log_change(db, "ontology_suggestion", s.id, "create", None,
               {"title": s.title, "summary": s.summary}, actor)
    db.commit()
    db.refresh(s)
    return s


def _map_term(term, ref_concepts: dict, ref_operators: dict,
              concept_ids: dict[str, int], operator_ids: dict[tuple[str, str], int]):
    if isinstance(term, str):
        return term
    if not isinstance(term, dict):
        raise ValueError("项必须是变量/对象")
    if "operator" in term:
        ref = str(term.get("operator") or "")
        if ref in ref_operators:
            spec = ref_operators[ref] or {}
            key = (str(spec.get("name") or ""), str(spec.get("subject") or ""))
            oid = operator_ids.get(key)
            if oid is None:
                raise ValueError(f"算子引用不存在：{key}")
            ref = f"oms-op-{oid}"
        args = []
        for arg in term.get("arguments") or []:
            args.append(_map_term(arg, ref_concepts, ref_operators, concept_ids, operator_ids))
        return {"operator": ref, "arguments": args}
    if "symbol" in term:
        ref = term.get("belong_concepts")
        def one(r):
            r = str(r)
            if r in ref_concepts:
                name = ref_concepts[r]
                cid = concept_ids.get(str(name))
                if cid is None:
                    raise ValueError(f"概念引用不存在：{name}")
                return f"oms-c-{cid}"
            return r
        if isinstance(ref, list):
            return {"symbol": term.get("symbol"), "belong_concepts": [one(r) for r in ref]}
        return {"symbol": term.get("symbol"), "belong_concepts": one(ref)}
    raise ValueError("未知项形状")


def _map_node(node, ref_concepts, ref_operators, concept_ids, operator_ids):
    if not isinstance(node, dict):
        raise ValueError("规则节点必须是 JSON 对象")
    if "connective" in node:
        return {
            "formula_left": _map_node(node["formula_left"], ref_concepts, ref_operators,
                                      concept_ids, operator_ids),
            "connective": node.get("connective"),
            "formula_right": _map_node(node["formula_right"], ref_concepts, ref_operators,
                                       concept_ids, operator_ids)
            if node.get("formula_right") is not None else None,
        }
    if "lhs" not in node:
        raise ValueError("规则节点缺 lhs/connective")
    out = {"lhs": _map_term(node["lhs"], ref_concepts, ref_operators,
                            concept_ids, operator_ids), "rhs": None}
    if node.get("rhs") is not None:
        out["rhs"] = _map_term(node["rhs"], ref_concepts, ref_operators,
                               concept_ids, operator_ids)
    return out


def approve_suggestion(db: Session, suggestion: OntologySuggestion, actor: str) -> dict:
    if suggestion.status != "pending":
        raise HTTPException(400, f"建议状态为 {suggestion.status}，不能审批")
    pkg = suggestion.package or {}
    refs = suggestion.refs or {}
    ref_concepts = refs.get("concepts") or {}
    ref_operators = refs.get("operators") or {}
    try:
        first = import_payload(db, {
            "concepts": pkg.get("concepts") or [],
            "operators": pkg.get("operators") or [],
        }, actor, source=f"suggestion:{suggestion.id}")
        concepts = {c.name: c.id for c in db.scalars(select(Concept)).all()}
        operators = {(o.name, db.get(Concept, o.subject_concept_id).name
                      if db.get(Concept, o.subject_concept_id) else ""): o.id
                     for o in db.scalars(select(Operator)).all()}
        mapped_rules = []
        skipped_rules = 0
        for r in pkg.get("rules") or []:
            try:
                body = _map_node(r["body"], ref_concepts, ref_operators,
                                 concepts, operators) if r.get("body") is not None else None
                head = [_map_node(n, ref_concepts, ref_operators, concepts, operators)
                        for n in (r.get("head") or [])]
                if not head:
                    raise ValueError("head 为空")
                mapped_rules.append({
                    "name": r.get("name") or r.get("canonical_name"),
                    "description": r.get("description") or "",
                    "body": body, "head": head,
                    "sources": r.get("sources") or [], "supply": r.get("supply") or {},
                    "domain": r.get("domain") or "KEOL", "status": r.get("status") or "approved",
                })
            except (KeyError, ValueError):
                skipped_rules += 1
        mapped_ind = []
        skipped_ind = 0
        for i in pkg.get("individuals") or []:
            name = i.get("name") or i.get("canonical_name")
            cname = i.get("concept")
            if not name or cname not in concepts:
                skipped_ind += 1
                continue
            mapped_ind.append({
                "name": name, "concept": cname,
                "description": i.get("description") or "",
                "sources": i.get("sources") or [], "supply": i.get("supply") or {},
                "domain": i.get("domain") or "KEOL", "status": i.get("status") or "approved",
            })
        second = {"rules_created": 0, "rules_updated": 0, "rules_unchanged": 0,
                  "individuals_created": 0, "individuals_updated": 0,
                  "individuals_unchanged": 0}
        if mapped_rules or mapped_ind:
            second = import_payload(db, {"rules": mapped_rules, "individuals": mapped_ind},
                                    actor, source=f"suggestion:{suggestion.id}")
        stats = {**first, **second, "skipped_rules": skipped_rules,
                 "skipped_individuals": skipped_ind}
        suggestion.status = "approved"
        suggestion.reviewed_by = actor
        suggestion.reviewed_at = datetime.now()
        suggestion.applied_at = datetime.now()
        suggestion.stats = stats
        suggestion.error = ""
        log_change(db, "ontology_suggestion", suggestion.id, "approve",
                   None, {"stats": stats}, actor)
        db.commit()
        return stats
    except Exception as e:
        db.rollback()
        suggestion.status = "failed"
        suggestion.reviewed_by = actor
        suggestion.reviewed_at = datetime.now()
        suggestion.error = str(e)[:2000]
        db.commit()
        raise HTTPException(400, f"应用建议失败：{e}") from e


def reject_suggestion(db: Session, suggestion: OntologySuggestion,
                      actor: str, note: str = "") -> None:
    if suggestion.status != "pending":
        raise HTTPException(400, f"建议状态为 {suggestion.status}，不能驳回")
    suggestion.status = "rejected"
    suggestion.reviewed_by = actor
    suggestion.reviewed_at = datetime.now()
    suggestion.note = (suggestion.note + "\n" + note).strip()
    log_change(db, "ontology_suggestion", suggestion.id, "reject", None,
               {"note": note}, actor)
    db.commit()
