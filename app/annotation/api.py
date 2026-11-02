"""Agent JSON API：标注批次 / 单元 / CO / 对齐 / KE / GOLD / 版本。

页面路由继续服务 `/annotation/*`；本模块提供 `/api/annotation/*`，参数用 JSON，
供 agent/脚本调用。权限仍复用 annotation.service（批次成员、双盲、状态机）。
"""
from __future__ import annotations

import csv
import io
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (AnnotationBatch, AnnotationCrossReview, AnnotationEvidence,
                      AnnotationMember, AnnotationUnit, CoAlignment, CoAnnotation,
                      CoMergeLog, GoldKe, Issue, KeAnnotation, OntologyVersion,
                      QueryRun)
from ..security import require_role
from . import service
from .archive import build_batch_archive

router = APIRouter(prefix="/api/annotation", tags=["annotation-api"])


# ---------------- 请求模型 ----------------

class BatchCreate(BaseModel):
    batch_id: str | None = None
    name: str = Field(min_length=1)
    source_name: str = ""
    source_version: str = ""
    source_checksum: str = ""
    source_type: str = "policy"
    domain: str = "通用"
    guide_version: str = ""
    statistical_unit: str = "标注单元"
    acceptance_threshold: str = ""
    lead: str = ""
    annotator_a: str = ""
    annotator_b: str = ""
    arbiter: str = ""
    cross_checkers: list[str] | str = ""
    project_owner: str = ""
    note: str = ""


class MemberIn(BaseModel):
    username: str = Field(min_length=1)
    role: str = Field(min_length=1)
    side: str | None = None


class UnitIn(BaseModel):
    unit_id: str = Field(min_length=1)
    source_id: str = ""
    source_type: str = "policy"
    source_locator: dict | None = None
    raw_text: str = ""
    translation_text: str = ""
    language: str = "zh"
    source_payload: dict | None = None


class UnitsIn(BaseModel):
    units: list[UnitIn] = Field(default_factory=list)


class BulkUnitsIn(BaseModel):
    unit_ids: list[int] = Field(min_length=1, max_length=5000)


class EvidenceIn(BaseModel):
    source_id: str = ""
    locator: dict | None = None
    quote: str = ""
    language: str = "zh"
    kind: str = "text"


class COIn(BaseModel):
    side: Literal["A", "B"]
    decision: str = "include"
    concepts: list[str] | str = Field(default_factory=list)
    constants: list[str] | str = Field(default_factory=list)
    operators: list[str] | str = Field(default_factory=list)
    reason: str = ""
    evidence_ids: list[int] = Field(default_factory=list)


class AlignIn(BaseModel):
    conclusion: str = "aligned"
    decision: str = "include"
    concepts: list[str] | str = Field(default_factory=list)
    constants: list[str] | str = Field(default_factory=list)
    operators: list[str] | str = Field(default_factory=list)
    concept_rows: list[dict] | None = None
    operator_rows: list[dict] | None = None
    differences: list[str] | str = Field(default_factory=list)
    reason: str = ""
    evidence_ids: list[int] = Field(default_factory=list)


class KEIn(BaseModel):
    side: Literal["A", "B"]
    content: str = Field(min_length=1)
    evidence_ids: list[int] = Field(default_factory=list)
    execution: dict | None = None
    self_check: dict | None = None


class GoldIn(BaseModel):
    source: str = "A"
    content: str = Field(min_length=1)
    differences: list[str] | str = Field(default_factory=list)
    reason: str = ""
    evidence_ids: list[int] = Field(default_factory=list)


class RunQueryIn(BaseModel):
    side: Literal["A", "B"]


class StateIn(BaseModel):
    action: str = Field(min_length=1)
    return_state: str | None = None


class VersionIn(BaseModel):
    label: str = Field(min_length=1)
    note: str = ""
    parent_version_id: int | None = None
    import_batch_id: int | None = None


class ApplyIn(BaseModel):
    label: str | None = None


class CrossReviewIn(BaseModel):
    scope: str = ""
    conclusion: Literal["pass", "changes", "block"] = "pass"
    findings: list[str] | str = Field(default_factory=list)


class MergeLogIn(BaseModel):
    action: Literal["merge", "split", "rename", "reparent", "other"] = "merge"
    version_id: int | None = None
    source_units: list[str] | str = Field(default_factory=list)
    target: str = ""
    reason: str = ""


# ---------------- 序列化 ----------------

def _dt(v):
    return v.isoformat(timespec="seconds") if v else None


def _member(m: AnnotationMember) -> dict:
    return {"id": m.id, "username": m.username, "role": m.role,
            "side": m.side, "group_name": m.group_name,
            "created_at": _dt(m.created_at)}


def _batch(db: Session, b: AnnotationBatch, user: dict, *, detail: bool = False) -> dict:
    out = {
        "id": b.id, "batch_id": b.batch_id, "name": b.name,
        "source_name": b.source_name, "source_version": b.source_version,
        "source_checksum": b.source_checksum, "source_type": b.source_type,
        "domain": b.domain, "guide_version": b.guide_version,
        "statistical_unit": b.statistical_unit,
        "acceptance_threshold": b.acceptance_threshold,
        "state": b.state, "state_label": service.STATE_LABELS.get(b.state, b.state),
        "note": b.note, "created_by": b.created_by,
        "created_at": _dt(b.created_at), "frozen_at": _dt(b.frozen_at),
        "finished_at": _dt(b.finished_at),
        "stats": service.batch_stats(db, b),
        "members": [_member(m) for m in service._members(db, b.id)],
    }
    if detail:
        out["versions"] = [_version(v) for v in db.scalars(
            select(OntologyVersion).where(OntologyVersion.batch_id == b.id)
            .order_by(OntologyVersion.id.desc())).all()]
        out["merge_logs"] = [_merge_log(x) for x in db.scalars(
            select(CoMergeLog).where(CoMergeLog.batch_id == b.id)
            .order_by(CoMergeLog.id.desc())).all()]
        out["cross_reviews"] = [_cross(x) for x in db.scalars(
            select(AnnotationCrossReview).where(AnnotationCrossReview.batch_id == b.id)
            .order_by(AnnotationCrossReview.id.desc())).all()]
        out["roles"] = sorted(service.roles_for(db, b, user))
        out["can_manage"] = service.can_manage(db, b, user)
        out["can_cross_review"] = service.can_cross_review(db, b, user)
        out["can_project_approve"] = service.can_project_approve(db, b, user)
        out["can_view_unit_data"] = service.can_view_unit_data(db, b, user)
    return out


def _unit(u: AnnotationUnit) -> dict:
    return {
        "id": u.id, "batch_id": u.batch_id, "unit_id": u.unit_id,
        "source_id": u.source_id, "source_type": u.source_type,
        "source_locator": u.source_locator, "raw_text": u.raw_text,
        "translation_text": u.translation_text, "language": u.language,
        "source_payload": u.source_payload, "status": u.status,
        "status_label": service.UNIT_STATUS_LABELS.get(u.status, u.status),
        "created_at": _dt(u.created_at),
    }


def _evidence(e: AnnotationEvidence) -> dict:
    return {"id": e.id, "batch_id": e.batch_id, "unit_id": e.unit_id,
            "source_id": e.source_id, "locator": e.locator, "quote": e.quote,
            "language": e.language, "kind": e.kind, "created_by": e.created_by,
            "created_at": _dt(e.created_at)}


def _co(c: CoAnnotation) -> dict:
    return {"id": c.id, "batch_id": c.batch_id, "unit_id": c.unit_id,
            "side": c.side, "version": c.version, "is_current": c.is_current,
            "decision": c.decision, "concepts": c.concepts or [],
            "constants": c.constants or [], "operators": c.operators or [],
            "reason": c.reason, "evidence_ids": c.evidence_ids or [],
            "annotator": c.annotator, "guide_version": c.guide_version,
            "created_at": _dt(c.created_at)}


def _alignment(a: CoAlignment) -> dict:
    return {"id": a.id, "batch_id": a.batch_id, "unit_id": a.unit_id,
            "version": a.version, "is_current": a.is_current,
            "co_a_id": a.co_a_id, "co_b_id": a.co_b_id,
            "conclusion": a.conclusion, "aligned": a.aligned or {},
            "differences": a.differences or [], "reason": a.reason,
            "evidence_ids": a.evidence_ids or [], "decided_by": a.decided_by,
            "guide_version": a.guide_version, "created_at": _dt(a.created_at)}


def _ke(k: KeAnnotation) -> dict:
    return {"id": k.id, "batch_id": k.batch_id, "unit_id": k.unit_id,
            "side": k.side, "version": k.version, "is_current": k.is_current,
            "content": k.content, "evidence_ids": k.evidence_ids or [],
            "execution": k.execution, "self_check": k.self_check,
            "annotator": k.annotator, "created_at": _dt(k.created_at)}


def _gold(g: GoldKe) -> dict:
    return {"id": g.id, "batch_id": g.batch_id, "unit_id": g.unit_id,
            "version": g.version, "is_current": g.is_current,
            "source": g.source, "content": g.content,
            "differences": g.differences or [], "reason": g.reason,
            "evidence_ids": g.evidence_ids or [], "decided_by": g.decided_by,
            "created_at": _dt(g.created_at)}


def _run(r: QueryRun) -> dict:
    return {"id": r.id, "batch_id": r.batch_id, "unit_id": r.unit_id,
            "side": r.side, "source_type": r.source_type, "command": r.command,
            "status": r.status, "stdout": r.stdout, "stderr": r.stderr,
            "result": r.result, "created_by": r.created_by,
            "created_at": _dt(r.created_at)}


def _version(v: OntologyVersion) -> dict:
    return {"id": v.id, "batch_id": v.batch_id, "label": v.label,
            "status": v.status, "parent_version_id": v.parent_version_id,
            "import_batch_id": v.import_batch_id,
            "counts": v.counts or {}, "snapshot": v.snapshot or {},
            "has_payload": bool(v.payload),
            "validation": v.validation or {}, "merge_log": v.merge_log or [],
            "note": v.note, "created_by": v.created_by,
            "created_at": _dt(v.created_at), "frozen_at": _dt(v.frozen_at),
            "released_at": _dt(v.released_at)}


def _cross(r: AnnotationCrossReview) -> dict:
    return {"id": r.id, "batch_id": r.batch_id, "reviewer": r.reviewer,
            "scope": r.scope, "conclusion": r.conclusion,
            "findings": r.findings or [], "created_at": _dt(r.created_at)}


def _merge_log(l: CoMergeLog) -> dict:
    return {"id": l.id, "batch_id": l.batch_id, "version_id": l.version_id,
            "action": l.action, "source_units": l.source_units or [],
            "target": l.target, "reason": l.reason, "created_by": l.created_by,
            "created_at": _dt(l.created_at)}


# ---------------- 批次 / 成员 / 单元 ----------------

@router.get("/batches")
def list_batches(q: str | None = None, state: str | None = None,
                 page: int = 1, size: int = Query(50, ge=1, le=200),
                 db: Session = Depends(get_db), user: dict = Depends(require_role())):
    if user.get("role") == "admin":
        stmt = select(AnnotationBatch)
    else:
        ids = db.scalars(select(AnnotationMember.batch_id).where(
            AnnotationMember.username == user["username"]).distinct()).all()
        if not ids:
            return {"items": [], "total": 0, "page": page, "size": size}
        stmt = select(AnnotationBatch).where(AnnotationBatch.id.in_(ids))
    if q:
        stmt = stmt.where(AnnotationBatch.name.like(f"%{q}%"))
    if state:
        stmt = stmt.where(AnnotationBatch.state == state)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AnnotationBatch.id.desc())
                      .limit(size).offset(max(0, page - 1) * size)).all()
    return {"items": [_batch(db, b, user) for b in rows], "total": total,
            "page": page, "size": size}


@router.post("/batches", status_code=201)
def create_batch(data: BatchCreate, db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    payload = data.model_dump()
    if isinstance(payload.get("cross_checkers"), list):
        payload["cross_checkers"] = ",".join(payload["cross_checkers"])
    batch = service.create_batch(db, payload, user)
    return _batch(db, batch, user, detail=True)


@router.get("/batches/{bid}")
def get_batch(bid: int, db: Session = Depends(get_db),
              user: dict = Depends(require_role())):
    batch = service.get_batch_or_404(db, bid)
    service.require_view_batch(db, batch, user)
    return _batch(db, batch, user, detail=True)


@router.post("/batches/{bid}/members", status_code=201)
def add_member(bid: int, data: MemberIn, db: Session = Depends(get_db),
               user: dict = Depends(require_role())):
    batch = service.get_batch_or_404(db, bid)
    service.add_member(db, batch, data.model_dump(), user)
    members = [m for m in service._members(db, bid)
               if m.username == data.username and m.role == data.role]
    return _member(members[-1]) if members else {"ok": True}


@router.get("/batches/{bid}/units")
def list_units(bid: int, page: int = 1, size: int = Query(100, ge=1, le=500),
               db: Session = Depends(get_db), user: dict = Depends(require_role())):
    batch = service.get_batch_or_404(db, bid)
    service.require_view_batch(db, batch, user)
    can_view = service.can_view_unit_data(db, batch, user)
    rows = service.unit_rows(db, batch)
    total = len(rows)
    rows = rows[(page - 1) * size: page * size]
    items = []
    for row in rows:
        item = _unit(row["unit"])
        if not can_view:
            for k in ("source_locator", "source_payload"):
                item[k] = None
            item["raw_text"] = item["translation_text"] = ""
        for k in ("co_a", "co_b", "aligned", "ke_a", "ke_b", "gold"):
            item[k] = row[k] if can_view else False
        items.append(item)
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/batches/{bid}/units")
def import_units(bid: int, data: UnitsIn, db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    batch = service.get_batch_or_404(db, bid)
    stats = service.import_units(db, batch, [u.model_dump() for u in data.units], user)
    return stats


@router.post("/batches/{bid}/state")
def advance_state(bid: int, data: StateIn, db: Session = Depends(get_db),
                  user: dict = Depends(require_role())):
    batch = service.get_batch_or_404(db, bid)
    service.advance_state(db, batch, data.action, user, data.return_state)
    db.refresh(batch)
    return {"id": batch.id, "state": batch.state,
            "state_label": service.STATE_LABELS.get(batch.state, batch.state)}


@router.post("/batches/{bid}/apply")
def apply_alignments(bid: int, data: ApplyIn, db: Session = Depends(get_db),
                     user: dict = Depends(require_role())):
    batch = service.get_batch_or_404(db, bid)
    res = service.apply_alignments(db, batch, user, data.label)
    version = service.get_version_or_404(db, res["version_id"])
    return {**res, "version": _version(version)}


@router.post("/batches/{bid}/versions", status_code=201)
def create_version(bid: int, data: VersionIn, db: Session = Depends(get_db),
                   user: dict = Depends(require_role())):
    batch = service.get_batch_or_404(db, bid)
    v = service.create_version(db, batch, data.model_dump(), user)
    return _version(v)


@router.post("/batches/{bid}/cross-review", status_code=201)
def add_cross_review(bid: int, data: CrossReviewIn, db: Session = Depends(get_db),
                     user: dict = Depends(require_role())):
    batch = service.get_batch_or_404(db, bid)
    return _cross(service.add_cross_review(db, batch, data.model_dump(), user))


@router.post("/batches/{bid}/merge-log", status_code=201)
def add_merge_log(bid: int, data: MergeLogIn, db: Session = Depends(get_db),
                  user: dict = Depends(require_role())):
    batch = service.get_batch_or_404(db, bid)
    return _merge_log(service.add_merge_log(db, batch, data.model_dump(), user))


@router.get("/batches/{bid}/export")
def export_batch(bid: int, db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    batch = service.get_batch_or_404(db, bid)
    if not (service.can_manage(db, batch, user) or service.can_project_approve(db, batch, user)):
        raise HTTPException(403, "仅组长/批次负责人/项目负责人可导出归档")
    return Response(build_batch_archive(db, batch), media_type="application/zip",
                    headers={"Content-Disposition":
                             f'attachment; filename="annotation-{batch.batch_id}.zip"'})


# ---------------- 单元 ----------------

@router.get("/units/{uid}")
def get_unit(uid: int, db: Session = Depends(get_db),
             user: dict = Depends(require_role())):
    unit = service.get_unit_or_404(db, uid)
    batch = service.get_batch_or_404(db, unit.batch_id)
    service.require_view_batch(db, batch, user)
    ctx = service.unit_context(db, unit, user)
    unit_data = _unit(unit)
    if not ctx["can_view_data"]:
        for k in ("source_locator", "source_payload"):
            unit_data[k] = None
        unit_data["raw_text"] = unit_data["translation_text"] = ""
    return {
        "unit": unit_data,
        "batch": _batch(db, ctx["batch"], user),
        "evidence": [_evidence(e) for e in ctx["evidence"]] if ctx["can_view_data"] else [],
        "co": {s: _co(c) for s, c in (("A", ctx["co_a"]), ("B", ctx["co_b"])) if c}
              if ctx["can_view_data"] else {},
        "alignment": _alignment(ctx["alignment"]) if ctx["alignment"] else None,
        "ke": {s: _ke(k) for s, k in (("A", ctx["ke_a"]), ("B", ctx["ke_b"])) if k}
              if ctx["can_view_data"] else {},
        "gold": _gold(ctx["gold"]) if ctx["gold"] else None,
        "runs": [_run(r) for r in ctx["runs"]] if ctx["can_view_data"] else [],
        "state": ctx["state"], "state_label": ctx["state_label"],
        "my_side": ctx["my_side"], "can_view_data": ctx["can_view_data"],
        "can_submit_co": ctx["can_submit_co"], "can_align": ctx["can_align"],
        "can_submit_ke": ctx["can_submit_ke"],
        "can_submit_gold": ctx["can_submit_gold"],
        "can_run_query": ctx["can_run_query"],
        "can_add_evidence": ctx["can_add_evidence"],
    }


@router.get("/units/{uid}/evidence")
def list_evidence(uid: int, db: Session = Depends(get_db),
                  user: dict = Depends(require_role())):
    unit = service.get_unit_or_404(db, uid)
    batch = service.get_batch_or_404(db, unit.batch_id)
    service.require_view_batch(db, batch, user)
    if not service.can_view_unit_data(db, batch, user):
        raise HTTPException(403, "当前阶段无权查看该单元数据")
    return {"items": [_evidence(e) for e in service.list_evidence(db, unit)]}


@router.get("/batches/{bid}/units/export")
def export_units(bid: int, format: str = "json", db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    batch = service.get_batch_or_404(db, bid)
    if not (service.can_manage(db, batch, user) or service.can_project_approve(db, batch, user)):
        raise HTTPException(403, "仅组长/批次负责人/项目负责人可导出单元")
    units = service.list_units(db, batch)
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["unit_id", "source_id", "source_type", "source_locator", "raw_text",
                    "translation_text", "language", "source_payload", "status"])
        for u in units:
            w.writerow([u.unit_id, u.source_id, u.source_type,
                        json.dumps(u.source_locator or {}, ensure_ascii=False),
                        u.raw_text, u.translation_text, u.language,
                        json.dumps(u.source_payload or {}, ensure_ascii=False), u.status])
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":
                                 f'attachment; filename="units-{batch.batch_id}.csv"'})
    return {"batch_id": batch.batch_id, "items": [_unit(u) for u in units]}


@router.post("/batches/{bid}/units/bulk-delete")
def bulk_delete_units(bid: int, data: BulkUnitsIn, db: Session = Depends(get_db),
                      user: dict = Depends(require_role())):
    batch = service.get_batch_or_404(db, bid)
    return service.bulk_delete_units(db, batch, data.unit_ids, user)


@router.post("/units/{uid}/evidence", status_code=201)
def add_evidence(uid: int, data: EvidenceIn, db: Session = Depends(get_db),
                 user: dict = Depends(require_role())):
    unit = service.get_unit_or_404(db, uid)
    return _evidence(service.add_evidence(db, unit, data.model_dump(), user))


@router.post("/units/{uid}/co", status_code=201)
def submit_co(uid: int, data: COIn, db: Session = Depends(get_db),
              user: dict = Depends(require_role())):
    unit = service.get_unit_or_404(db, uid)
    return _co(service.submit_co(db, unit, data.side, data.model_dump(), user))


@router.post("/units/{uid}/align", status_code=201)
def submit_alignment(uid: int, data: AlignIn, db: Session = Depends(get_db),
                     user: dict = Depends(require_role())):
    unit = service.get_unit_or_404(db, uid)
    return _alignment(service.submit_alignment(db, unit, data.model_dump(), user))


@router.post("/units/{uid}/ke", status_code=201)
def submit_ke(uid: int, data: KEIn, db: Session = Depends(get_db),
              user: dict = Depends(require_role())):
    unit = service.get_unit_or_404(db, uid)
    return _ke(service.submit_ke(db, unit, data.side, data.model_dump(), user))


@router.post("/units/{uid}/gold", status_code=201)
def submit_gold(uid: int, data: GoldIn, db: Session = Depends(get_db),
                user: dict = Depends(require_role())):
    unit = service.get_unit_or_404(db, uid)
    return _gold(service.submit_gold(db, unit, data.model_dump(), user))


@router.post("/units/{uid}/run-query", status_code=201)
def run_query(uid: int, data: RunQueryIn, db: Session = Depends(get_db),
              user: dict = Depends(require_role())):
    unit = service.get_unit_or_404(db, uid)
    return _run(service.run_query(db, unit, data.side, user))


@router.get("/units/{uid}/runs")
def list_runs(uid: int, db: Session = Depends(get_db),
              user: dict = Depends(require_role())):
    unit = service.get_unit_or_404(db, uid)
    batch = service.get_batch_or_404(db, unit.batch_id)
    service.require_view_batch(db, batch, user)
    ctx = service.unit_context(db, unit, user)
    return {"items": [_run(r) for r in ctx["runs"]] if ctx["can_view_data"] else []}


# ---------------- 版本 ----------------

@router.get("/versions")
def list_versions(batch_id: int | None = None, status: str | None = None,
                  page: int = 1, size: int = Query(50, ge=1, le=200),
                  db: Session = Depends(get_db), user: dict = Depends(require_role())):
    stmt = select(OntologyVersion)
    if user.get("role") != "admin":
        ids = db.scalars(select(AnnotationMember.batch_id).where(
            AnnotationMember.username == user["username"]).distinct()).all()
        if not ids:
            return {"items": [], "total": 0, "page": page, "size": size}
        stmt = stmt.where(OntologyVersion.batch_id.in_(ids))
    if batch_id:
        stmt = stmt.where(OntologyVersion.batch_id == batch_id)
    if status:
        stmt = stmt.where(OntologyVersion.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(OntologyVersion.id.desc())
                      .limit(size).offset(max(0, page - 1) * size)).all()
    return {"items": [_version(v) for v in rows], "total": total,
            "page": page, "size": size}


@router.get("/versions/{vid}")
def get_version(vid: int, db: Session = Depends(get_db),
                user: dict = Depends(require_role())):
    v = service.get_version_or_404(db, vid)
    batch = service.get_batch_or_404(db, v.batch_id)
    service.require_view_batch(db, batch, user)
    return _version(v)


@router.get("/versions/{vid}/payload")
def get_version_payload(vid: int, db: Session = Depends(get_db),
                        user: dict = Depends(require_role())):
    v = service.get_version_or_404(db, vid)
    batch = service.get_batch_or_404(db, v.batch_id)
    service.require_view_batch(db, batch, user)
    if v.status not in {"frozen", "released"}:
        raise HTTPException(400, "只有冻结/已发布版本可导出完整快照")
    if not v.payload:
        raise HTTPException(404, "该版本没有完整快照（历史版本需重新生成）")
    return v.payload


@router.get("/versions/{vid}/diff")
def diff_version(vid: int, base_version_id: int | None = None,
                 db: Session = Depends(get_db), user: dict = Depends(require_role())):
    v = service.get_version_or_404(db, vid)
    batch = service.get_batch_or_404(db, v.batch_id)
    service.require_view_batch(db, batch, user)
    bid = base_version_id or v.parent_version_id
    if not bid:
        raise HTTPException(400, "请指定 base_version_id（或版本存在父版本）")
    base = service.get_version_or_404(db, bid)
    base_batch = service.get_batch_or_404(db, base.batch_id)
    service.require_view_batch(db, base_batch, user)
    if not base.payload or not v.payload:
        raise HTTPException(400, "版本缺少完整快照，不能比较")
    return {"base": _version(base), "target": _version(v),
            "diff": service.diff_version_payloads(base.payload, v.payload)}


@router.post("/versions/{vid}/rollback", status_code=201)
def rollback_version(vid: int, db: Session = Depends(get_db),
                     user: dict = Depends(require_role())):
    v = service.get_version_or_404(db, vid)
    return _version(service.rollback_version(db, v, user))


@router.post("/versions/{vid}/freeze")

def freeze_version(vid: int, db: Session = Depends(get_db),
                   user: dict = Depends(require_role())):
    v = service.get_version_or_404(db, vid)
    service.freeze_version(db, v, user)
    db.refresh(v)
    return _version(v)


@router.post("/versions/{vid}/release")
def release_version(vid: int, db: Session = Depends(get_db),
                    user: dict = Depends(require_role())):
    v = service.get_version_or_404(db, vid)
    service.release_version(db, v, user)
    db.refresh(v)
    return _version(v)
