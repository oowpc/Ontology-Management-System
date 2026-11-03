"""回填统一 KE 文档：GOLD / NL2KE / 标注内容。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import init_db  # noqa: E402
from app.ke_docs import sync_gold_document, sync_nl2ke_run, upsert  # noqa: E402
from app.library_context import library_session  # noqa: E402
from app.models import (AnnotationUnit, GoldItem, GoldKe, KeAnnotation,  # noqa: E402
                        KeDocument, Nl2keRun, OntologyLibrary, OntologyVersion)


def main() -> None:
    init_db()
    total = 0
    with library_session(None) as db:
        libs = [x.id for x in db.scalars(select(OntologyLibrary).order_by(OntologyLibrary.id))]
    for lid in libs:
        with library_session(lid) as db:
            for item in db.scalars(select(GoldItem).order_by(GoldItem.id)):
                sync_gold_document(db, item, reason="backfill gold")
                total += 1
            for run in db.scalars(select(Nl2keRun).order_by(Nl2keRun.id)):
                if run.ke or run.gold_item_id:
                    sync_nl2ke_run(db, run)
                    total += 1
            for unit in db.scalars(select(AnnotationUnit).order_by(AnnotationUnit.id)):
                ke = (unit.source_payload or {}).get("ke") if unit.source_payload else ""
                if ke:
                    upsert(db, source_type="annotation", source_ref=f"unit:{unit.id}",
                           question=unit.raw_text or "", ke_text=ke, actor="backfill",
                           annotation_batch_id=unit.batch_id)
                    total += 1
            for ann in db.scalars(select(KeAnnotation).order_by(KeAnnotation.id)):
                if ann.content:
                    doc = upsert(db, source_type="annotation", source_ref=f"ke_annotation:{ann.id}",
                                 question="", ke_text=ann.content, actor=ann.annotator or "backfill",
                                 annotation_batch_id=ann.batch_id, reason=f"ke annotation v{ann.version}")
                    ann.ke_document_id = doc.id
                    total += 1
            for g in db.scalars(select(GoldKe).order_by(GoldKe.id)):
                if g.content:
                    doc = upsert(db, source_type="annotation", source_ref=f"gold_ke:{g.id}",
                                 question="", ke_text=g.content, actor=g.decided_by or "backfill",
                                 annotation_batch_id=g.batch_id, status="reviewed",
                                 reason=f"gold ke v{g.version}")
                    g.ke_document_id = doc.id
                    total += 1
            for batch_id in list(db.scalars(select(AnnotationUnit.batch_id).distinct())):
                version = db.scalar(select(OntologyVersion).where(
                    OntologyVersion.batch_id == batch_id,
                    OntologyVersion.status.in_(["frozen", "released"]))
                    .order_by(OntologyVersion.id.desc()))
                if version:
                    for doc in db.scalars(select(KeDocument).where(
                            KeDocument.annotation_batch_id == batch_id)).all():
                        doc.ontology_version_id = version.id
            db.commit()
        print(f"library {lid}: documents upserted")
    print(f"total upsert operations: {total}")


if __name__ == "__main__":
    main()
