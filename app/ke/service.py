"""上传事务与查询：CSV 文本 → 行 dict；按组全量替换；看板数据组装。"""
from __future__ import annotations

import csv
import io
import re
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import KeTask, KeUpload, User
from .metrics import STATUSES, build_data, normalize, parse_dt

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 单文件上限 5MB

# 必需规范字段（列名由 settings.ke_column_map 决定，别的团队可用 KE_COLUMN_MAP 换表头）
REQUIRED_FIELDS = ["id", "domain", "summary", "status", "person",
                   "created", "started", "finished", "review", "tags"]


def decode_upload(raw: bytes) -> str:
    """CSV 解码：优先 UTF-8（含 BOM），失败回退 GB18030（Excel 中文导出常见）。"""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("文件编码无法识别（请使用 UTF-8 或 GBK 编码的 CSV）")


def _canon_status(raw: str) -> str:
    """CSV 状态写法 → 规范值（KE_STATUS_MAP：别家写 done/完成 也能入）。"""
    for values in settings.ke_status_map.values():
        if raw in values:
            return values[0]
    return raw


def parse_csv_text(text: str, column_map: dict | None = None):
    """CSV 文本 → (rows, warnings)；缺必需列抛 ValueError。

    列名走 column_map（默认 settings.ke_column_map）：其他团队的表头可在
    KE_COLUMN_MAP 里映射，无需改代码。「篇编号」「KE内容」为可选列。
    """
    cmap = {**settings.ke_column_map, **(column_map or {})}   # 单项覆盖默认表头（与 KE_COLUMN_MAP 同语义）

    def col(raw: dict, canon: str) -> str:
        return (raw.get(cmap.get(canon, canon)) or "").strip()

    warnings, rows = [], []
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    missing = [cmap.get(c, c) for c in REQUIRED_FIELDS if cmap.get(c, c) not in headers]
    if missing:
        raise ValueError(f"CSV 缺少必需列: {missing}（列名可用 KE_COLUMN_MAP 映射自有表头）")
    for line_no, raw in enumerate(reader, 2):
        rid = col(raw, "id")
        if not rid:
            warnings.append(f"第{line_no}行: 缺少题目ID，已跳过")
            continue
        status = _canon_status(col(raw, "status"))
        if status not in STATUSES:
            warnings.append(f"{rid}: 标注状态 {status!r} 非法，已按「待标注」处理")
            status = "待标注"
        content = col(raw, "content")
        article = col(raw, "article")
        if content and not article:
            warnings.append(f"{rid}: 有 KE内容 但无篇编号，未入成品库")
        rows.append({
            "id": rid,
            "domain": col(raw, "domain") or "未分类",
            "summary": col(raw, "summary"),
            "status": status,
            "person": col(raw, "person") or "未分配",
            "created": parse_dt(col(raw, "created"), warnings, rid, "创建时间"),
            "started": parse_dt(col(raw, "started"), warnings, rid, "开始时间"),
            "finished": parse_dt(col(raw, "finished"), warnings, rid, "完成时间"),
            "review": col(raw, "review"),
            "tags": [t.strip() for t in re.split(r"[;;]", col(raw, "tags")) if t.strip()],
            "article": article,
            "content": content,
        })
    return rows, warnings


def replace_group_tasks(db: Session, group: str, rows, filename: str,
                        uploader: str, warnings) -> KeUpload:
    """全量替换该组题目 + 记录上传批次（同事务；重复上传幂等，以最新一次为准）。

    M10 口径：入库前重放系统复核结论（组内表自报不覆盖系统复核）。"""
    from .review import apply_latest_reviews   # 避免模块级循环依赖
    apply_latest_reviews(db, group, rows, warnings)
    done = sum(1 for r in rows if r["status"] == "已完成")
    up = KeUpload(group_name=group, filename=filename, row_count=len(rows),
                  done_count=done, warnings=list(warnings), uploaded_by=uploader)
    db.add(up)
    db.flush()  # 取 up.id
    db.execute(delete(KeTask).where(KeTask.group_name == group))
    for r in rows:
        db.add(KeTask(group_name=group, ke_id=r["id"], domain=r["domain"],
                      summary=r["summary"], status=r["status"], person=r["person"],
                      created_dt=r["created"], started_dt=r["started"],
                      finished_dt=r["finished"], review=r["review"],
                      tags=r["tags"], article_code=r.get("article", ""), upload_id=up.id))
    db.commit()
    db.refresh(up)  # 载入 server_default 的 uploaded_at
    return up


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _group_key(name: str):
    """「一组/二组/三组」等中文数字组名按数值排序，其余按字典序。"""
    s = (name or "").strip()
    if len(s) >= 2 and s[0] in _CN_NUM and s[1:] in ("组", "队", "部"):
        return (0, _CN_NUM[s[0]], s)
    return (1, 0, s)


def list_groups(db: Session) -> list[str]:
    """组清单：用户表组字段 ∪ 已有任务的组（用户表为主，任务表兜底）。"""
    g1 = {g for (g,) in db.execute(
        select(User.group_name).where(User.group_name.is_not(None)).distinct())}
    g2 = {g for (g,) in db.execute(select(KeTask.group_name).distinct())}
    return sorted(g1 | g2, key=_group_key)


def rows_for(db: Session, group: str | None = None) -> list[dict]:
    """查任务行并还原为 metrics 需要的行 dict（group=None 表示全部组）。"""
    stmt = select(KeTask)
    if group:
        stmt = stmt.where(KeTask.group_name == group)
    return [{"id": t.ke_id, "domain": t.domain, "summary": t.summary, "status": t.status,
             "person": t.person, "created": t.created_dt, "started": t.started_dt,
             "finished": t.finished_dt, "review": t.review or "", "tags": list(t.tags or []),
             "article": t.article_code or ""}
            for t in db.scalars(stmt).all()]


def group_status(db: Session, today: date) -> list[dict]:
    """每组最近一条上传 + 今日是否已上传（未上传提醒的数据源）。"""
    out = []
    for g in list_groups(db):
        last = db.scalar(select(KeUpload).where(KeUpload.group_name == g)
                         .order_by(KeUpload.id.desc()).limit(1))
        out.append({"name": g, "last_upload": last,
                    "uploaded_today": bool(last and last.uploaded_at
                                           and last.uploaded_at.date() == today)})
    return out


def upload_history(db: Session, group: str | None = None, limit: int = 20):
    stmt = select(KeUpload).order_by(KeUpload.id.desc()).limit(limit)
    if group:
        stmt = (select(KeUpload).where(KeUpload.group_name == group)
                .order_by(KeUpload.id.desc()).limit(limit))
    return db.scalars(stmt).all()


def dashboard_payload(db: Session, group: str | None, today: date, deadline: date) -> dict:
    """看板数据：DB 行 → 归一化 → 指标（与独立版同口径）。"""
    rows = rows_for(db, group)
    warnings: list[str] = []
    normalize(rows, today, warnings)
    return build_data(rows, warnings, today, deadline)
