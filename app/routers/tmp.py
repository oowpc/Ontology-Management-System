"""TMP 映射与批量回填 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..backfill import backfill, record_dict, tmp_mapping_dict
from ..db import get_db
from ..models import BackfillRecord, TmpMapping
from ..schemas import BackfillRequest
from ..security import require_role
from ..services import name_map

router = APIRouter(prefix="/api", tags=["tmp"])

TMP_STATUS = {"open", "resolved", "merged"}


@router.get("/tmp-mappings")
def list_mappings(status: str | None = None, db: Session = Depends(get_db),
                  user: dict = Depends(require_role())):
    stmt = select(TmpMapping).order_by(TmpMapping.id.desc()).limit(500)
    if status:
        if status not in TMP_STATUS:
            raise HTTPException(400, "非法状态")
        stmt = stmt.where(TmpMapping.status == status)
    maps = db.scalars(stmt).all()
    names = name_map(db, [m.concept_id for m in maps])
    items = [tmp_mapping_dict(m, names=names) for m in maps]
    return {"items": items, "total": len(items)}


@router.post("/tmp/backfill")
def backfill_ep(data: BackfillRequest, db: Session = Depends(get_db),
                user: dict = Depends(require_role())):
    return backfill(db, text=data.text, source_label=data.source_label, actor=user["username"])


@router.get("/tmp/backfill/records")
def list_records(limit: int = Query(100, le=500), db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    rows = db.scalars(
        select(BackfillRecord).order_by(BackfillRecord.id.desc()).limit(limit)
    ).all()
    return {"items": [record_dict(r) for r in rows], "total": len(rows)}
