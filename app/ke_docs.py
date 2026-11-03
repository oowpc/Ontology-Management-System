"""统一 KE 文档：从 GOLD/NL2KE/DataMaker/标注写入文档和版本。"""
from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .library_context import current_library_id
from .models import GoldItem, KeDocument, KeDocumentVersion, Nl2keRun

STATUS_MAP = {"draft": "candidate", "reviewed": "reviewed", "gold": "gold"}


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def _source_for_gold(item: GoldItem) -> tuple[str, str]:
    if item.article_code.startswith("DM-"):
        return "datamaker", f"datamaker:{item.article_code}:{item.ke_id}"
    if item.ke_id.startswith("NL2KE-"):
        return "nl2ke", f"nl2ke:{item.ke_id[6:]}"
    return "gold", f"gold:{item.id}"


def upsert(db: Session, *, source_type: str, source_ref: str, question: str,
           ke_text: str, status: str = "candidate", actor: str = "system",
           ontology_version_id: int | None = None, gold_item_id: int | None = None,
           annotation_batch_id: int | None = None, validation: dict | None = None,
           reason: str = "") -> KeDocument:
    text = ke_text or ""
    digest = content_hash(text)
    doc = db.scalar(select(KeDocument).where(
        KeDocument.source_type == source_type,
        KeDocument.source_ref == source_ref))
    if doc is None:
        doc = KeDocument(
            library_id=current_library_id.get(), source_type=source_type,
            source_ref=source_ref, question=question or "", ke_text=text,
            content_hash=digest, status=status, ontology_version_id=ontology_version_id,
            gold_item_id=gold_item_id, annotation_batch_id=annotation_batch_id,
            validation=validation, created_by=actor,
        )
        db.add(doc)
        db.flush()
        db.add(KeDocumentVersion(
            library_id=doc.library_id, document_id=doc.id, version=1,
            ke_text=text, content_hash=digest, reason=reason or "initial",
            created_by=actor,
        ))
        db.flush()
        return doc
    changed = doc.ke_text != text
    doc.question = question or doc.question
    doc.status = status or doc.status
    if validation is not None:
        doc.validation = validation
    if ontology_version_id is not None:
        doc.ontology_version_id = ontology_version_id
    if gold_item_id is not None:
        doc.gold_item_id = gold_item_id
    if annotation_batch_id is not None:
        doc.annotation_batch_id = annotation_batch_id
    if changed:
        doc.ke_text = text
        doc.content_hash = digest
        version = (db.scalar(select(KeDocumentVersion.version).where(
            KeDocumentVersion.document_id == doc.id)
            .order_by(KeDocumentVersion.version.desc()).limit(1)) or 0) + 1
        db.add(KeDocumentVersion(
            library_id=doc.library_id, document_id=doc.id, version=version,
            ke_text=text, content_hash=digest, reason=reason or "update",
            created_by=actor,
        ))
    doc.updated_at = datetime.now()
    db.flush()
    return doc


def sync_gold_document(db: Session, item: GoldItem, *, question: str | None = None,
                       actor: str = "system", reason: str = "") -> KeDocument:
    source_type, source_ref = _source_for_gold(item)
    doc = upsert(
        db, source_type=source_type, source_ref=source_ref,
        question=question or "", ke_text=item.ke_text or "",
        status=STATUS_MAP.get(item.status, "candidate"),
        actor=item.updated_by or item.created_by or actor,
        gold_item_id=item.id, validation=item.validation, reason=reason,
    )
    item.ke_document_id = doc.id
    doc.gold_item_id = item.id
    db.flush()
    return doc


def sync_nl2ke_run(db: Session, run: Nl2keRun, *, actor: str = "system") -> KeDocument:
    if run.gold_item_id:
        item = db.get(GoldItem, run.gold_item_id)
        if item is not None:
            doc = sync_gold_document(db, item, question=run.question, actor=actor,
                                     reason="nl2ke accept")
            run.ke_document_id = doc.id
            db.flush()
            return doc
    doc = upsert(
        db, source_type="nl2ke", source_ref=f"nl2ke:{run.id}",
        question=run.question or "", ke_text=run.ke or "",
        status="candidate" if run.status == "ok" else run.status,
        actor=actor, reason="nl2ke run",
    )
    run.ke_document_id = doc.id
    db.flush()
    return doc
