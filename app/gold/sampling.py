"""M21 · 抽检闭环：分层等距抽样 → 双人盲评 → 分歧仲裁 → 统计（Wilson CI / 一致率）。

口径参考 NL2KE kelab（reviews_v2 双人盲评 + C 裁决、分层等距抽样、Wilson 置信区间）：
- 抽样：按组别分层、层内按条目 id 排序等距抽取（seed 落 params，可复现）；
  size 与 rate（默认 10%）二选一，size 优先；分层按最大余数法分配
- 盲评：每条样本需 2 名复核人独立打标（通过/不通过）；两人一致 → final_verdict 落定；
  分歧 → 待仲裁，第三人（reviewer/admin）仲裁落定。复核页对他人结论盲化
  （本人已评或已定局的条目才可见他人结论）
- 关闭：全部样本有 final_verdict 方可关闭；关闭时回填 total/passed（兼容旧报表口径）
- 统计：通过率 + Wilson 95% CI；双评一致率 = 双评一致条数 / 双评完成条数
写操作全部留痕（entity_type='gold_sample'）。
"""
from __future__ import annotations

import random
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_change, snapshot
from ..models import GoldItem, GoldSample, GoldSampleItem, GoldSampleReview

REQUIRED_REVIEWS = 2          # 双人盲评口径
VERDICTS = ("通过", "不通过")
SAMPLER_ROLES = ("reviewer", "admin")   # 抽检打标是复核行为（标注员是被抽方，回避）


# ---------------- 统计 ----------------

def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """比例的 Wilson 95% 置信区间（n=0 返回 None）。"""
    if n == 0:
        return None, None
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5 / denom
    return max(0.0, center - half), min(1.0, center + half)


# ---------------- 抽样 ----------------

def _stratified_pick(items: list[GoldItem], size: int, seed: int) -> list[GoldItem]:
    """按组别分层等距抽样：层内按 id 排序，步长 n/k，起点由 (seed, 层名) 决定。"""
    strata: dict[str, list[GoldItem]] = {}
    for it in items:
        strata.setdefault(it.group_name or "(未分组)", []).append(it)
    # 最大余数法分配各层名额
    total = len(items)
    alloc = []
    for name, members in strata.items():
        exact = size * len(members) / total
        alloc.append([name, members, int(exact), exact - int(exact)])
    remainder = size - sum(a[2] for a in alloc)
    for a in sorted(alloc, key=lambda a: -a[3])[:remainder]:
        a[2] += 1
    picked: list[GoldItem] = []
    for name, members, k, _ in alloc:
        members.sort(key=lambda i: i.id)
        k = min(k, len(members))
        if k <= 0:
            continue
        rng = random.Random(f"{seed}:{name}")
        step = len(members) / k
        start = rng.randrange(max(1, int(step))) if step >= 1 else 0
        idxs = sorted({min(len(members) - 1, int(start + i * step)) for i in range(k)})
        # 去重后不足名额 → 从头补齐未选中的
        fill = 0
        while len(idxs) < k and fill < len(members):
            if fill not in idxs:
                idxs.append(fill)
            fill += 1
        picked.extend(members[i] for i in sorted(idxs))
    return picked


def create_auto_sample(db: Session, *, group_name: str = "", article_code: str = "",
                       status: str = "", size: int = 0, rate: float = 0.1,
                       seed: int | None = None, note: str = "", actor: str) -> GoldSample:
    """系统抽样创建抽查：总体按过滤条件圈定，分层等距抽取，样本清单落库。"""
    stmt = select(GoldItem).order_by(GoldItem.id)
    if group_name:
        stmt = stmt.where(GoldItem.group_name == group_name.strip())
    if article_code:
        stmt = stmt.where(GoldItem.article_code == article_code.strip())
    if status:
        stmt = stmt.where(GoldItem.status == status.strip())
    population = db.scalars(stmt).all()
    if not population:
        raise HTTPException(400, "按当前条件圈定的总体为空")
    if seed is None:
        seed = random.SystemRandom().randrange(1, 2**31)
    if size <= 0:
        if not (0 < rate <= 1):
            raise HTTPException(400, "rate 取值 (0,1]，或显式给 size")
        size = max(1, round(len(population) * rate))
    size = min(size, len(population))

    picked = _stratified_pick(population, size, seed)
    scope_parts = [p for p in (group_name.strip(), article_code.strip(), status.strip()) if p]
    s = GoldSample(
        sample_date=datetime.now().strftime("%Y-%m-%d"),
        scope="·".join(scope_parts) or "全部成品",
        total=len(picked), passed=0, checker=actor, note=note or "",
        kind="auto", status="open",
        params={"group_name": group_name.strip(), "article_code": article_code.strip(),
                "status": status.strip(), "size": size, "rate": rate, "seed": seed,
                "population": len(population), "required_reviews": REQUIRED_REVIEWS},
    )
    db.add(s)
    db.flush()
    for it in picked:
        db.add(GoldSampleItem(sample_id=s.id, gold_item_id=it.id,
                              article_code=it.article_code, ke_id=it.ke_id))
    db.flush()
    log_change(db, "gold_sample", s.id, "create", None, snapshot(s), actor)
    db.commit()
    db.refresh(s)
    return s


def get_sample_or_404(db: Session, sid: int) -> GoldSample:
    s = db.get(GoldSample, sid)
    if not s:
        raise HTTPException(404, "抽查记录不存在")
    if (s.kind or "manual") != "auto":
        raise HTTPException(400, "该记录是手工登记，无样本清单（系统抽样才有逐条打标）")
    return s


# ---------------- 盲评 / 仲裁 / 关闭 ----------------

def _item_or_404(db: Session, sid: int, gold_item_id: int) -> GoldSampleItem:
    si = db.scalar(select(GoldSampleItem).where(
        GoldSampleItem.sample_id == sid, GoldSampleItem.gold_item_id == gold_item_id))
    if not si:
        raise HTTPException(404, "该条目不在本抽查的样本清单里")
    return si


def _refresh_final(db: Session, si: GoldSampleItem):
    """按现有复核结论重算 final_verdict：≥2 人且一致 → 落定；分歧 → 置空待仲裁。"""
    reviews = db.scalars(select(GoldSampleReview).where(
        GoldSampleReview.sample_id == si.sample_id,
        GoldSampleReview.gold_item_id == si.gold_item_id)).all()
    verdicts = {r.verdict for r in reviews}
    if len(reviews) >= REQUIRED_REVIEWS and len(verdicts) == 1:
        si.final_verdict = verdicts.pop()
    elif len(verdicts) > 1:
        si.final_verdict = None          # 分歧：待仲裁
        si.arbiter = None
        si.arbitrated_at = None
    db.flush()


def add_review(db: Session, s: GoldSample, *, gold_item_id: int, reviewer: str,
               verdict: str, note: str = "") -> GoldSampleReview:
    if s.status != "open":
        raise HTTPException(409, "抽查已关闭，不能再打标")
    if verdict not in VERDICTS:
        raise HTTPException(400, f"结论取值：{' / '.join(VERDICTS)}")
    si = _item_or_404(db, s.id, gold_item_id)
    if db.scalar(select(GoldSampleReview).where(
            GoldSampleReview.sample_id == s.id,
            GoldSampleReview.gold_item_id == gold_item_id,
            GoldSampleReview.reviewer == reviewer)):
        raise HTTPException(409, "你已对该条目打标（每人一条；如需改判请由仲裁落定）")
    r = GoldSampleReview(sample_id=s.id, gold_item_id=gold_item_id, reviewer=reviewer,
                         verdict=verdict, note=(note or "").strip())
    db.add(r)
    db.flush()
    _refresh_final(db, si)
    db.flush()
    log_change(db, "gold_sample", s.id, "review", None,
               {"gold_item_id": gold_item_id, "reviewer": reviewer, "verdict": verdict}, reviewer)
    db.commit()
    db.refresh(r)
    return r


def arbitrate(db: Session, s: GoldSample, *, gold_item_id: int, arbiter: str,
              verdict: str, note: str = "") -> GoldSampleItem:
    if s.status != "open":
        raise HTTPException(409, "抽查已关闭")
    if verdict not in VERDICTS:
        raise HTTPException(400, f"结论取值：{' / '.join(VERDICTS)}")
    si = _item_or_404(db, s.id, gold_item_id)
    reviews = db.scalars(select(GoldSampleReview).where(
        GoldSampleReview.sample_id == s.id,
        GoldSampleReview.gold_item_id == gold_item_id)).all()
    verdicts = {r.verdict for r in reviews}
    if len(verdicts) < 2:
        raise HTTPException(400, "无分歧不需仲裁（分歧 = 两名复核人结论不一致）")
    before = snapshot(si)
    si.final_verdict = verdict
    si.arbiter = arbiter
    si.arbitrated_at = datetime.now()
    if note.strip():
        db.add(GoldSampleReview(sample_id=s.id, gold_item_id=gold_item_id,
                                reviewer=f"{arbiter}（仲裁）", verdict=verdict, note=note.strip()))
    db.flush()
    log_change(db, "gold_sample", s.id, "arbitrate", before, snapshot(si), arbiter)
    db.commit()
    db.refresh(si)
    return si


def close_sample(db: Session, s: GoldSample, actor: str) -> GoldSample:
    items = db.scalars(select(GoldSampleItem).where(GoldSampleItem.sample_id == s.id)).all()
    pending = [i for i in items if not i.final_verdict]
    if pending:
        raise HTTPException(409, f"还有 {len(pending)} 条样本无最终结论（需双评一致或仲裁）")
    before = snapshot(s)
    s.status = "closed"
    s.total = len(items)
    s.passed = sum(1 for i in items if i.final_verdict == "通过")
    db.flush()
    log_change(db, "gold_sample", s.id, "close", before, snapshot(s), actor)
    db.commit()
    db.refresh(s)
    return s


# ---------------- 查询与统计 ----------------

def sample_detail(db: Session, s: GoldSample, *, viewer: str, viewer_role: str) -> dict:
    """详情负载；盲化规则：本人未评且未定局的条目，他人结论对本人不可见。"""
    items = db.scalars(select(GoldSampleItem).where(
        GoldSampleItem.sample_id == s.id).order_by(GoldSampleItem.id)).all()
    reviews = db.scalars(select(GoldSampleReview).where(
        GoldSampleReview.sample_id == s.id)).all()
    by_item: dict[int, list[GoldSampleReview]] = {}
    for r in reviews:
        by_item.setdefault(r.gold_item_id, []).append(r)
    gold_map = {g.id: g for g in db.scalars(select(GoldItem).where(
        GoldItem.id.in_([i.gold_item_id for i in items] or [0]))).all()}

    rows = []
    for i in items:
        rs = by_item.get(i.gold_item_id, [])
        mine = next((r for r in rs if r.reviewer == viewer), None)
        blind = (mine is None and not i.final_verdict)
        others = [] if blind else [
            {"reviewer": r.reviewer, "verdict": r.verdict, "note": r.note,
             "created_at": r.created_at} for r in rs]
        g = gold_map.get(i.gold_item_id)
        v = (g.validation or {}) if g else {}
        rows.append({
            "gold_item_id": i.gold_item_id, "article_code": i.article_code, "ke_id": i.ke_id,
            "ke_text": g.ke_text if g else "", "group_name": g.group_name if g else "",
            "item_status": g.status if g else "",
            "validation_ok": v.get("ok"), "validation_errors": v.get("error_count", 0),
            "final_verdict": i.final_verdict, "arbiter": i.arbiter,
            "my_verdict": mine.verdict if mine else None,
            "review_count": len(rs), "reviews": others, "blind": blind,
        })
    n_final = sum(1 for r in rows if r["final_verdict"])
    passed = sum(1 for r in rows if r["final_verdict"] == "通过")
    lo, hi = wilson_ci(passed, n_final)
    paired = [r for r in rows if r["review_count"] >= REQUIRED_REVIEWS]
    agree = sum(1 for r in paired
                if len({rv.verdict for rv in by_item[r["gold_item_id"]]}) == 1)
    disagreements = [r for r in rows
                     if r["review_count"] >= REQUIRED_REVIEWS and not r["final_verdict"]]
    return {
        "sample": {"id": s.id, "sample_date": s.sample_date, "scope": s.scope,
                   "kind": s.kind or "manual", "status": s.status, "params": s.params or {},
                   "checker": s.checker, "note": s.note, "created_at": s.created_at},
        "items": rows,
        "stats": {
            "total": len(rows), "finalized": n_final, "passed": passed,
            "pass_rate": (passed / n_final) if n_final else None,
            "wilson_lo": lo, "wilson_hi": hi,
            "paired": len(paired), "agree": agree,
            "agreement_rate": (agree / len(paired)) if paired else None,
            "disagreement_count": len(disagreements),
            "required_reviews": REQUIRED_REVIEWS,
        },
    }


def sample_list(db: Session) -> list[dict]:
    """列表负载：manual 行保持旧形态；auto 行附进度与通过率。"""
    rows = db.scalars(select(GoldSample).order_by(GoldSample.id.desc()).limit(100)).all()
    out = []
    for s in rows:
        d = {"id": s.id, "sample_date": s.sample_date, "scope": s.scope,
             "total": s.total, "passed": s.passed, "checker": s.checker, "note": s.note,
             "kind": s.kind or "manual", "status": s.status, "created_at": s.created_at}
        if d["kind"] == "auto":
            items = db.scalars(select(GoldSampleItem).where(GoldSampleItem.sample_id == s.id)).all()
            d["item_count"] = len(items)
            d["finalized"] = sum(1 for i in items if i.final_verdict)
        out.append(d)
    return out
