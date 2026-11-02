"""标注运营看板：跨批次进度、证据覆盖、Issue、LLM 审核状态。"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import (AnnotationBatch, AnnotationEvidence, AnnotationUnit,
                      CoAlignment, Issue)
from . import service

ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIRS = (ROOT / "docs" / "pilots", ROOT / "docs" / "audits")


def _audit_for(batch_id: str) -> dict | None:
    for d in AUDIT_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*llm-audit.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            b = data.get("batch") or {}
            if str(b.get("batch_id") or "") != str(batch_id):
                continue
            s = data.get("review_summary") or {}
            return {
                "path": str(p.relative_to(ROOT)),
                "generated_at": data.get("generated_at"),
                "kept_concepts": s.get("kept_unique_concepts", 0),
                "kept_operators": s.get("kept_unique_operators", 0),
                "forced_context": len(s.get("forced_context") or []),
                "invented": len(s.get("invented") or []),
                "unreviewed": len(s.get("unreviewed") or []),
            }
    return None


def _unit_ids(db: Session, batch_id: int) -> list[int]:
    return list(db.scalars(select(AnnotationUnit.id).where(
        AnnotationUnit.batch_id == batch_id)).all())


def _issue_counts(db: Session, batch_id: int, unit_ids: list[int]) -> tuple[int, int]:
    cond = Issue.annotation_batch_id == batch_id
    if unit_ids:
        cond = or_(cond, Issue.annotation_unit_id.in_(unit_ids))
    total = db.scalar(select(func.count(Issue.id)).where(cond)) or 0
    opened = db.scalar(select(func.count(Issue.id)).where(
        cond, Issue.status.notin_(["resolved", "archived"]))) or 0
    return total, opened


def _blocked(db: Session, batch: AnnotationBatch, stats: dict, pending: int) -> int:
    units = stats["units"]
    if batch.state == "co_blind":
        return max(0, units - min(stats["co_a"], stats["co_b"]))
    if batch.state == "co_alignment":
        return max(0, units - stats["aligned"]) + pending
    if batch.state == "co_merge":
        return max(0, units - stats["aligned"]) + pending
    if batch.state == "ke_blind":
        return max(0, units - min(stats["ke_a"], stats["ke_b"]))
    if batch.state == "gold_merge":
        return max(0, units - stats["gold"])
    return 0


def _progress(batch: AnnotationBatch, stats: dict) -> int:
    units = stats["units"] or 0
    if not units:
        return 0
    state = batch.state
    if state in {"draft", "dataset_frozen"}:
        return 0
    if state == "co_blind":
        done = min(stats["co_a"], stats["co_b"])
    elif state in {"co_alignment", "co_merge"}:
        done = stats["aligned"]
    elif state == "ke_blind":
        done = min(stats["ke_a"], stats["ke_b"])
    elif state == "gold_merge":
        done = stats["gold"]
    elif state in {"cross_review", "validation", "passed", "rework"}:
        done = units
    else:
        done = 0
    return min(100, round(done * 100 / units))


def dashboard_data(db: Session) -> dict:
    batches = db.scalars(select(AnnotationBatch).order_by(AnnotationBatch.id.desc())).all()
    rows = []
    totals = {"batches": len(batches), "units": 0, "co_a": 0, "co_b": 0,
              "aligned": 0, "ke_a": 0, "ke_b": 0, "gold": 0,
              "evidence_units": 0, "issues": 0, "issues_open": 0,
              "blocked": 0, "llm_audited": 0}
    for b in batches:
        stats = service.batch_stats(db, b)
        ids = _unit_ids(db, b.id)
        evidence_units = db.scalar(select(func.count(func.distinct(
            AnnotationEvidence.unit_id))).where(AnnotationEvidence.batch_id == b.id)) or 0
        pending = db.scalar(select(func.count(CoAlignment.id)).where(
            CoAlignment.batch_id == b.id, CoAlignment.is_current.is_(True),
            CoAlignment.conclusion == "pending")) or 0
        issues, issues_open = _issue_counts(db, b.id, ids)
        audit = _audit_for(b.batch_id)
        blocked = _blocked(db, b, stats, pending)
        row = {
            "batch": b, "stats": stats, "evidence_units": evidence_units,
            "evidence_pct": round(evidence_units * 100 / stats["units"]) if stats["units"] else 0,
            "issues": issues, "issues_open": issues_open,
            "pending": pending, "blocked": blocked, "audit": audit,
            "progress": _progress(b, stats),
        }
        rows.append(row)
        for k, v in (("units", stats["units"]), ("co_a", stats["co_a"]),
                     ("co_b", stats["co_b"]), ("aligned", stats["aligned"]),
                     ("ke_a", stats["ke_a"]), ("ke_b", stats["ke_b"]),
                     ("gold", stats["gold"]), ("evidence_units", evidence_units),
                     ("issues", issues), ("issues_open", issues_open),
                     ("blocked", blocked)):
            totals[k] += v
        if audit:
            totals["llm_audited"] += 1
    return {"rows": rows, "totals": totals,
            "state_labels": service.STATE_LABELS}
