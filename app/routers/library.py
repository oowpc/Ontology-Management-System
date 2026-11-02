"""全局规则 / 个体库：服务端渲染页面（KEOL spec_v1 全局层）。"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import log_change, snapshot
from ..db import get_db
from ..models import Concept, OntologyIndividual, OntologyRule
from ..security import current_user
from ..templating import flash, templates

router = APIRouter(tags=["library"])
EDIT_ROLES = ("reviewer", "admin")
PAGE_SIZE = 30
STATUS_LABELS = {"draft": "草稿", "pending": "待审", "approved": "已通过", "disabled": "停用"}


def _can_edit(user: dict) -> bool:
    return user["role"] in EDIT_ROLES


def _forbid(user: dict):
    if not _can_edit(user):
        raise HTTPException(403, "权限不足")


def _lines(raw: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,，、;；\n]+", raw or "") if x.strip()]


def _pretty(v) -> str:
    return json.dumps(v, ensure_ascii=False, indent=2) if v is not None else ""


# ================= 规则 =================

def _rule_data(r: OntologyRule | None) -> dict:
    return {
        "name": r.name if r else "",
        "domain": (r.domain if r else "") or "通用",
        "description": (r.description if r else "") or "",
        "body_text": _pretty(r.body) if r else "",
        "head_text": _pretty(r.head or []) if r else "[]",
        "sources_text": "\n".join(r.sources or []) if r else "",
        "supply_text": _pretty(r.supply or {}) if r else "{}",
        "status": (r.status if r else "") or "approved",
    }


async def _rule_form(request: Request) -> tuple[dict, str | None]:
    form = await request.form()
    data = {
        "name": str(form.get("name") or "").strip(),
        "domain": str(form.get("domain") or "").strip() or "通用",
        "description": str(form.get("description") or ""),
        "body_text": str(form.get("body_text") or "").strip(),
        "head_text": str(form.get("head_text") or "").strip() or "[]",
        "sources_text": str(form.get("sources_text") or ""),
        "supply_text": str(form.get("supply_text") or "").strip() or "{}",
        "status": str(form.get("status") or "approved"),
    }
    try:
        data["body"] = json.loads(data["body_text"]) if data["body_text"] else None
        if data["body"] is not None and not isinstance(data["body"], dict):
            raise ValueError("body 必须是 JSON 对象或留空（事实）")
        data["head"] = json.loads(data["head_text"])
        if not isinstance(data["head"], list):
            raise ValueError("head 必须是 JSON 数组")
        data["supply"] = json.loads(data["supply_text"])
        if not isinstance(data["supply"], dict):
            raise ValueError("supply 必须是 JSON 对象")
        data["sources"] = _lines(data["sources_text"])
    except (json.JSONDecodeError, ValueError) as e:
        return data, f"JSON 格式不合法：{e}"
    if not data["name"]:
        return data, "规则名称不能为空"
    if data["status"] not in STATUS_LABELS:
        return data, "非法状态"
    return data, None


@router.get("/rules")
def rules_page(request: Request, q: str | None = None, status: str | None = None,
               domain: str | None = None, page: int = 1,
               db: Session = Depends(get_db), user: dict = Depends(current_user)):
    stmt = select(OntologyRule)
    if q:
        stmt = stmt.where(or_(OntologyRule.name.like(f"%{q}%"),
                              OntologyRule.description.like(f"%{q}%")))
    if status:
        stmt = stmt.where(OntologyRule.status == status)
    if domain:
        stmt = stmt.where(OntologyRule.domain == domain)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(OntologyRule.name)
                      .limit(PAGE_SIZE).offset(max(0, page - 1) * PAGE_SIZE)).all()
    domains = [x for x in db.scalars(select(OntologyRule.domain).distinct()
                                     .order_by(OntologyRule.domain)).all() if x]
    return templates.TemplateResponse(request=request, name="rules/list.html", context={
        "request": request, "user": user, "items": rows, "total": total,
        "page": page, "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        "q": q, "status": status, "domain": domain, "domains": domains,
        "status_labels": STATUS_LABELS,
    })


@router.get("/rules/new")
def rule_new_page(request: Request, user: dict = Depends(current_user)):
    _forbid(user)
    return templates.TemplateResponse(request=request, name="rules/form.html", context={
        "request": request, "user": user, "r": _rule_data(None), "error": None,
    })


@router.post("/rules/new")
async def rule_new(request: Request, db: Session = Depends(get_db),
                   user: dict = Depends(current_user)):
    _forbid(user)
    data, error = await _rule_form(request)
    if not error and db.scalar(select(OntologyRule).where(OntologyRule.name == data["name"])):
        error = f"规则「{data['name']}」已存在"
    if error:
        return templates.TemplateResponse(request=request, name="rules/form.html", context={
            "request": request, "user": user, "r": data, "error": error,
        }, status_code=400)
    r = OntologyRule(name=data["name"], domain=data["domain"],
                     description=data["description"], body=data["body"],
                     head=data["head"], sources=data["sources"], supply=data["supply"],
                     status=data["status"], created_by=user["username"],
                     reviewed_by=user["username"])
    db.add(r)
    db.flush()
    log_change(db, "rule", r.id, "create", None, snapshot(r), user["username"])
    db.commit()
    flash(request, "规则已创建")
    return RedirectResponse(f"/rules/{r.id}", status_code=303)


@router.get("/rules/{rid}")
def rule_detail_page(rid: int, request: Request, db: Session = Depends(get_db),
                     user: dict = Depends(current_user)):
    r = db.get(OntologyRule, rid)
    if not r:
        raise HTTPException(404, "规则不存在")
    return templates.TemplateResponse(request=request, name="rules/detail.html", context={
        "request": request, "user": user, "r": r,
        "body_text": _pretty(r.body) if r.body is not None else "null（事实）",
        "head_text": _pretty(r.head or []),
        "supply_text": _pretty(r.supply or {}),
        "status_labels": STATUS_LABELS,
    })


@router.get("/rules/{rid}/edit")
def rule_edit_page(rid: int, request: Request, db: Session = Depends(get_db),
                   user: dict = Depends(current_user)):
    _forbid(user)
    r = db.get(OntologyRule, rid)
    if not r:
        raise HTTPException(404, "规则不存在")
    return templates.TemplateResponse(request=request, name="rules/form.html", context={
        "request": request, "user": user, "r": _rule_data(r), "rid": rid, "error": None,
    })


@router.post("/rules/{rid}/edit")
async def rule_edit(rid: int, request: Request, db: Session = Depends(get_db),
                    user: dict = Depends(current_user)):
    _forbid(user)
    r = db.get(OntologyRule, rid)
    if not r:
        raise HTTPException(404, "规则不存在")
    data, error = await _rule_form(request)
    if not error and db.scalar(select(OntologyRule).where(
            OntologyRule.name == data["name"], OntologyRule.id != rid)):
        error = f"规则「{data['name']}」已存在"
    if error:
        return templates.TemplateResponse(request=request, name="rules/form.html", context={
            "request": request, "user": user, "r": data, "rid": rid, "error": error,
        }, status_code=400)
    before = snapshot(r)
    for attr in ("name", "domain", "description", "body", "head", "sources",
                 "supply", "status"):
        setattr(r, attr, data[attr])
    r.reviewed_by = user["username"]
    db.flush()
    log_change(db, "rule", r.id, "update", before, snapshot(r), user["username"])
    db.commit()
    flash(request, "规则已保存")
    return RedirectResponse(f"/rules/{r.id}", status_code=303)


# ================= 个体 =================

def _individual_data(i: OntologyIndividual | None) -> dict:
    return {
        "name": i.name if i else "",
        "concept_id": i.concept_id if i else None,
        "domain": (i.domain if i else "") or "通用",
        "description": (i.description if i else "") or "",
        "sources_text": "\n".join(i.sources or []) if i else "",
        "supply_text": _pretty(i.supply or {}) if i else "{}",
        "status": (i.status if i else "") or "approved",
    }


async def _individual_form(request: Request) -> tuple[dict, str | None]:
    form = await request.form()
    cid = str(form.get("concept_id") or "").strip()
    data = {
        "name": str(form.get("name") or "").strip(),
        "concept_id": int(cid) if cid.isdigit() else None,
        "domain": str(form.get("domain") or "").strip() or "通用",
        "description": str(form.get("description") or ""),
        "sources_text": str(form.get("sources_text") or ""),
        "supply_text": str(form.get("supply_text") or "").strip() or "{}",
        "status": str(form.get("status") or "approved"),
    }
    try:
        data["supply"] = json.loads(data["supply_text"])
        if not isinstance(data["supply"], dict):
            raise ValueError("supply 必须是 JSON 对象")
        data["sources"] = _lines(data["sources_text"])
    except (json.JSONDecodeError, ValueError) as e:
        return data, f"JSON 格式不合法：{e}"
    if not data["name"]:
        return data, "个体名称不能为空"
    if not data["concept_id"]:
        return data, "必须选择所属概念"
    if data["status"] not in STATUS_LABELS:
        return data, "非法状态"
    return data, None


def _selected_concept(db: Session, cid: int | None) -> dict | None:
    c = db.get(Concept, cid) if cid else None
    return {"id": c.id, "name": c.name} if c else None


@router.get("/individuals")
def individuals_page(request: Request, q: str | None = None, status: str | None = None,
                     domain: str | None = None, concept_id: int | None = None, page: int = 1,
                     db: Session = Depends(get_db), user: dict = Depends(current_user)):
    stmt = select(OntologyIndividual)
    if q:
        stmt = stmt.where(OntologyIndividual.name.like(f"%{q}%"))
    if status:
        stmt = stmt.where(OntologyIndividual.status == status)
    if domain:
        stmt = stmt.where(OntologyIndividual.domain == domain)
    if concept_id:
        stmt = stmt.where(OntologyIndividual.concept_id == concept_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(OntologyIndividual.concept_id, OntologyIndividual.name)
                      .limit(PAGE_SIZE).offset(max(0, page - 1) * PAGE_SIZE)).all()
    names = {c.id: c.name for c in db.scalars(select(Concept).where(
        Concept.id.in_({i.concept_id for i in rows}))).all()} if rows else {}
    domains = [x for x in db.scalars(select(OntologyIndividual.domain).distinct()
                                     .order_by(OntologyIndividual.domain)).all() if x]
    return templates.TemplateResponse(request=request, name="individuals/list.html", context={
        "request": request, "user": user, "items": rows, "total": total,
        "page": page, "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        "q": q, "status": status, "domain": domain, "concept_id": concept_id,
        "domains": domains, "names": names, "status_labels": STATUS_LABELS,
        "selected_concept": _selected_concept(db, concept_id),
    })


@router.get("/individuals/new")
def individual_new_page(request: Request, user: dict = Depends(current_user)):
    _forbid(user)
    return templates.TemplateResponse(request=request, name="individuals/form.html", context={
        "request": request, "user": user, "i": _individual_data(None), "error": None,
        "selected_concept": None,
    })


@router.post("/individuals/new")
async def individual_new(request: Request, db: Session = Depends(get_db),
                         user: dict = Depends(current_user)):
    _forbid(user)
    data, error = await _individual_form(request)
    c = db.get(Concept, data.get("concept_id")) if data.get("concept_id") else None
    if not error and not c:
        error = "所属概念不存在"
    if not error and db.scalar(select(OntologyIndividual).where(
            OntologyIndividual.concept_id == data["concept_id"],
            OntologyIndividual.name == data["name"])):
        error = f"概念「{c.name}」下已有个体「{data['name']}」"
    if error:
        return templates.TemplateResponse(request=request, name="individuals/form.html", context={
            "request": request, "user": user, "i": data, "error": error,
            "selected_concept": _selected_concept(db, data.get("concept_id")),
        }, status_code=400)
    i = OntologyIndividual(name=data["name"], concept_id=data["concept_id"],
                           domain=data["domain"], description=data["description"],
                           sources=data["sources"], supply=data["supply"],
                           status=data["status"], created_by=user["username"],
                           reviewed_by=user["username"])
    db.add(i)
    db.flush()
    log_change(db, "individual", i.id, "create", None, snapshot(i), user["username"])
    db.commit()
    flash(request, "个体已创建")
    return RedirectResponse(f"/individuals/{i.id}", status_code=303)


@router.get("/individuals/{iid}")
def individual_detail_page(iid: int, request: Request, db: Session = Depends(get_db),
                           user: dict = Depends(current_user)):
    i = db.get(OntologyIndividual, iid)
    if not i:
        raise HTTPException(404, "个体不存在")
    c = db.get(Concept, i.concept_id)
    return templates.TemplateResponse(request=request, name="individuals/detail.html", context={
        "request": request, "user": user, "i": i, "concept": c,
        "supply_text": _pretty(i.supply or {}),
        "status_labels": STATUS_LABELS,
    })


@router.get("/individuals/{iid}/edit")
def individual_edit_page(iid: int, request: Request, db: Session = Depends(get_db),
                         user: dict = Depends(current_user)):
    _forbid(user)
    i = db.get(OntologyIndividual, iid)
    if not i:
        raise HTTPException(404, "个体不存在")
    return templates.TemplateResponse(request=request, name="individuals/form.html", context={
        "request": request, "user": user, "i": _individual_data(i), "iid": iid,
        "error": None, "selected_concept": _selected_concept(db, i.concept_id),
    })


@router.post("/individuals/{iid}/edit")
async def individual_edit(iid: int, request: Request, db: Session = Depends(get_db),
                          user: dict = Depends(current_user)):
    _forbid(user)
    i = db.get(OntologyIndividual, iid)
    if not i:
        raise HTTPException(404, "个体不存在")
    data, error = await _individual_form(request)
    c = db.get(Concept, data.get("concept_id")) if data.get("concept_id") else None
    if not error and not c:
        error = "所属概念不存在"
    if not error and db.scalar(select(OntologyIndividual).where(
            OntologyIndividual.concept_id == data["concept_id"],
            OntologyIndividual.name == data["name"],
            OntologyIndividual.id != iid)):
        error = f"概念「{c.name}」下已有个体「{data['name']}」"
    if error:
        return templates.TemplateResponse(request=request, name="individuals/form.html", context={
            "request": request, "user": user, "i": data, "iid": iid, "error": error,
            "selected_concept": _selected_concept(db, data.get("concept_id")),
        }, status_code=400)
    before = snapshot(i)
    for attr in ("name", "concept_id", "domain", "description", "sources",
                 "supply", "status"):
        setattr(i, attr, data[attr])
    i.reviewed_by = user["username"]
    db.flush()
    log_change(db, "individual", i.id, "update", before, snapshot(i), user["username"])
    db.commit()
    flash(request, "个体已保存")
    return RedirectResponse(f"/individuals/{i.id}", status_code=303)
