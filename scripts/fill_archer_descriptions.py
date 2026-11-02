"""为 Archer 概念生成 description（来源表/字段摘要）；算子已有 Archer note。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SRC = Path("/home/oowpc/NL2KE/Archer/super_ontology.json")


def describe(name: str, data: dict) -> str:
    sources = data.get("sources") or {}
    tables = []
    slots = []
    for db, src in sources.items():
        table = src.get("table") or "?"
        tables.append(f"{db}.{table}")
        for key in (src.get("slots") or {}):
            if key not in slots:
                slots.append(key)
    kind = "关联表概念" if data.get("internal") else "实体/关系概念"
    head = f"Archer {kind} {name}"
    if tables:
        head += f"，来源 {len(sources)} 个数据库：" + "、".join(tables[:6])
        if len(tables) > 6:
            head += f" 等 {len(tables)} 个"
    if slots:
        head += "；字段包括 " + "、".join(slots[:10])
        if len(slots) > 10:
            head += f" 等 {len(slots)} 个"
    return head + "。"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--library-id", type=int, default=4)
    args = ap.parse_args()
    so = json.loads(SRC.read_text(encoding="utf-8"))
    concepts = so.get("concepts") or {}
    print(f"Archer concepts: {len(concepts)}")
    if not args.apply:
        for n, (name, data) in enumerate(concepts.items()):
            if n < 3:
                print(name, "=>", describe(name, data))
        print("dry-run；加 --apply 写入")
        return 0

    sys.path.insert(0, str(ROOT))
    from app.db import SessionLocal, init_db
    from app.library_context import library_scope
    from app.models import Concept, Operator

    init_db()
    with library_scope(args.library_id):
        db = SessionLocal()
        try:
            changed = 0
            for name, data in concepts.items():
                c = db.scalar(select(Concept).where(Concept.name == name))
                if not c:
                    continue
                text = describe(name, data)
                if c.description != text:
                    c.description = text
                    changed += 1
            # 算子为空时补签名说明
            for o in db.scalars(select(Operator)).all():
                if not (o.description or "").strip():
                    o.description = f"Archer 算子 {o.name}：{o.subject_concept_id} → {o.return_type}"
                    changed += 1
            db.commit()
            print(f"updated={changed}")
        finally:
            db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
