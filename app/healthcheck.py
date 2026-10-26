"""本体体检：未使用 / 孤立节点 / 近义候选对 / 算子计数失衡。
手动触发与定时任务共用 run_health；报告落 health_reports。"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import Concept, GoldItem, HealthReport, Operator, UsageStat
from .similarity import score_against

KIND_LABELS = {
    "unused": "未使用概念",
    "orphan": "孤立节点（无父类）",
    "similar": "近义候选对",
    "cross_domain": "跨领域近义对",
    "operators": "算子过多/过少",
    "gold_validation": "成品 KE 校验",
}


def check_unused(db: Session) -> list[dict]:
    """已入库但 KE 使用次数为 0 的概念（附内部引用数，便于判断是否只是没扫到）。"""
    concepts = db.scalars(select(Concept).where(Concept.status == "approved").order_by(Concept.id)).all()
    stats = {s.concept_id: s for s in db.scalars(select(UsageStat))}
    children = dict(db.execute(
        select(Concept.parent_id, func.count()).group_by(Concept.parent_id)).all())
    ops = dict(db.execute(
        select(Operator.subject_concept_id, func.count()).group_by(Operator.subject_concept_id)).all())
    out = []
    for c in concepts:
        u = stats.get(c.id)
        if (u.ke_count if u else 0) > 0:
            continue
        out.append({
            "id": c.id, "name": c.name,
            "internal_refs": children.get(c.id, 0) + ops.get(c.id, 0),
            "last_used_at": u.last_used_at.isoformat() if u and u.last_used_at else None,
        })
    return out


def check_orphans(db: Session) -> list[dict]:
    """无父类的已入库概念（根白名单除外）。"""
    concepts = db.scalars(select(Concept).where(
        Concept.parent_id.is_(None), Concept.status == "approved").order_by(Concept.id)).all()
    roots = set(settings.root_concepts)
    return [{"id": c.id, "name": c.name, "aliases": c.aliases or []}
            for c in concepts if c.name not in roots]


def check_similar_pairs(db: Session, limit=200) -> list[dict]:
    """两两近义候选对（>= 提示阈值，按分数降序，封顶 limit）。"""
    concepts = db.scalars(select(Concept).where(
        Concept.status.in_(["approved", "draft"])).order_by(Concept.id)).all()
    pairs = []
    for i, a in enumerate(concepts):
        for b in concepts[i + 1:]:
            sc = score_against(a.name, a.aliases or [], b)
            if sc["score"] >= settings.similarity_suggest_threshold:
                pairs.append({
                    "a_id": a.id, "a_name": a.name, "a_domain": a.domain or "通用",
                    "b_id": b.id, "b_name": b.name, "b_domain": b.domain or "通用",
                    "score": sc["score"], "matched_on": sc["matched_on"],
                    "matched_value": sc["matched_value"],
                    "cross_domain": (a.domain or "通用") != (b.domain or "通用"),
                })
    pairs.sort(key=lambda p: p["score"], reverse=True)
    return pairs[:limit]


def check_operator_counts(db: Session) -> list[dict]:
    """算子=0（粒度过粗候选）或 > 阈值（粒度过细候选）的已入库概念。"""
    concepts = db.scalars(select(Concept).where(Concept.status == "approved").order_by(Concept.id)).all()
    ops = dict(db.execute(
        select(Operator.subject_concept_id, func.count())
        .where(Operator.status != "disabled")
        .group_by(Operator.subject_concept_id)).all())
    out = []
    for c in concepts:
        n = ops.get(c.id, 0)
        if n == 0:
            out.append({"id": c.id, "name": c.name, "operator_count": 0, "issue": "过少"})
        elif n > settings.health_operator_max:
            out.append({"id": c.id, "name": c.name, "operator_count": n, "issue": "过多"})
    return out


def check_gold_validation(db: Session) -> list[dict]:
    """M20：成品库 KE 校验问题清单（错误在前，按错误数降序）。

    缓存为当前引擎版本的直接用缓存，否则重算并刷新缓存（体检顺带当批量校验跑）。
    """
    from .ke import ke_validate

    out = []
    for it in db.scalars(select(GoldItem).order_by(GoldItem.id)).all():
        cached = it.validation or {}
        if cached.get("engine") != ke_validate.ENGINE_VERSION:
            cached = ke_validate.validate_item(db, it)
        if cached.get("ok") and not cached.get("warning_count"):
            continue
        first = (cached.get("errors") or cached.get("warnings") or [{}])[0]
        out.append({"id": it.id, "article_code": it.article_code, "ke_id": it.ke_id,
                    "status": it.status, "error_count": cached.get("error_count", 0),
                    "warning_count": cached.get("warning_count", 0),
                    "first_issue": first.get("message", "")})
    db.commit()  # 刷新过的校验缓存随体检落库
    out.sort(key=lambda x: (-x["error_count"], -x["warning_count"]))
    return out


def run_health(db: Session, trigger_type: str) -> HealthReport:
    similar = check_similar_pairs(db)
    details = {
        "unused": check_unused(db),
        "orphan": check_orphans(db),
        "similar_pairs": similar,
        "cross_domain": [p for p in similar if p["cross_domain"]],
        "operator_count": check_operator_counts(db),
        "gold_validation": check_gold_validation(db),
    }
    report = HealthReport(
        trigger_type=trigger_type,
        summary={k: len(v) for k, v in details.items()},
        details=details,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def report_dict(r: HealthReport, *, with_details=True) -> dict:
    d = {
        "id": r.id, "trigger_type": r.trigger_type,
        "summary": r.summary, "created_at": r.created_at,
    }
    if with_details:
        d["details"] = r.details
    return d
