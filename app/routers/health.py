"""体检与使用统计 API：/api/health/*、/api/usage/scan、/api/history"""
from __future__ import annotations

import csv
import io
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit_dict
from ..db import get_db
from ..healthcheck import report_dict, run_health
from ..models import AuditLog, HealthReport
from ..security import require_role
from ..services import name_map
from ..usage import scan_usage

router = APIRouter(prefix="/api", tags=["health"])


class UsageScanRequest(BaseModel):
    text: str
    source: str = ""


def _latest_report(db: Session) -> HealthReport | None:
    return db.scalar(select(HealthReport).order_by(HealthReport.id.desc()).limit(1))


@router.post("/health/run")
def run_ep(db: Session = Depends(get_db), user: dict = Depends(require_role("reviewer", "admin"))):
    r = run_health(db, "manual")
    return report_dict(r, with_details=False)


@router.get("/health/latest")
def latest(db: Session = Depends(get_db), user: dict = Depends(require_role())):
    r = _latest_report(db)
    return {"report": report_dict(r) if r else None}


@router.get("/health/history")
def history(limit: int = Query(20, le=100), db: Session = Depends(get_db),
            user: dict = Depends(require_role())):
    rows = db.scalars(select(HealthReport).order_by(HealthReport.id.desc()).limit(limit)).all()
    return {"items": [report_dict(r, with_details=False) for r in rows]}


_CSV_KINDS = {
    "unused": (["id", "名称", "KE使用次数", "内部引用", "最近使用"],
               "unused",
               lambda d: [[x["id"], x["name"], 0, x["internal_refs"], x["last_used_at"] or ""] for x in d["unused"]]),
    "orphan": (["id", "名称", "别名"],
               "orphan",
               lambda d: [[x["id"], x["name"], "、".join(x["aliases"])] for x in d["orphan"]]),
    "similar": (["概念A", "领域A", "概念B", "领域B", "相似度", "命中方式", "命中值"],
                "similar_pairs",
                lambda d: [[x["a_name"], x["a_domain"], x["b_name"], x["b_domain"],
                            f"{x['score']:.2f}", x["matched_on"], x["matched_value"]]
                           for x in d["similar_pairs"]]),
    "cross_domain": (["概念A", "领域A", "概念B", "领域B", "相似度", "命中方式", "命中值"],
                     "cross_domain",
                     lambda d: [[x["a_name"], x["a_domain"], x["b_name"], x["b_domain"],
                                 f"{x['score']:.2f}", x["matched_on"], x["matched_value"]]
                                for x in d["cross_domain"]]),
    "operators": (["id", "名称", "算子数", "问题"],
                  "operator_count",
                  lambda d: [[x["id"], x["name"], x["operator_count"], x["issue"]] for x in d["operator_count"]]),
    "gold_validation": (["篇编号", "KE编号", "状态", "错误数", "警告数", "首个问题"],
                        "gold_validation",
                        lambda d: [[x["article_code"], x["ke_id"], x["status"], x["error_count"],
                                    x["warning_count"], x["first_issue"]] for x in d["gold_validation"]]),
}


@router.get("/health/report.csv")
def report_csv(kind: str, db: Session = Depends(get_db), user: dict = Depends(require_role())):
    if kind not in _CSV_KINDS:
        raise HTTPException(400, f"kind 取值：{' / '.join(_CSV_KINDS)}")
    r = _latest_report(db)
    if not r:
        raise HTTPException(404, "还没有体检报告，请先运行体检")
    header, _, rows_fn = _CSV_KINDS[kind]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows_fn(r.details))
    filename = f"health-{kind}-{r.id}.csv"
    return Response(
        "﻿" + buf.getvalue(),  # BOM：Excel 打开中文不乱码
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{quote(filename)}"},
    )


@router.post("/usage/scan")
def usage_scan(data: UsageScanRequest, db: Session = Depends(get_db),
               user: dict = Depends(require_role())):
    """粘贴/上传 KE 文本，按概念名+别名出现计数累加使用统计。"""
    return scan_usage(db, text=data.text, source=data.source)


@router.get("/history")
def global_history(entity_type: str | None = None, actor: str | None = None,
                   limit: int = Query(200, le=1000),
                   db: Session = Depends(get_db), user: dict = Depends(require_role())):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)
    rows = db.scalars(stmt).all()
    names = name_map(db, [r.entity_id for r in rows if r.entity_type == "concept"])
    return {"items": [{**audit_dict(r),
                       "concept_name": names.get(r.entity_id) if r.entity_type == "concept" else None}
                      for r in rows]}
