"""给 Archer 库按来源数据库建立概念树。

结构：
  Archer (root)
    └── aan_1 / book_1 / ...（199 个数据库分组）
          └── 639 个 Archer 概念（多来源概念保留多父类）

只改 archer 库，不碰其他库；默认 dry-run。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SRC = Path("/home/oowpc/NL2KE/Archer/super_ontology.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=SRC)
    ap.add_argument("--library-id", type=int, default=4)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    data = json.loads(args.src.read_text(encoding="utf-8"))
    concepts = data.get("concepts") or {}
    db_sources: dict[str, set[str]] = {}
    for name, c in concepts.items():
        for db in (c.get("sources") or {}):
            db_sources.setdefault(str(db), set()).add(str(name))
    print(f"concepts={len(concepts)} databases={len(db_sources)} links={sum(len(v) for v in db_sources.values())}")
    if not args.apply:
        print("dry-run；加 --apply 写入 archer 库")
        return 0

    sys.path.insert(0, str(ROOT))
    from app.db import SessionLocal, init_db
    from app.library_context import library_scope
    from app.models import Concept, ConceptParent, OntologyLibrary

    init_db()
    probe = SessionLocal()
    try:
        lib = probe.get(OntologyLibrary, args.library_id)
        if not lib:
            print("archer 库不存在")
            return 1
    finally:
        probe.close()

    with library_scope(args.library_id):
        db = SessionLocal()
        try:
            by_name = {c.name: c for c in db.scalars(select(Concept)).all()}
            root = by_name.get("Archer")
            if root is None:
                root = Concept(name="Archer", domain="Archer", definition="Archer Benchmark 根概念",
                               status="approved", created_by="archer-tree",
                               reviewed_by="archer-tree", source_uri="archer://",
                               ontology_module="Archer:Root")
                from app.services import assign_local_code
                assign_local_code(db, root)
                db.add(root)
                db.flush()
                by_name[root.name] = root
            root.parent_id = None

            groups = {}
            for db_name in sorted(db_sources):
                g = by_name.get(db_name)
                if g is None:
                    g = Concept(name=db_name, domain="Archer:Database",
                                definition=f"Archer 来源数据库 {db_name}",
                                status="approved", created_by="archer-tree",
                                reviewed_by="archer-tree",
                                source_uri=f"archer://{db_name}",
                                ontology_module="Archer:Database")
                    from app.services import assign_local_code
                    assign_local_code(db, g)
                    db.add(g)
                    db.flush()
                    by_name[g.name] = g
                if g.parent_id != root.id:
                    g.parent_id = root.id
                    exists = db.scalar(select(ConceptParent).where(
                        ConceptParent.concept_id == g.id,
                        ConceptParent.parent_id == root.id))
                    if not exists:
                        db.add(ConceptParent(concept_id=g.id, parent_id=root.id,
                                             is_primary=True, position=0))
                groups[db_name] = g
            db.flush()

            linked = 0
            primary_done: set[int] = set()
            for db_name in sorted(db_sources):
                g = groups[db_name]
                for name in sorted(db_sources[db_name]):
                    c = by_name.get(name)
                    if c is None or c.id == g.id:
                        continue
                    exists = db.scalar(select(ConceptParent).where(
                        ConceptParent.concept_id == c.id,
                        ConceptParent.parent_id == g.id))
                    is_primary = c.id not in primary_done
                    if exists is None:
                        db.add(ConceptParent(concept_id=c.id, parent_id=g.id,
                                             is_primary=is_primary, position=0))
                        linked += 1
                    elif is_primary and not exists.is_primary:
                        exists.is_primary = True
                    if is_primary:
                        primary_done.add(c.id)
                        c.parent_id = g.id
            db.commit()
            # 兜底：只要还有概念的 parent_id 为空，取它现有父类中的第一个
            for name, c in by_name.items():
                if name in groups or name == "Archer":
                    continue
                parents = db.scalars(select(ConceptParent).where(
                    ConceptParent.concept_id == c.id).order_by(
                    ConceptParent.parent_id)).all()
                if parents and c.parent_id is None:
                    c.parent_id = parents[0].parent_id
            db.commit()
            print(f"root={root.id} groups={len(groups)} new_parent_links={linked}")
            print(f"total_concepts={db.scalar(select(Concept.id).count()) if False else len(by_name)}")
        finally:
            db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
