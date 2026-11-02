"""标注批次归档 ZIP 构建（页面与 agent API 共用）。"""
from __future__ import annotations

import io
import json
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (AnnotationBatch, AnnotationCrossReview, AnnotationEvidence,
                      AnnotationUnit, AuditLog, CoAlignment, CoAnnotation, CoMergeLog,
                      GoldKe, Issue, KeAnnotation, OntologyVersion, QueryRun)
from . import service


def _dict(obj) -> dict:
    out = {}
    for col in obj.__table__.columns:
        v = getattr(obj, col.name)
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        out[col.name] = v
    return out


def build_batch_archive(db: Session, batch: AnnotationBatch) -> bytes:
    bid = batch.id
    units = service.list_units(db, batch)
    unit_ids = [u.id for u in units]
    evidence = db.scalars(select(AnnotationEvidence).where(
        AnnotationEvidence.batch_id == bid).order_by(AnnotationEvidence.id)).all()
    co = db.scalars(select(CoAnnotation).where(CoAnnotation.batch_id == bid)
                    .order_by(CoAnnotation.id)).all()
    align = db.scalars(select(CoAlignment).where(CoAlignment.batch_id == bid)
                       .order_by(CoAlignment.id)).all()
    ke = db.scalars(select(KeAnnotation).where(KeAnnotation.batch_id == bid)
                    .order_by(KeAnnotation.id)).all()
    gold = db.scalars(select(GoldKe).where(GoldKe.batch_id == bid)
                      .order_by(GoldKe.id)).all()
    runs = db.scalars(select(QueryRun).where(QueryRun.batch_id == bid)
                      .order_by(QueryRun.id)).all()
    versions = db.scalars(select(OntologyVersion).where(OntologyVersion.batch_id == bid)
                          .order_by(OntologyVersion.id)).all()
    logs = db.scalars(select(CoMergeLog).where(CoMergeLog.batch_id == bid)
                      .order_by(CoMergeLog.id)).all()
    reviews = db.scalars(select(AnnotationCrossReview).where(
        AnnotationCrossReview.batch_id == bid).order_by(AnnotationCrossReview.id)).all()
    issue_cond = Issue.annotation_batch_id == bid
    if unit_ids:
        issue_cond = issue_cond | Issue.annotation_unit_id.in_(unit_ids)
    issues = db.scalars(select(Issue).where(issue_cond).order_by(Issue.id)).all()
    version_ids = [v.id for v in versions]
    audit_cond = (AuditLog.entity_type == "annotation_batch") & (AuditLog.entity_id == bid)
    if version_ids:
        audit_cond = audit_cond | ((AuditLog.entity_type == "ontology_version")
                                   & AuditLog.entity_id.in_(version_ids))
    audits = db.scalars(select(AuditLog).where(audit_cond).order_by(AuditLog.id)).all()
    files = {
        "batch.json": {"batch": _dict(batch), "stats": service.batch_stats(db, batch),
                       "members": [_dict(m) for m in service._members(db, bid)]},
        "units.json": [_dict(u) for u in units],
        "evidence.json": [_dict(e) for e in evidence],
        "co_annotations.json": [_dict(c) for c in co],
        "co_alignments.json": [_dict(a) for a in align],
        "ke_annotations.json": [_dict(k) for k in ke],
        "gold_kes.json": [_dict(g) for g in gold],
        "query_runs.json": [_dict(r) for r in runs],
        "ontology_versions.json": [_dict(v) for v in versions],
        "merge_logs.json": [_dict(l) for l in logs],
        "cross_reviews.json": [_dict(r) for r in reviews],
        "issues.json": [_dict(i) for i in issues],
        "audit.json": [_dict(a) for a in audits],
    }
    readme = ("# 标注批次归档\n\n"
              f"- 批次：{batch.batch_id} {batch.name}\n"
              f"- 状态：{service.STATE_LABELS.get(batch.state, batch.state)}\n"
              f"- 来源：{batch.source_name} {batch.source_version}\n"
              f"- 校验和：{batch.source_checksum}\n\n"
              "文件：batch/units/evidence/CO/对齐/KE/GOLD/query_runs/versions/merge_logs/"
              "cross_reviews/issues/audit。\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, json.dumps(data, ensure_ascii=False, indent=2))
        z.writestr("README.md", readme)
    return buf.getvalue()
