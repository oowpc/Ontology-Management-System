"""Archer 本体导入 OMS（默认 dry-run）。

数据源：
  /home/oowpc/NL2KE/Archer/super_ontology.json
  /home/oowpc/NL2KE/Archer/tables.json

映射：
  Archer concept            -> OMS Concept（domain=Archer，source_uri=archer://db/table）
  Archer PROPERTY           -> OMS 算子，输入=概念，输出=列类型（String/Number/Date/Bool）
  Archer LINK               -> OMS 算子，输入=源概念，输出=目标概念
  LINK path/bindings        -> 描述文本（OMS 暂无结构化路径字段）

用法：
  python scripts/import_archer.py --dry-run
  python scripts/import_archer.py --apply --library archer
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = Path("/home/oowpc/NL2KE/Archer")
TYPE_MAP = {
    "text": "String", "varchar": "String", "char": "String", "string": "String",
    "integer": "Number", "int": "Number", "real": "Number", "float": "Number",
    "numeric": "Number", "decimal": "Number", "double": "Number",
    "boolean": "Bool", "bool": "Bool",
    "date": "Date", "datetime": "Date", "timestamp": "Date", "time": "Date",
}


def load(src: Path) -> tuple[dict, dict]:
    so = json.loads((src / "super_ontology.json").read_text(encoding="utf-8"))
    tables = json.loads((src / "tables.json").read_text(encoding="utf-8"))
    return so, tables


def schema_index(tables: list[dict]) -> dict[tuple[str, str], dict[str, str]]:
    out = {}
    for db in tables:
        dbid = db["db_id"]
        names = db.get("table_names_original") or []
        cols = db.get("column_names_original") or []
        types = db.get("column_types") or []
        for ti, table in enumerate(names):
            cmap = {}
            for ci, item in enumerate(cols):
                tid, cname = item
                if tid == ti and ci < len(types):
                    cmap[str(cname).lower()] = str(types[ci]).lower()
            out[(dbid, str(table).lower())] = cmap
    return out


def column_type(concept: dict, slot: str, schemas) -> str | None:
    values = []
    for source in (concept.get("sources") or {}).values():
        db = source.get("database")
        table = source.get("table")
        mapped = (source.get("slots") or {}).get(slot)
        if mapped:
            typ = schemas.get((db, str(table).lower()), {}).get(str(mapped).lower())
            if typ:
                values.append(typ)
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def build(so: dict, tables: list[dict]) -> tuple[dict, dict]:
    concepts = so.get("concepts") or {}
    ops = so.get("operators") or {}
    schemas = schema_index(tables)
    errors: list[str] = []
    warnings: list[str] = []
    max_name = 0
    concept_rows = []
    for name, c in concepts.items():
        if not str(name).strip():
            errors.append("空概念名")
            continue
        max_name = max(max_name, len(str(name)))
        sources = c.get("sources") or {}
        srcs = []
        for db, src in sources.items():
            table = src.get("table") or ""
            slots = src.get("slots") or {}
            srcs.append({"db": db, "table": table, "slots": slots})
        if not srcs:
            warnings.append(f"概念 {name} 没有 sources")
        src_uri = f"archer://{srcs[0]['db']}/{srcs[0]['table']}" if srcs else None
        definition = "Archer 概念"
        if srcs:
            definition = "Archer 概念；来源：" + "、".join(
                f"{x['db']}.{x['table']}" for x in srcs[:8])
            if len(srcs) > 8:
                definition += f" 等 {len(srcs)} 个来源"
        concept_rows.append({
            "name": str(name), "domain": "Archer", "aliases": [],
            "definition": definition, "parents": [], "status": "approved",
            "source_uri": src_uri, "ontology_module": "Archer",
        })

    operator_rows = []
    op_types = Counter()
    return_types = Counter()
    for name, o in ops.items():
        op_type = str(o.get("type") or "").upper()
        op_types[op_type] += 1
        note = str(o.get("note") or "")
        databases = ", ".join(o.get("databases") or [])
        if op_type == "PROPERTY":
            concept_name = str(o.get("concept") or "")
            slot = str(o.get("slot") or "")
            if concept_name not in concepts:
                errors.append(f"PROPERTY {name}: 概念不存在 {concept_name}")
                continue
            typ = column_type(concepts[concept_name], slot, schemas)
            ret = TYPE_MAP.get(typ or "", "String")
            return_types[ret] += 1
            description = f"Archer PROPERTY；字段 {slot}；类型 {typ or 'unknown'}；数据库 {databases}。{note}"
            operator_rows.append({
                "name": str(name), "subject": concept_name, "param_types": [],
                "return_type": ret, "description": description,
                "source_uri": (f"archer://{databases.split(',')[0].strip()}" if databases else None),
                "ontology_module": "Archer:PROPERTY", "status": "approved",
            })
        elif op_type == "LINK":
            sig = [str(x) for x in (o.get("signature") or [])]
            if len(sig) < 2 or any(x not in concepts for x in sig):
                errors.append(f"LINK {name}: 非法 signature {sig}")
                continue
            source, target = sig[0], sig[1]
            path = o.get("path") or []
            description = (f"Archer LINK；签名 {sig}；路径 {json.dumps(path, ensure_ascii=False)}；"
                           f"数据库 {databases}。{note}")
            operator_rows.append({
                "name": str(name), "subject": source, "param_types": [],
                "return_type": target, "description": description,
                "source_uri": (f"archer://{databases.split(',')[0].strip()}" if databases else None),
                "ontology_module": "Archer:LINK", "status": "approved",
            })
        else:
            errors.append(f"未知算子类型 {op_type}: {name}")
    payload = {"concepts": concept_rows, "operators": operator_rows,
               "rules": [], "individuals": []}
    stats = {
        "concepts": len(concept_rows),
        "operators": len(operator_rows),
        "operator_types": dict(op_types),
        "return_types": dict(return_types),
        "max_name_length": max_name,
        "databases": len(tables),
        "tables": sum(len(x.get("table_names_original") or []) for x in tables),
        "errors": errors[:50],
        "warnings": warnings[:50],
        "error_count": len(errors),
        "warning_count": len(warnings),
    }
    return payload, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=Path("/tmp/opencode/archer-import-preview.json"))
    ap.add_argument("--apply", action="store_true", help="写库；缺省只 dry-run")
    ap.add_argument("--dry-run", action="store_true", help="只预览（默认行为）")
    ap.add_argument("--library", default="archer")
    args = ap.parse_args()
    so, tables = load(args.src)
    payload, stats = build(so, tables)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"stats": stats, "payload": payload},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"preview: {args.out}")
    if stats["error_count"]:
        return 1
    if not args.apply:
        print("dry-run：未写入 OMS；加 --apply 才写库")
        return 0

    sys.path.insert(0, str(ROOT))
    from sqlalchemy import select
    from app.db import SessionLocal, init_db
    from app.importer import import_payload
    from app.library_context import library_scope
    from app.models import OntologyLibrary

    init_db()
    probe = SessionLocal()
    try:
        lib = probe.scalar(select(OntologyLibrary).where(OntologyLibrary.key == args.library))
        if lib is None:
            lib = OntologyLibrary(key=args.library, name="Archer Benchmark",
                                  kind="reference", description="Archer 多库本体基准",
                                  owner="system")
            probe.add(lib)
            probe.commit()
            probe.refresh(lib)
            print(f"创建本体库: {lib.key} (#{lib.id})")
        lib_id = lib.id
    finally:
        probe.close()
    batch_id = f"archer-{datetime.now():%Y%m%d-%H%M%S}"
    with library_scope(lib_id):
        db = SessionLocal()
        try:
            result = import_payload(db, payload, "archer-import", batch_id=batch_id,
                                    source=str(args.src))
        finally:
            db.close()
    print("导入批次:", batch_id)
    print("导入结果:", json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
