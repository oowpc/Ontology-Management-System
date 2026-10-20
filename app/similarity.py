"""查重打分：SimilarityProvider 接口 + 编辑距离实现。

- rapidfuzz 优先，缺失时退回 difflib
- 归一化：NFKC（全半角统一）+ 去空白 + 小写
- 分数 = max(输入名/别名 × 候选名/别名)，含包含关系加权与同父加权
- M5 近义候选对复用本模块；第二期可在此接口下换向量实现
"""
from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Concept

try:
    from rapidfuzz.fuzz import ratio as _rf_ratio
except ImportError:  # pragma: no cover
    _rf_ratio = None


class SimilarityBlock(Exception):
    """直接入库被高相似概念拦截；similar 为命中列表（供 API 结构化返回）。"""

    def __init__(self, name: str, similar: list[dict]):
        self.similar = similar
        tops = "、".join(f"「{s['name']}」（{s['score']:.0%}）" for s in similar[:5])
        super().__init__(f"存在与「{name}」高相似的概念：{tops}。已阻止直接入库，请走提交审核流程。")


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return "".join(s.split()).lower()


def _ratio(a: str, b: str) -> float:
    if _rf_ratio is not None:
        return _rf_ratio(a, b) / 100.0
    return SequenceMatcher(None, a, b).ratio()


def pair_score(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    base = _ratio(na, nb)
    if na in nb or nb in na:
        return max(0.9, base)  # 包含关系（如 政府 ⊂ 地方政府）
    return base


def score_against(name: str, aliases, candidate: Concept, parent_id=None) -> dict:
    """输入（名+别名）对单个候选概念打分，记录命中方式。"""
    best, matched_on, matched_value = 0.0, "", ""
    cand_pairs = [("name", candidate.name)] + [("alias", a) for a in (candidate.aliases or [])]
    for inp in [name, *(aliases or [])]:
        for field, cand in cand_pairs:
            s = pair_score(inp, cand)
            if s > best:
                best = s
                matched_on = "名称" if field == "name" else "别名"
                matched_value = cand
    if parent_id is not None and candidate.parent_id == parent_id:
        best = min(1.0, best + 0.05)
    return {"score": round(best, 4), "matched_on": matched_on, "matched_value": matched_value}


def find_similar(db: Session, *, name: str, aliases=(), parent_id=None,
                 limit=10, exclude_id=None, threshold=None) -> list[dict]:
    """返回相似概念列表（>= threshold 或提示阈值，按分数降序）。停用概念不参与。"""
    th = settings.similarity_suggest_threshold if threshold is None else threshold
    stmt = select(Concept).where(Concept.status != "disabled")
    if exclude_id is not None:
        stmt = stmt.where(Concept.id != exclude_id)
    candidates = db.scalars(stmt).all()
    parent_names = {c.id: c.name for c in candidates}
    hits = []
    for c in candidates:
        sc = score_against(name, aliases, c, parent_id)
        if sc["score"] >= th:
            hits.append({
                "id": c.id, "name": c.name, "aliases": c.aliases or [],
                "parent_id": c.parent_id,
                "parent_name": parent_names.get(c.parent_id),
                "status": c.status, **sc,
            })
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:limit]
