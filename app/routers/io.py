"""导入导出 API：/api/export/*、/api/import/*（仅 admin）"""
from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..importer import (csv_to_payload, export_csv, export_payload,
                        import_payload, parse_field_map)
from ..security import require_role

router = APIRouter(prefix="/api", tags=["io"])


def _attachment(filename: str) -> dict:
    return {"Content-Disposition": f"attachment; filename*=utf-8''{quote(filename)}"}


@router.get("/export/json")
def export_json_ep(field_map: str | None = None, pretty: int = 0,
                   db: Session = Depends(get_db), user: dict = Depends(require_role("admin"))):
    payload = export_payload(db, parse_field_map(field_map))
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None),
        media_type="application/json; charset=utf-8",
        headers=_attachment("ontology-export.json"),
    )


@router.get("/export/csv")
def export_csv_ep(kind: str = "concepts", field_map: str | None = None,
                  db: Session = Depends(get_db), user: dict = Depends(require_role("admin"))):
    text = export_csv(db, kind, parse_field_map(field_map))
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers=_attachment(f"ontology-{kind}.csv"))


@router.post("/import/json")
async def import_json_ep(request: Request, field_map: str | None = None,
                         db: Session = Depends(get_db),
                         user: dict = Depends(require_role("admin"))):
    body = (await request.body()).decode("utf-8-sig")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON 解析失败：{e}")
    if not isinstance(payload, dict):
        raise HTTPException(400, "导入内容应为 {concepts: [...], operators: [...]}")
    fm = parse_field_map(field_map)
    return import_payload(db, payload, user["username"], fm)


@router.post("/import/csv")
async def import_csv_ep(request: Request, kind: str = "concepts", field_map: str | None = None,
                        db: Session = Depends(get_db),
                        user: dict = Depends(require_role("admin"))):
    body = (await request.body()).decode("utf-8-sig")
    fm = parse_field_map(field_map)
    payload = csv_to_payload(body, kind, fm)
    return import_payload(db, payload, user["username"], fm)
