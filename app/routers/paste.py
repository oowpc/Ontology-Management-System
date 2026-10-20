"""粘贴识别：/paste（页面）与 /api/paste/*（解析报告 + 导入）"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Concept
from ..paster import build_report, import_parsed, parse_keol
from ..security import current_user, require_role
from ..templating import templates

router = APIRouter(tags=["paste"])


class PasteIn(BaseModel):
    text: str = Field(min_length=1)
    domain: str = "通用"


class PasteImportIn(PasteIn):
    import_concepts: bool = True
    import_operators: bool = True
    import_facts: bool = True
    article_code: str = ""  # KE 事实行入成品库时的篇编号（不给则不入事实）


@router.post("/api/paste/parse")
def api_parse(data: PasteIn, db: Session = Depends(get_db), user: dict = Depends(require_role())):
    parsed = parse_keol(data.text)
    return build_report(db, parsed, data.domain)


@router.post("/api/paste/import")
def api_import(data: PasteImportIn, db: Session = Depends(get_db),
               user: dict = Depends(require_role("reviewer", "admin"))):
    parsed = parse_keol(data.text)
    return import_parsed(
        db, parsed, domain=data.domain, actor=user["username"],
        import_concepts=data.import_concepts, import_operators=data.import_operators,
        import_facts=data.import_facts and bool(data.article_code.strip()),
        article_code=data.article_code.strip(),
    )


def _domains(db: Session) -> list[str]:
    ds = db.scalars(select(Concept.domain).distinct().order_by(Concept.domain)).all()
    return sorted({d or "通用" for d in ds} | {"通用"})


# ---------------- 页面 ----------------

@router.get("/paste")
def paste_page(request: Request, db: Session = Depends(get_db),
               user: dict = Depends(current_user)):
    return templates.TemplateResponse(request=request, name="paste.html", context={
        "request": request, "user": user, "report": None, "result": None,
        "text": "", "domains": _domains(db), "domain": "通用", "article_code": "",
    })


@router.post("/paste/parse")
async def paste_parse(request: Request, db: Session = Depends(get_db),
                      user: dict = Depends(current_user)):
    form = await request.form()
    text = str(form.get("text", ""))
    domain = str(form.get("domain", "通用")).strip() or "通用"
    report = build_report(db, parse_keol(text), domain) if text.strip() else None
    return templates.TemplateResponse(request=request, name="paste.html", context={
        "request": request, "user": user, "report": report, "result": None,
        "text": text, "domains": _domains(db), "domain": domain,
        "article_code": str(form.get("article_code", "")),
    })


@router.post("/paste/import")
async def paste_import(request: Request, db: Session = Depends(get_db),
                       user: dict = Depends(current_user)):
    if user["role"] not in ("reviewer", "admin"):
        from fastapi import HTTPException
        raise HTTPException(403, "导入需审核员 / 管理员")
    form = await request.form()
    text = str(form.get("text", ""))
    domain = str(form.get("domain", "通用")).strip() or "通用"
    article_code = str(form.get("article_code", "")).strip()
    parsed = parse_keol(text)
    result = import_parsed(
        db, parsed, domain=domain, actor=user["username"],
        import_concepts=form.get("import_concepts") is not None,
        import_operators=form.get("import_operators") is not None,
        import_facts=form.get("import_facts") is not None and bool(article_code),
        article_code=article_code,
    )
    return templates.TemplateResponse(request=request, name="paste.html", context={
        "request": request, "user": user, "report": build_report(db, parsed, domain),
        "result": result, "text": text, "domains": _domains(db),
        "domain": domain, "article_code": article_code,
    })
