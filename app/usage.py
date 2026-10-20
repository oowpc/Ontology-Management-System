"""使用统计：KE 文本扫描（按概念名/别名出现计数，MVP 口径）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Concept, UsageStat


def scan_usage(db: Session, *, text: str, source: str) -> dict:
    text = text or ""
    concepts = db.scalars(select(Concept).where(Concept.status != "disabled")).all()
    stats = {s.concept_id: s for s in db.scalars(select(UsageStat))}
    now = datetime.now()
    hits = []
    for c in concepts:
        terms = [c.name, *(c.aliases or [])]
        count = sum(text.count(t) for t in terms if t)
        if count == 0:
            continue
        u = stats.get(c.id)
        if not u:
            u = UsageStat(concept_id=c.id, ke_count=0)
            db.add(u)
        u.ke_count = (u.ke_count or 0) + count
        u.last_used_at = now
        u.source = (source or "").strip()[:256]
        hits.append({"concept_id": c.id, "name": c.name, "count": count})
    db.commit()
    return {"hits": hits, "scanned_concepts": len(concepts)}
