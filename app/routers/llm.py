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


@router.get("/usage")
def usage(db: Session = Depends(get_db),
          user: dict = Depends(require_role("reviewer", "admin"))):
    """M23：LLM 调用台账汇总（按场景聚合：次数/tokens/均耗时/失败数/最近错误）。"""
    from sqlalchemy import select

    from ..models import LlmCall
    # Python 侧聚合（台账量级小，免拼方言无关的 SQL）
    scenes: dict[str, dict] = {}
    all_calls = db.scalars(select(LlmCall).order_by(LlmCall.id.desc()).limit(5000)).all()
    for c in all_calls:
        s = scenes.setdefault(c.scene or "(未标注)", {
            "calls": 0, "ok": 0, "failed": 0, "prompt_tokens": 0,
            "completion_tokens": 0, "latency_ms_sum": 0, "last_error": ""})
        s["calls"] += 1
        s["ok" if c.ok else "failed"] += 1
        s["prompt_tokens"] += c.prompt_tokens or 0
        s["completion_tokens"] += c.completion_tokens or 0
        s["latency_ms_sum"] += c.latency_ms or 0
        if not c.ok and c.error and not s["last_error"]:
            s["last_error"] = c.error
    for s in scenes.values():
        s["avg_latency_ms"] = round(s.pop("latency_ms_sum") / s["calls"]) if s["calls"] else 0
    totals = {"calls": sum(s["calls"] for s in scenes.values()),
              "failed": sum(s["failed"] for s in scenes.values()),
              "prompt_tokens": sum(s["prompt_tokens"] for s in scenes.values()),
              "completion_tokens": sum(s["completion_tokens"] for s in scenes.values())}
    return {"enabled": llm.enabled(), "totals": totals, "scenes": scenes}
