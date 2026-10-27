"""SOP 手工标注工作流页面。"""
from __future__ import annotations

import json
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (AnnotationBatch, AnnotationCrossReview, AnnotationMember,
                      AnnotationUnit, CoMergeLog, OntologyVersion, User)
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
            "source_type", "guide_version", "statistical_unit", "acceptance_threshold",
            "annotator_a", "annotator_b", "lead", "arbiter", "cross_checkers",
            "project_owner", "note")}, user)
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
