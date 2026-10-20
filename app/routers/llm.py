"""LLM 辅助 API：/api/llm/*（未配置时优雅降级 llm=false）"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import llm
from ..config import settings
from ..db import get_db
from ..security import current_user, require_role

router = APIRouter(prefix="/api/llm", tags=["llm"])


class DedupRequest(BaseModel):
    name: str
    aliases: list[str] = []
    parent_id: int | None = None


class ReviewSuggestRequest(BaseModel):
    submission_id: int


@router.get("/status")
def status(user: dict = Depends(current_user)):
    return {"enabled": llm.enabled(), "model": settings.llm_model if llm.enabled() else None}


@router.post("/dedup")
def dedup(data: DedupRequest, db: Session = Depends(get_db),
          user: dict = Depends(require_role())):
    items = llm.semantic_dedup(db, name=data.name, aliases=data.aliases, parent_id=data.parent_id)
    if items is None:
        return {"llm": False, "items": []}
    return {"llm": True, "items": items}


@router.post("/review-suggest")
def review_suggest(data: ReviewSuggestRequest, db: Session = Depends(get_db),
                   user: dict = Depends(require_role("reviewer", "admin"))):
    result = llm.suggest_review(db, data.submission_id)
    if result is None:
        return {"llm": False}
    return {"llm": True, **result}
