"""实时查重 API：/api/similarity/check（提交页 JS 调用）"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..security import require_role
from ..similarity import find_similar
from ..templating import STATUS_LABELS

router = APIRouter(prefix="/api/similarity", tags=["similarity"])


@router.get("/check")
def check(name: str = "", aliases: list[str] = Query(default=[]),
          parent_id: int | None = None, limit: int = Query(10, le=50),
          db: Session = Depends(get_db), user: dict = Depends(require_role())):
    items = find_similar(db, name=name, aliases=aliases, parent_id=parent_id, limit=limit) \
        if name.strip() else []
    for i in items:
        i["status_label"] = STATUS_LABELS.get(i["status"], i["status"])
    return {
        "items": items,
        "block_threshold": settings.similarity_block_threshold,
        "suggest_threshold": settings.similarity_suggest_threshold,
    }
