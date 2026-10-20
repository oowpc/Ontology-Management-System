"""TMP 批量回填：把标注文本中的 TMP-xxx 替换为正式概念名，并逐条留痕。"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import BackfillRecord, Concept, TmpMapping

TMP_RE = re.compile(r"TMP-\d{4,}")


def backfill(db: Session, *, text: str, source_label: str, actor: str) -> dict:
    """扫描文本中的 TMP-xxx：
    - resolved / merged → 替换为正式概念名，写 backfill_records
    - open / 未登记    → 不替换，列入 unresolved（含原因）
    """
    text = text or ""
    # 长的 ID 先替换，避免短前缀互相干扰（如 TMP-0001 与未来的 TMP-00010）
    tmp_ids = sorted(set(TMP_RE.findall(text)), key=len, reverse=True)
    mappings = {}
    if tmp_ids:
        mappings = {m.tmp_id: m for m in db.scalars(
            select(TmpMapping).where(TmpMapping.tmp_id.in_(tmp_ids)))}
    replaced, unresolved = [], []
    result = text
    for tid in tmp_ids:
        m = mappings.get(tid)
        if not m:
            unresolved.append({"tmp_id": tid, "reason": "未登记"})
            continue
        if m.status not in ("resolved", "merged") or m.concept_id is None:
            unresolved.append({"tmp_id": tid, "reason": "审核未决或已驳回"})
            continue
        concept = db.get(Concept, m.concept_id)
        name = concept.name if concept else None
        if not name:
            unresolved.append({"tmp_id": tid, "reason": "目标概念不存在"})
            continue
        count = result.count(tid)
        if count == 0:
            continue
        result = result.replace(tid, name)
        rec = BackfillRecord(
            tmp_id=tid, concept_id=m.concept_id, replacement=name,
            source_label=(source_label or "").strip(), replaced_count=count, actor=actor,
        )
        db.add(rec)
        db.flush()
        replaced.append({
            "tmp_id": tid, "concept_id": m.concept_id, "replacement": name,
            "count": count, "record_id": rec.id,
        })
    db.commit()
    return {"text": result, "replaced": replaced, "unresolved": unresolved, "scanned": len(tmp_ids)}


def tmp_mapping_dict(m: TmpMapping, *, names=None) -> dict:
    return {
        "id": m.id,
        "tmp_id": m.tmp_id,
        "submission_id": m.submission_id,
        "concept_id": m.concept_id,
        "concept_name": (names or {}).get(m.concept_id),
        "status": m.status,
        "created_by": m.created_by,
        "created_at": m.created_at,
        "resolved_at": m.resolved_at,
    }


def record_dict(r: BackfillRecord) -> dict:
    return {
        "id": r.id,
        "tmp_id": r.tmp_id,
        "concept_id": r.concept_id,
        "replacement": r.replacement,
        "source_label": r.source_label,
        "replaced_count": r.replaced_count,
        "actor": r.actor,
        "created_at": r.created_at,
    }
