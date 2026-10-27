"""导入导出：JSON / CSV。概念以「名称」为自然键（父类/合并目标/算子主体均按名称引用），
幂等 upsert：存在则更新差异字段，不存在则新建；重复导入不产生任何变化（验收5）。
字段名可用 field_map 重命名（导入时自动反向还原）。"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import concept_snapshot, log_change, snapshot
from .models import Concept, ConceptParent, ImportBatch, Operator

CONCEPT_FIELDS = ["name", "domain", "aliases", "definition", "parent", "parents",
                  "source_uri", "ontology_module", "status", "merged_into"]
OPERATOR_FIELDS = ["name", "subject", "owner", "param_types", "return_type",
                   "source_uri", "ontology_module", "status"]
VALID_STATUS = {"draft", "pending", "approved", "disabled"}
DEFAULT_DOMAIN = "通用"


def parse_field_map(raw: str | None) -> dict:
    if not raw or not raw.strip():
        return {}
    try:
        m = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "字段映射不是合法 JSON")
    if not isinstance(m, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in m.items()):
        raise HTTPException(400, "字段映射应为 {标准名: 导出名} 的对象")
    return m


# ---------------- 导出 ----------------

def export_payload(db: Session, field_map: dict | None = None) -> dict:
    fm = field_map or {}
    k = lambda key: fm.get(key, key)  # noqa: E731
    concepts = db.scalars(select(Concept).order_by(Concept.id)).all()
    names = {c.id: c.name for c in concepts}
    rows = db.execute(
        select(ConceptParent, Concept)
        .join(Concept, Concept.id == ConceptParent.parent_id)
        .order_by(ConceptParent.concept_id, ConceptParent.is_primary.desc(),
                  ConceptParent.position, Concept.name)
    ).all()
    parents_by: dict[int, list[str]] = {}
    for link, parent in rows:
        parents_by.setdefault(link.concept_id, []).append(parent.name)
    out_c = [{
        k("name"): c.name,
        k("domain"): c.domain or DEFAULT_DOMAIN,
        k("aliases"): c.aliases or [],
        k("definition"): c.definition or "",
        k("parent"): names.get(c.parent_id),
        k("parents"): parents_by.get(c.id, []),
        k("source_uri"): c.source_uri or "",
        k("ontology_module"): c.ontology_module or "",
        k("status"): c.status,
        k("merged_into"): names.get(c.merged_into_id),
    } for c in concepts]
    ops = db.scalars(select(Operator).order_by(Operator.id)).all()
    out_o = [{
        k("name"): o.name,
        k("subject"): names.get(o.subject_concept_id),
        k("owner"): names.get(o.owner_concept_id),
        k("param_types"): o.param_types or [],
        k("return_type"): o.return_type,
        k("source_uri"): o.source_uri or "",
        k("ontology_module"): o.ontology_module or "",
        k("status"): o.status,
    } for o in ops]
    return {"version": 1, "exported_at": datetime.now().isoformat(timespec="seconds"),
            "concepts": out_c, "operators": out_o}


def _to_csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return "﻿" + buf.getvalue()


def export_csv(db: Session, kind: str, field_map: dict | None = None) -> str:
    payload = export_payload(db, field_map)
    if kind == "concepts":
        fields = CONCEPT_FIELDS
        k = lambda key: (field_map or {}).get(key, key)  # noqa: E731
        rows = [[r[k("name")], r[k("domain")] or DEFAULT_DOMAIN, "|".join(r[k("aliases")]), r[k("definition")],
                 r[k("parent")] or "", "|".join(r[k("parents")] or []),
                 r[k("source_uri")] or "", r[k("ontology_module")] or "",
                 r[k("status")], r[k("merged_into")] or ""]
                for r in payload["concepts"]]
        return _to_csv([k(f) for f in fields], rows)
    if kind == "operators":
        fields = OPERATOR_FIELDS
        k = lambda key: (field_map or {}).get(key, key)  # noqa: E731
        rows = [[r[k("name")], r[k("subject")], r[k("owner")] or "",
                 "|".join(r[k("param_types")]), r[k("return_type")],
                 r[k("source_uri")] or "", r[k("ontology_module")] or "", r[k("status")]]
                for r in payload["operators"]]
        return _to_csv([k(f) for f in fields], rows)
    raise HTTPException(400, "kind 取值：concepts / operators")


# ---------------- 导入 ----------------

def _canon(row: dict, field_map: dict | None) -> dict:
    rev = {v: kk for kk, v in (field_map or {}).items()}
    return {rev.get(key, key): val for key, val in row.items()}


def _split_list(val) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    return [x.strip() for x in re.split(r"[,，、;；|]+", str(val)) if x.strip()]


def csv_to_payload(text: str, kind: str, field_map: dict | None = None) -> dict:
    reader = csv.DictReader(io.StringIO((text or "").lstrip("﻿")))
    rows = [dict(r) for r in reader]
    if kind == "concepts":
        return {"concepts": rows, "operators": []}
    if kind == "operators":
        return {"concepts": [], "operators": rows}
    raise HTTPException(400, "kind 取值：concepts / operators")


def import_payload(db: Session, payload: dict, actor: str, field_map: dict | None = None,
                   batch_id: str | None = None, source: str = "",
                   files: list | None = None) -> dict:
    rows_c = [_canon(r, field_map) for r in (payload.get("concepts") or [])]
    rows_o = [_canon(r, field_map) for r in (payload.get("operators") or [])]
    stats = {"concepts_created": 0, "concepts_updated": 0, "concepts_unchanged": 0,
             "operators_created": 0, "operators_updated": 0, "operators_unchanged": 0}
    if batch_id and db.scalar(select(ImportBatch).where(ImportBatch.batch_id == batch_id)):
        raise HTTPException(400, f"导入批次「{batch_id}」已存在")
    created_concept_ids: list[int] = []
    updated_concept_ids: list[int] = []
    created_operator_ids: list[int] = []
    updated_operator_ids: list[int] = []
    existing = {c.name: c for c in db.scalars(select(Concept)).all()}
    pre_existing_names = set(existing.keys())
    touched: dict[str, dict] = {}  # name -> before 快照（仅既有概念）
    created: list[Concept] = []

    def touch(c: Concept):
        if c.name in pre_existing_names and c.name not in touched:
            touched[c.name] = concept_snapshot(db, c)

    def row_parent_names(row: dict) -> list[str]:
        raw = row.get("parents")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            raw = row.get("parent")
        names = _split_list(raw)
        out, seen = [], set()
        for name in names:
            if name not in seen:
                seen.add(name)
                out.append(name)
        return out

    # 第一遍：概念本体（暂不挂父类，保证前向引用可解析）
    for row in rows_c:
        name = str(row.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "存在缺名称的概念行")
        status = str(row.get("status") or "approved").strip()
        if status not in VALID_STATUS:
            status = "approved"
        domain = str(row.get("domain") or DEFAULT_DOMAIN).strip() or DEFAULT_DOMAIN
        aliases = _split_list(row.get("aliases"))
        definition = str(row.get("definition") or "")
        source_uri = str(row.get("source_uri") or "").strip() or None
        ontology_module = str(row.get("ontology_module") or "").strip() or None
        c = existing.get(name)
        if c is None:
            c = Concept(name=name, domain=domain, aliases=aliases, definition=definition,
                        source_uri=source_uri, ontology_module=ontology_module,
                        status=status, created_by=actor, reviewed_by=actor)
            db.add(c)
            db.flush()
            existing[name] = c
            created.append(c)
            created_concept_ids.append(c.id)
            stats["concepts_created"] += 1
        else:
            if c.domain != domain:
                touch(c)
                c.domain = domain
            if (c.aliases or []) != aliases:
                touch(c)
                c.aliases = aliases
            if (c.definition or "") != definition:
                touch(c)
                c.definition = definition
            if (c.source_uri or None) != source_uri:
                touch(c)
                c.source_uri = source_uri
            if (c.ontology_module or None) != ontology_module:
                touch(c)
                c.ontology_module = ontology_module
            if c.status != status:
                touch(c)
                c.status = status

    # 第二遍：多父类与合并目标（按名称解析；全图校验循环继承）
    from .services import set_concept_parents
    for row in rows_c:
        name = str(row.get("name") or "").strip()
        c = existing[name]
        parent_names = row_parent_names(row)
        parent_ids = []
        for parent_name in parent_names:
            p = existing.get(parent_name)
            if not p:
                raise HTTPException(400, f"概念「{name}」的父类「{parent_name}」不存在")
            parent_ids.append(p.id)
        merged_name = str(row.get("merged_into") or "").strip() or None
        merged_id = None
        if merged_name:
            m = existing.get(merged_name)
            if not m:
                raise HTTPException(400, f"概念「{name}」的合并目标「{merged_name}」不存在")
            merged_id = m.id
        if set_concept_parents(db, c, parent_ids):
            touch(c)
        if c.merged_into_id != merged_id:
            touch(c)
            c.merged_into_id = merged_id

    stats["concepts_updated"] = len(touched)
    stats["concepts_unchanged"] = len(pre_existing_names) - len(touched)
    updated_concept_ids = [existing[name].id for name in touched]
    db.flush()
    for c in created:
        log_change(db, "concept", c.id, "create", None, concept_snapshot(db, c), actor)
    for name, before in touched.items():
        log_change(db, "concept", existing[name].id, "import", before,
                   concept_snapshot(db, existing[name]), actor)

    # 算子：按 (name, subject) upsert
    existing_ops = {(o.name, o.subject_concept_id): o for o in db.scalars(select(Operator)).all()}
    ops_touched = 0
    for row in rows_o:
        name = str(row.get("name") or "").strip()
        subject_name = str(row.get("subject") or "").strip()
        if not name or not subject_name:
            raise HTTPException(400, "算子行缺名称或主体概念")
        subj = existing.get(subject_name)
        if not subj:
            raise HTTPException(400, f"算子「{name}」的主体概念「{subject_name}」不存在")
        owner_name = str(row.get("owner") or "").strip() or None
        owner = existing.get(owner_name) if owner_name else None
        if owner_name and not owner:
            raise HTTPException(400, f"算子「{name}」的所属概念「{owner_name}」不存在")
        param_types = _split_list(row.get("param_types"))
        return_type = str(row.get("return_type") or "").strip()
        source_uri = str(row.get("source_uri") or "").strip() or None
        ontology_module = str(row.get("ontology_module") or "").strip() or None
        if not return_type:
            raise HTTPException(400, f"算子「{name}」缺返回类型")
        status = str(row.get("status") or "approved").strip()
        if status not in VALID_STATUS:
            status = "approved"
        o = existing_ops.get((name, subj.id))
        if o is None:
            o = Operator(name=name, subject_concept_id=subj.id,
                         owner_concept_id=owner.id if owner else None,
                         param_types=param_types, return_type=return_type,
                         source_uri=source_uri, ontology_module=ontology_module,
                         status=status, created_by=actor)
            db.add(o)
            db.flush()
            log_change(db, "operator", o.id, "create", None, snapshot(o), actor)
            created_operator_ids.append(o.id)
            stats["operators_created"] += 1
        else:
            before = snapshot(o)
            changed = False
            if (o.param_types or []) != param_types:
                o.param_types = param_types
                changed = True
            if o.return_type != return_type:
                o.return_type = return_type
                changed = True
            if o.status != status:
                o.status = status
                changed = True
            new_owner = owner.id if owner else None
            if o.owner_concept_id != new_owner:
                o.owner_concept_id = new_owner
                changed = True
            if (o.source_uri or None) != source_uri:
                o.source_uri = source_uri
                changed = True
            if (o.ontology_module or None) != ontology_module:
                o.ontology_module = ontology_module
                changed = True
            if changed:
                db.flush()
                log_change(db, "operator", o.id, "import", before, snapshot(o), actor)
                updated_operator_ids.append(o.id)
                ops_touched += 1
            else:
                stats["operators_unchanged"] += 1
    stats["operators_updated"] = ops_touched
    if batch_id:
        db.add(ImportBatch(
            batch_id=batch_id, source=source or "", actor=actor, status="completed",
            stats=stats, files=files or [],
            created_concept_ids=created_concept_ids,
            updated_concept_ids=updated_concept_ids,
            created_operator_ids=created_operator_ids,
            updated_operator_ids=updated_operator_ids,
            finished_at=datetime.now(),
        ))
    db.commit()
    return stats
