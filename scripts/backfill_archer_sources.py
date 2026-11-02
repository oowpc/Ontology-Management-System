"""把 Archer 源 JSON 的结构化 sources / schema 写入 OMS，供 NL2KE 桥使用。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SRC = Path("/home/oowpc/NL2KE/Archer")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--library-id", type=int, default=4)
    args = ap.parse_args()
    so = json.loads((SRC / "super_ontology.json").read_text(encoding="utf-8"))
    tables = json.loads((SRC / "tables.json").read_text(encoding="utf-8"))
    print(f"concepts={len(so.get('concepts') or {})} operators={len(so.get('operators') or {})} schemas={len(tables)}")
    if not args.apply:
        print("dry-run；加 --apply 写入")
        return 0
    sys.path.insert(0, str(ROOT))
    from app.db import SessionLocal, init_db
    from app.library_context import library_scope
    from app.models import Concept, OntologySource, Operator

    init_db()
    with library_scope(args.library_id):
        db = SessionLocal()
        try:
            nc = no = ns = 0
            for name, data in (so.get("concepts") or {}).items():
                c = db.scalar(select(Concept).where(Concept.name == name))
                if c:
                    c.source_payload = data
                    nc += 1
            for name, data in (so.get("operators") or {}).items():
                o = db.scalar(select(Operator).where(Operator.name == name))
                if o:
                    o.source_payload = data
                    no += 1
            for table in tables:
                key = str(table["db_id"])
                row = db.scalar(select(OntologySource).where(OntologySource.key == key))
                if row is None:
                    row = OntologySource(library_id=args.library_id, key=key,
                                         name=key, schema_payload=table,
                                         created_by="archer-import")
                    db.add(row)
                else:
                    row.schema_payload = table
                ns += 1
            db.commit()
            print(f"updated concepts={nc} operators={no} schemas={ns}")
        finally:
            db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
