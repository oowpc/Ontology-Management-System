"""SOP 手工标注工作流页面。"""
from __future__ import annotations

import io
import json
import zipfile
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (AnnotationBatch, AnnotationCrossReview, AnnotationEvidence,
                      AnnotationMember, AnnotationUnit, AuditLog, CoAlignment,
                      CoAnnotation, CoMergeLog, GoldKe, Issue, KeAnnotation,
                      OntologyVersion, QueryRun, User)
from ..security import current_user
from ..templating import templates
from . import service

router = APIRouter(prefix="/annotation", tags=["annotation"])


def _back(path: str, ok: str | None = None, err: str | None = None):
    q = ""
    if ok:
        q = "?ok=" + quote_plus(ok)
    elif err:
        q = "?err=" + quote_plus(str(err))
    return RedirectResponse(path + q, status_code=303)


def _usernames(db: Session) -> list[str]:
    return list(db.scalars(select(User.username).order_by(User.username)))


def _dict(obj) -> dict:
    out = {}
    for col in obj.__table__.columns:
        v = getattr(obj, col.name)
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        out[col.name] = v
    return out


@router.get("")
def list_batches(request: Request, db: Session = Depends(get_db),
                 user: dict = Depends(current_user)):
    if user.get("role") == "admin":
        batches = db.scalars(select(AnnotationBatch).order_by(AnnotationBatch.id.desc())).all()
    else:
        ids = db.scalars(select(AnnotationMember.batch_id).where(
            AnnotationMember.username == user["username"]).distinct()).all()
        batches = db.scalars(select(AnnotationBatch).where(
            AnnotationBatch.id.in_(ids)).order_by(AnnotationBatch.id.desc())).all() if ids else []
    summaries = [service.batch_summary(db, b) for b in batches]
    return templates.TemplateResponse(request=request, name="annotation/list.html", context={
        "request": request, "user": user, "batches": summaries,
        "can_create": service.can_create_batch(user), "usernames": _usernames(db),
        "STATE_LABELS": service.STATE_LABELS,
    })


@router.post("/batches")
async def create_batch(request: Request, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    form = await request.form()
    try:
        batch = service.create_batch(db, {k: form.get(k) for k in (
            "batch_id", "name", "source_name", "source_version", "source_checksum",
            "source_type", "domain", "guide_version", "statistical_unit",
            "acceptance_threshold", "annotator_a", "annotator_b", "lead", "arbiter",
            "cross_checkers", "project_owner", "note")}, user)
    except HTTPException as e:
        return _back("/annotation", err=e.detail)
    return _back(f"/annotation/batches/{batch.id}", ok="批次已创建")


@router.get("/batches/{bid}")
def batch_detail(bid: int, request: Request, db: Session = Depends(get_db),
                 user: dict = Depends(current_user)):
    batch = service.get_batch_or_404(db, bid)
    service.require_view_batch(db, batch, user)
    members = service._members(db, batch.id)
    units = service.unit_rows(db, batch)
    versions = db.scalars(select(OntologyVersion).where(
        OntologyVersion.batch_id == batch.id).order_by(OntologyVersion.id.desc())).all()
    merge_logs = db.scalars(select(CoMergeLog).where(
        CoMergeLog.batch_id == batch.id).order_by(CoMergeLog.id.desc())).all()
    reviews = db.scalars(select(AnnotationCrossReview).where(
        AnnotationCrossReview.batch_id == batch.id).order_by(AnnotationCrossReview.id.desc())).all()
    return templates.TemplateResponse(request=request, name="annotation/batch.html", context={
        "request": request, "user": user, "batch": batch, "members": members,
        "units": units, "versions": versions, "merge_logs": merge_logs, "reviews": reviews,
        "stats": service.batch_stats(db, batch),
        "roles": service.roles_for(db, batch, user),
        "can_manage": service.can_manage(db, batch, user),
        "can_cross": service.can_cross_review(db, batch, user),
        "can_approve": service.can_project_approve(db, batch, user),
        "show_unit_data": service.can_view_unit_data(db, batch, user),
        "usernames": _usernames(db),
        "STATE_LABELS": service.STATE_LABELS,
        "UNIT_STATUS_LABELS": service.UNIT_STATUS_LABELS,
        "MEMBER_ROLE_LABELS": service.MEMBER_ROLE_LABELS,
    })


@router.post("/batches/{bid}/members")
async def add_member(bid: int, request: Request, db: Session = Depends(get_db),
                     user: dict = Depends(current_user)):
    batch = service.get_batch_or_404(db, bid)
    form = await request.form()
    try:
        service.add_member(db, batch, {"username": form.get("username"),
                                       "role": form.get("role"),
                                       "side": form.get("side")}, user)
    except HTTPException as e:
        return _back(f"/annotation/batches/{bid}", err=e.detail)
    return _back(f"/annotation/batches/{bid}", ok="成员已添加")


@router.post("/batches/{bid}/units/import")
async def import_units(bid: int, request: Request, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    batch = service.get_batch_or_404(db, bid)
    form = await request.form()
    try:
        rows = json.loads(str(form.get("units_json") or ""))
        if not isinstance(rows, list):
            raise ValueError("顶层必须是数组")
        stats = service.import_units(db, batch, rows, user)
    except HTTPException as e:
        return _back(f"/annotation/batches/{bid}", err=e.detail)
    except Exception as e:  # noqa: BLE001
        return _back(f"/annotation/batches/{bid}", err=f"JSON 解析失败：{e}")
    return _back(f"/annotation/batches/{bid}",
                 ok=f"单元导入完成：新建 {stats['created']} / 更新 {stats['updated']}")


@router.post("/batches/{bid}/state")
async def advance_state(bid: int, request: Request, db: Session = Depends(get_db),
                        user: dict = Depends(current_user)):
    batch = service.get_batch_or_404(db, bid)
    form = await request.form()
    try:
        service.advance_state(db, batch, str(form.get("action") or ""), user,
                              str(form.get("return_state") or "") or None)
    except HTTPException as e:
        return _back(f"/annotation/batches/{bid}", err=e.detail)
    return _back(f"/annotation/batches/{bid}", ok="状态已更新")


@router.post("/batches/{bid}/versions")
async def create_version(bid: int, request: Request, db: Session = Depends(get_db),
                         user: dict = Depends(current_user)):
    batch = service.get_batch_or_404(db, bid)
    form = await request.form()
    try:
        def _int(v):
            s = str(v or "").strip()
            return int(s) if s.isdigit() else None
        service.create_version(db, batch, {
            "label": form.get("label"), "note": form.get("note"),
            "import_batch_id": _int(form.get("import_batch_id")),
            "parent_version_id": _int(form.get("parent_version_id")),
        }, user)
    except HTTPException as e:
        return _back(f"/annotation/batches/{bid}", err=e.detail)
    return _back(f"/annotation/batches/{bid}", ok="版本已创建（草稿）")


@router.post("/versions/{vid}/freeze")
def freeze_version(vid: int, db: Session = Depends(get_db),
                   user: dict = Depends(current_user)):
    version = service.get_version_or_404(db, vid)
    try:
        service.freeze_version(db, version, user)
    except HTTPException as e:
        return _back(f"/annotation/batches/{version.batch_id}", err=e.detail)
    return _back(f"/annotation/batches/{version.batch_id}", ok="版本已冻结")


@router.post("/versions/{vid}/release")
def release_version(vid: int, db: Session = Depends(get_db),
                    user: dict = Depends(current_user)):
    version = service.get_version_or_404(db, vid)
    try:
        service.release_version(db, version, user)
    except HTTPException as e:
        return _back(f"/annotation/batches/{version.batch_id}", err=e.detail)
    return _back(f"/annotation/batches/{version.batch_id}", ok="版本已发布")


@router.post("/batches/{bid}/merge-log")
async def add_merge_log(bid: int, request: Request, db: Session = Depends(get_db),
                        user: dict = Depends(current_user)):
    batch = service.get_batch_or_404(db, bid)
    form = await request.form()
    try:
        vid = str(form.get("version_id") or "").strip()
        service.add_merge_log(db, batch, {
            "action": form.get("action"), "version_id": int(vid) if vid.isdigit() else None,
            "source_units": form.get("source_units"), "target": form.get("target"),
            "reason": form.get("reason"),
        }, user)
    except HTTPException as e:
        return _back(f"/annotation/batches/{bid}", err=e.detail)
    return _back(f"/annotation/batches/{bid}", ok="合并日志已记录")


@router.post("/batches/{bid}/cross-review")
async def add_cross_review(bid: int, request: Request, db: Session = Depends(get_db),
                           user: dict = Depends(current_user)):
    batch = service.get_batch_or_404(db, bid)
    form = await request.form()
    try:
        service.add_cross_review(db, batch, {
            "scope": form.get("scope"), "conclusion": form.get("conclusion"),
            "findings": form.get("findings"),
        }, user)
    except HTTPException as e:
        return _back(f"/annotation/batches/{bid}", err=e.detail)
    return _back(f"/annotation/batches/{bid}", ok="交叉检查结论已记录")


@router.get("/versions")
def versions_page(request: Request, db: Session = Depends(get_db),
                  user: dict = Depends(current_user)):
    if user.get("role") == "admin":
        versions = db.scalars(select(OntologyVersion).order_by(
            OntologyVersion.id.desc())).all()
    else:
        ids = db.scalars(select(AnnotationMember.batch_id).where(
            AnnotationMember.username == user["username"]).distinct()).all()
        versions = db.scalars(select(OntologyVersion).where(
            OntologyVersion.batch_id.in_(ids)).order_by(
            OntologyVersion.id.desc())).all() if ids else []
    rows = []
    for v in versions:
        b = db.get(AnnotationBatch, v.batch_id)
        rows.append({"version": v, "batch": b,
                     "can_manage": bool(b and service.can_manage(db, b, user)),
                     "can_approve": bool(b and service.can_project_approve(db, b, user))})
    return templates.TemplateResponse(request=request, name="annotation/versions.html", context={
        "request": request, "user": user, "rows": rows,
    })


@router.post("/batches/{bid}/apply")
async def apply_alignments(bid: int, request: Request, db: Session = Depends(get_db),
                           user: dict = Depends(current_user)):
    batch = service.get_batch_or_404(db, bid)
    form = await request.form()
    try:
        res = service.apply_alignments(db, batch, user,
                                       str(form.get("label") or "") or None)
    except HTTPException as e:
        return _back(f"/annotation/batches/{bid}", err=e.detail)
    st = res["stats"]
    return _back(f"/annotation/batches/{bid}",
                 ok=f"已应用：概念 +{st['concepts_created']} / 算子 +{st['operators_created']}；"
                    f"版本 {res['label']}（草稿，需冻结）")


@router.get("/batches/{bid}/export")
def export_batch(bid: int, db: Session = Depends(get_db),
                 user: dict = Depends(current_user)):
    batch = service.get_batch_or_404(db, bid)
    if not (service.can_manage(db, batch, user) or service.can_project_approve(db, batch, user)):
        raise HTTPException(403, "仅组长/批次负责人/项目负责人可导出归档")
    units = service.list_units(db, batch)
    unit_ids = [u.id for u in units]
    evidence = db.scalars(select(AnnotationEvidence).where(
        AnnotationEvidence.batch_id == bid).order_by(AnnotationEvidence.id)).all()
    co = db.scalars(select(CoAnnotation).where(CoAnnotation.batch_id == bid)
                    .order_by(CoAnnotation.id)).all()
    align = db.scalars(select(CoAlignment).where(CoAlignment.batch_id == bid)
                       .order_by(CoAlignment.id)).all()
    ke = db.scalars(select(KeAnnotation).where(KeAnnotation.batch_id == bid)
                    .order_by(KeAnnotation.id)).all()
    gold = db.scalars(select(GoldKe).where(GoldKe.batch_id == bid)
                      .order_by(GoldKe.id)).all()
    runs = db.scalars(select(QueryRun).where(QueryRun.batch_id == bid)
                      .order_by(QueryRun.id)).all()
    versions = db.scalars(select(OntologyVersion).where(OntologyVersion.batch_id == bid)
                          .order_by(OntologyVersion.id)).all()
    logs = db.scalars(select(CoMergeLog).where(CoMergeLog.batch_id == bid)
                      .order_by(CoMergeLog.id)).all()
    reviews = db.scalars(select(AnnotationCrossReview).where(
        AnnotationCrossReview.batch_id == bid).order_by(AnnotationCrossReview.id)).all()
    issue_cond = Issue.annotation_batch_id == bid
    if unit_ids:
        issue_cond = issue_cond | Issue.annotation_unit_id.in_(unit_ids)
    issues = db.scalars(select(Issue).where(issue_cond).order_by(Issue.id)).all()
    version_ids = [v.id for v in versions]
    audit_cond = (AuditLog.entity_type == "annotation_batch") & (AuditLog.entity_id == bid)
    if version_ids:
        audit_cond = audit_cond | ((AuditLog.entity_type == "ontology_version")
                                   & AuditLog.entity_id.in_(version_ids))
    audits = db.scalars(select(AuditLog).where(audit_cond).order_by(AuditLog.id)).all()
    files = {
        "batch.json": {"batch": _dict(batch), "stats": service.batch_stats(db, batch),
                       "members": [_dict(m) for m in service._members(db, bid)]},
        "units.json": [_dict(u) for u in units],
        "evidence.json": [_dict(e) for e in evidence],
        "co_annotations.json": [_dict(c) for c in co],
        "co_alignments.json": [_dict(a) for a in align],
        "ke_annotations.json": [_dict(k) for k in ke],
        "gold_kes.json": [_dict(g) for g in gold],
        "query_runs.json": [_dict(r) for r in runs],
        "ontology_versions.json": [_dict(v) for v in versions],
        "merge_logs.json": [_dict(l) for l in logs],
        "cross_reviews.json": [_dict(r) for r in reviews],
        "issues.json": [_dict(i) for i in issues],
        "audit.json": [_dict(a) for a in audits],
    }
    readme = ("# 标注批次归档\n\n"
              f"- 批次：{batch.batch_id} {batch.name}\n"
              f"- 状态：{service.STATE_LABELS.get(batch.state, batch.state)}\n"
              f"- 来源：{batch.source_name} {batch.source_version}\n"
              f"- 校验和：{batch.source_checksum}\n\n"
              "文件：batch/units/evidence/CO/对齐/KE/GOLD/query_runs/versions/merge_logs/"
              "cross_reviews/issues/audit。\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, json.dumps(data, ensure_ascii=False, indent=2))
        z.writestr("README.md", readme)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition":
                             f'attachment; filename="annotation-{batch.batch_id}.zip"'})


@router.post("/units/{uid}/run-query")
async def run_query(uid: int, request: Request, db: Session = Depends(get_db),
                    user: dict = Depends(current_user)):
    unit = service.get_unit_or_404(db, uid)
    form = await request.form()
    try:
        run = service.run_query(db, unit, str(form.get("side") or ""), user)
    except HTTPException as e:
        return _back(f"/annotation/units/{uid}", err=e.detail)
    if run.status == "passed":
        return _back(f"/annotation/units/{uid}", ok="执行/编译通过，已记录运行结果")
    return _back(f"/annotation/units/{uid}",
                 err=f"执行失败：{(run.stderr or '见运行记录')[:200]}")


@router.get("/units/{uid}")
def unit_detail(uid: int, request: Request, db: Session = Depends(get_db),
                user: dict = Depends(current_user)):
    unit = service.get_unit_or_404(db, uid)
    batch = service.get_batch_or_404(db, unit.batch_id)
    service.require_view_batch(db, batch, user)
    ctx = service.unit_context(db, unit, user)
    ctx.update({"request": request, "user": user,
                "STATE_LABELS": service.STATE_LABELS,
                "UNIT_STATUS_LABELS": service.UNIT_STATUS_LABELS})
    return templates.TemplateResponse(request=request, name="annotation/unit.html", context=ctx)


@router.post("/units/{uid}/evidence")
async def add_evidence(uid: int, request: Request, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    unit = service.get_unit_or_404(db, uid)
    form = await request.form()
    try:
        locator_raw = str(form.get("locator") or "").strip()
        locator = json.loads(locator_raw) if locator_raw else None
        if locator is not None and not isinstance(locator, dict):
            raise ValueError("locator 必须是 JSON 对象")
        service.add_evidence(db, unit, {
            "source_id": form.get("source_id"), "locator": locator,
            "quote": form.get("quote"), "language": form.get("language"),
            "kind": form.get("kind"),
        }, user)
    except HTTPException as e:
        return _back(f"/annotation/units/{uid}", err=e.detail)
    except Exception as e:  # noqa: BLE001
        return _back(f"/annotation/units/{uid}", err=f"evidence 解析失败：{e}")
    return _back(f"/annotation/units/{uid}", ok="evidence 已添加")


@router.post("/units/{uid}/co")
async def submit_co(uid: int, request: Request, db: Session = Depends(get_db),
                    user: dict = Depends(current_user)):
    unit = service.get_unit_or_404(db, uid)
    form = await request.form()
    try:
        service.submit_co(db, unit, str(form.get("side") or ""), {
            "decision": form.get("decision"), "concepts": form.get("concepts"),
            "constants": form.get("constants"), "operators": form.get("operators"),
            "reason": form.get("reason"), "evidence_ids": form.getlist("evidence_ids"),
        }, user)
    except HTTPException as e:
        return _back(f"/annotation/units/{uid}", err=e.detail)
    return _back(f"/annotation/units/{uid}", ok="CO 已提交")


@router.post("/units/{uid}/align")
async def submit_alignment(uid: int, request: Request, db: Session = Depends(get_db),
                           user: dict = Depends(current_user)):
    unit = service.get_unit_or_404(db, uid)
    form = await request.form()
    try:
        service.submit_alignment(db, unit, {
            "conclusion": form.get("conclusion"), "decision": form.get("decision"),
            "concepts": form.get("concepts"), "constants": form.get("constants"),
            "operators": form.get("operators"), "differences": form.get("differences"),
            "concept_rows": form.get("concept_rows"),
            "operator_rows": form.get("operator_rows"),
            "reason": form.get("reason"), "evidence_ids": form.getlist("evidence_ids"),
        }, user)
    except HTTPException as e:
        return _back(f"/annotation/units/{uid}", err=e.detail)
    return _back(f"/annotation/units/{uid}", ok="CO 对齐结论已提交")


@router.post("/units/{uid}/ke")
async def submit_ke(uid: int, request: Request, db: Session = Depends(get_db),
                    user: dict = Depends(current_user)):
    unit = service.get_unit_or_404(db, uid)
    form = await request.form()
    try:
        service.submit_ke(db, unit, str(form.get("side") or ""), {
            "content": form.get("content"), "execution": form.get("execution"),
            "self_check": form.get("self_check"), "evidence_ids": form.getlist("evidence_ids"),
        }, user)
    except HTTPException as e:
        return _back(f"/annotation/units/{uid}", err=e.detail)
    return _back(f"/annotation/units/{uid}", ok="KE 已提交")


@router.post("/units/{uid}/gold")
async def submit_gold(uid: int, request: Request, db: Session = Depends(get_db),
                      user: dict = Depends(current_user)):
    unit = service.get_unit_or_404(db, uid)
    form = await request.form()
    try:
        service.submit_gold(db, unit, {
            "source": form.get("source"), "content": form.get("content"),
            "differences": form.get("differences"), "reason": form.get("reason"),
            "evidence_ids": form.getlist("evidence_ids"),
        }, user)
    except HTTPException as e:
        return _back(f"/annotation/units/{uid}", err=e.detail)
    return _back(f"/annotation/units/{uid}", ok="GOLD 已提交")
