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

from .audit import log_change, snapshot
from .models import Concept, Operator

CONCEPT_FIELDS = ["name", "aliases", "definition", "parent", "status", "merged_into"]
OPERATOR_FIELDS = ["name", "subject", "owner", "param_types", "return_type", "status"]
VALID_STATUS = {"draft", "pending", "approved", "disabled"}


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
    out_c = [{
        k("name"): c.name,
        k("aliases"): c.aliases or [],
        k("definition"): c.definition or "",
        k("parent"): names.get(c.parent_id),
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
        rows = [[r[k("name")], "|".join(r[k("aliases")]), r[k("definition")],
                 r[k("parent")] or "", r[k("status")], r[k("merged_into")] or ""]
                for r in payload["concepts"]]
        return _to_csv([k(f) for f in fields], rows)
    if kind == "operators":
        fields = OPERATOR_FIELDS
        k = lambda key: (field_map or {}).get(key, key)  # noqa: E731
        rows = [[r[k("name")], r[k("subject")], r[k("owner")] or "",
                 "|".join(r[k("param_types")]), r[k("return_type")], r[k("status")]]
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


def import_payload(db: Session, payload: dict, actor: str, field_map: dict | None = None) -> dict:
    rows_c = [_canon(r, field_map) for r in (payload.get("concepts") or [])]
    rows_o = [_canon(r, field_map) for r in (payload.get("operators") or [])]
    stats = {"concepts_created": 0, "concepts_updated": 0, "concepts_unchanged": 0,
             "operators_created": 0, "operators_updated": 0, "operators_unchanged": 0}
    existing = {c.name: c for c in db.scalars(select(Concept)).all()}
    pre_existing_names = set(existing.keys())
    touched: dict[str, dict] = {}  # name -> before 快照（仅既有概念）

    def touch(c: Concept):
        if c.name in pre_existing_names and c.name not in touched:
            touched[c.name] = snapshot(c)

    # 第一遍：概念本体（暂不挂父类，保证前向引用可解析）
    for row in rows_c:
        name = str(row.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "存在缺名称的概念行")
        status = str(row.get("status") or "approved").strip()
        if status not in VALID_STATUS:
            status = "approved"
        aliases = _split_list(row.get("aliases"))
        definition = str(row.get("definition") or "")
        c = existing.get(name)
        if c is None:
            c = Concept(name=name, aliases=aliases, definition=definition, status=status,
                        created_by=actor, reviewed_by=actor)
            db.add(c)
            db.flush()
            log_change(db, "concept", c.id, "create", None, snapshot(c), actor)
            existing[name] = c
            stats["concepts_created"] += 1
        else:
            if (c.aliases or []) != aliases:
                touch(c)
                c.aliases = aliases
            if (c.definition or "") != definition:
                touch(c)
                c.definition = definition
            if c.status != status:
                touch(c)
                c.status = status

    # 第二遍：父类与合并目标（按名称解析；校验循环继承）
    from .services import would_create_cycle
    for row in rows_c:
        name = str(row.get("name") or "").strip()
        c = existing[name]
        parent_name = str(row.get("parent") or "").strip() or None
        merged_name = str(row.get("merged_into") or "").strip() or None
        parent_id = None
        if parent_name:
            p = existing.get(parent_name)
            if not p:
                raise HTTPException(400, f"概念「{name}」的父类「{parent_name}」不存在")
            parent_id = p.id
        merged_id = None
        if merged_name:
            m = existing.get(merged_name)
            if not m:
                raise HTTPException(400, f"概念「{name}」的合并目标「{merged_name}」不存在")
            merged_id = m.id
        if c.parent_id != parent_id:
            if parent_id is not None and would_create_cycle(db, c.id, parent_id):
                raise HTTPException(400, f"概念「{name}」的父类设置会形成循环继承")
            touch(c)
            c.parent_id = parent_id
        if c.merged_into_id != merged_id:
            touch(c)
            c.merged_into_id = merged_id

    stats["concepts_updated"] = len(touched)
    stats["concepts_unchanged"] = len(pre_existing_names) - len(touched)
    db.flush()
    for name, before in touched.items():
        log_change(db, "concept", existing[name].id, "import", before, snapshot(existing[name]), actor)

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
                         status=status, created_by=actor)
            db.add(o)
            db.flush()
            log_change(db, "operator", o.id, "create", None, snapshot(o), actor)
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
            if changed:
                db.flush()
                log_change(db, "operator", o.id, "import", before, snapshot(o), actor)
                ops_touched += 1
            else:
                stats["operators_unchanged"] += 1
    stats["operators_updated"] = ops_touched
    db.commit()
    return stats
