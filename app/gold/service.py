"""GOLDKE 服务层：阶段推进、泳道、统计聚合（按《数据标注执行方案》）。"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import GoldArticle, GoldIssue, GoldSample, GoldScheduleCell, KeTask

STAGES = [k for k, _ in settings.gold_stages]           # 阶段流水线（GOLD_STAGES 可配，建库前设定）
STAGE_LABELS = dict(settings.gold_stages)
SLOTS = list(settings.gold_slots)                        # 泳道时段（GOLD_SLOTS 可配）
GROUPS = ["一组", "二组", "三组"]  # 兜底默认；运行时组清单以用户表为准（与 M7 同口径，见 list_groups）
ISSUE_STATUS_LABELS = {"open": "开放", "resolved": "已解决", "escalated": "已上会"}


def list_groups(db: Session) -> list[str]:
    """组清单以 users.group_name 为准（与 M7 上传模块完全同口径）；无组账号时回退默认三组。"""
    from ..ke.service import list_groups as _ke_groups
    return _ke_groups(db) or GROUPS


# ---------------- 篇级任务 ----------------

def get_article_or_404(db: Session, aid: int) -> GoldArticle:
    a = db.get(GoldArticle, aid)
    if not a:
        raise HTTPException(404, "篇任务不存在")
    return a


def create_article(db: Session, *, code, title, domain, group_name, ke_target, note, actor,
                   ke_done: int = 0) -> GoldArticle:
    code = (code or "").strip()
    title = (title or "").strip()
    if not code or not title:
        raise HTTPException(400, "篇编号与标题不能为空")
    if db.scalar(select(GoldArticle).where(GoldArticle.code == code)):
        raise HTTPException(409, f"篇编号「{code}」已存在")
    groups = list_groups(db)
    if group_name not in groups:
        raise HTTPException(400, f"组别取值：{' / '.join(groups)}")
    a = GoldArticle(code=code, title=title, domain=(domain or "").strip() or "未分类",
                    group_name=group_name, ke_target=max(0, int(ke_target or 30)),
                    ke_done=max(0, int(ke_done or 0)),
                    note=note or "", created_by=actor)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def update_article(db: Session, a: GoldArticle, changes: dict) -> GoldArticle:
    if "title" in changes and changes["title"] is not None:
        if not changes["title"].strip():
            raise HTTPException(400, "标题不能为空")
        a.title = changes["title"].strip()
    if "domain" in changes and changes["domain"] is not None:
        a.domain = changes["domain"].strip() or "未分类"
    if "group_name" in changes and changes["group_name"] is not None:
        groups = list_groups(db)
        if changes["group_name"] not in groups:
            raise HTTPException(400, f"组别取值：{' / '.join(groups)}")
        a.group_name = changes["group_name"]
    if "ke_target" in changes and changes["ke_target"] is not None:
        a.ke_target = max(0, int(changes["ke_target"]))
    if "ke_done" in changes and changes["ke_done"] is not None:
        a.ke_done = max(0, int(changes["ke_done"]))
    if "note" in changes and changes["note"] is not None:
        a.note = changes["note"]
    db.commit()
    db.refresh(a)
    return a


def advance_stage(db: Session, a: GoldArticle) -> GoldArticle:
    idx = STAGES.index(a.stage)
    if idx >= len(STAGES) - 1:
        raise HTTPException(400, "已是最终阶段")
    a.stage = STAGES[idx + 1]
    db.commit()
    db.refresh(a)
    return a


def article_dict(a: GoldArticle) -> dict:
    return {
        "id": a.id, "code": a.code, "title": a.title, "domain": a.domain,
        "group_name": a.group_name, "stage": a.stage,
        "stage_label": STAGE_LABELS.get(a.stage, a.stage),
        "ke_target": a.ke_target, "ke_done": a.ke_done, "note": a.note,
        "created_by": a.created_by, "created_at": a.created_at, "updated_at": a.updated_at,
    }


# ---------------- 泳道 ----------------

def schedule_grid(db: Session, days: int = 20) -> dict:
    """{day_index: {slot: {group: cell}}}，无记录的格子为 None。"""
    groups = list_groups(db)
    cells = db.scalars(select(GoldScheduleCell)).all()
    grid: dict[int, dict[str, dict[str, dict | None]]] = {}
    for d in range(1, days + 1):
        grid[d] = {slot: {g: None for g in groups} for slot in SLOTS}
    for c in cells:
        if c.day_index in grid and c.slot in grid[c.day_index] and c.group_name in grid[c.day_index][c.slot]:
            grid[c.day_index][c.slot][c.group_name] = schedule_cell_dict(c)
    return grid


def upsert_cell(db: Session, *, day_index, slot, group_name, task, article_code, status, note) -> GoldScheduleCell:
    if slot not in SLOTS:
        raise HTTPException(400, f"时段取值：{' / '.join(SLOTS)}")
    groups = list_groups(db)
    if group_name not in groups:
        raise HTTPException(400, f"组别取值：{' / '.join(groups)}")
    if status not in ("planned", "done", "missed"):
        raise HTTPException(400, "状态取值：planned / done / missed")
    if not (1 <= int(day_index) <= 60):
        raise HTTPException(400, "day_index 取值 1-60")
    c = db.scalar(select(GoldScheduleCell).where(
        GoldScheduleCell.day_index == day_index,
        GoldScheduleCell.slot == slot,
        GoldScheduleCell.group_name == group_name))
    if c is None:
        c = GoldScheduleCell(day_index=day_index, slot=slot, group_name=group_name,
                             task="", status="planned")
        db.add(c)
    c.task = (task or "").strip()
    c.article_code = (article_code or "").strip()
    c.status = status
    c.note = (note or "").strip()
    db.commit()
    db.refresh(c)
    return c


def schedule_cell_dict(c: GoldScheduleCell) -> dict:
    return {
        "id": c.id, "day_index": c.day_index, "slot": c.slot, "group_name": c.group_name,
        "task": c.task, "article_code": c.article_code, "status": c.status, "note": c.note,
    }


# ---------------- 统计 ----------------

def dashboard_stats(db: Session) -> dict:
    articles = db.scalars(select(GoldArticle).order_by(GoldArticle.id)).all()
    # 篇级完成数口径：题目带「篇编号」的按 M7 实际上传自动统计；无映射的篇回退到组内自报 ke_done
    article_auto = {code: n for code, n in db.execute(
        select(KeTask.article_code, func.count())
        .where(KeTask.article_code != "", KeTask.status == "已完成")
        .group_by(KeTask.article_code)).all()}

    def eff_done(a: GoldArticle) -> int:
        return article_auto.get(a.code, a.ke_done)

    new_done = sum(eff_done(a) for a in articles)
    new_target = sum(a.ke_target for a in articles)
    by_domain: dict[str, dict] = {}
    for a in articles:
        d = by_domain.setdefault(a.domain, {"articles": 0, "ke_done": 0, "ke_target": 0})
        d["articles"] += 1
        d["ke_done"] += eff_done(a)
        d["ke_target"] += a.ke_target
    by_group: dict[str, dict] = {g: {"articles": 0, "ke_done": 0, "ke_target": 0} for g in list_groups(db)}
    for a in articles:
        g = by_group.setdefault(a.group_name, {"articles": 0, "ke_done": 0, "ke_target": 0})
        g["articles"] += 1
        g["ke_done"] += eff_done(a)
        g["ke_target"] += a.ke_target
    by_stage = {s: 0 for s in STAGES}
    for a in articles:
        by_stage[a.stage] = by_stage.get(a.stage, 0) + 1
    cells = db.scalars(select(GoldScheduleCell)).all()
    cell_status = {"planned": 0, "done": 0, "missed": 0}
    for c in cells:
        if c.task:
            cell_status[c.status] = cell_status.get(c.status, 0) + 1
    open_issues = db.scalar(
        select(func.count()).select_from(GoldIssue).where(GoldIssue.status == "open")) or 0
    # M7 题级交叉核对：ke_tasks 已完成题数
    m7_done = db.scalar(
        select(func.count()).select_from(KeTask).where(KeTask.status == "已完成")) or 0
    samples = db.scalars(select(GoldSample).order_by(GoldSample.id.desc()).limit(5)).all()
    return {
        "baseline": settings.gold_baseline,          # 存量
        "target_total": settings.gold_target_total,  # 累计目标
        "target_new": settings.gold_target_new,      # 本期新增目标
        "new_done": new_done,
        "new_target": new_target,
        "total_estimate": settings.gold_baseline + new_done,
        "by_domain": by_domain,
        "by_group": by_group,
        "by_stage": by_stage,
        "stage_labels": STAGE_LABELS,
        "cell_status": cell_status,
        "open_issues": open_issues,
        "m7_done": m7_done,
        "article_auto": article_auto,  # 篇编号 → M7 系统自动完成数（供页面与自报数对照）
        "recent_samples": [
            {"id": s.id, "sample_date": s.sample_date, "scope": s.scope,
             "total": s.total, "passed": s.passed, "checker": s.checker}
            for s in samples
        ],
    }
