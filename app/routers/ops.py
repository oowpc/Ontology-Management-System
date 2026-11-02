"""运行状态：服务端口、DB、备份、证书、Token、待审建议。"""
from __future__ import annotations

import os
import shutil
import socket
import ssl
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import (AnnotationBatch, ApiToken, Concept, OntologyIndividual,
                      OntologyRule, OntologySuggestion, OntologyVersion, Operator, User)
from ..security import require_role
from ..templating import templates

router = APIRouter(tags=["ops"])
CERT = Path(os.environ.get("OMS_TLS_CERT", "/home/oowpc/oms-tls/cert.pem"))


def _port_ok(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _cert_info() -> dict | None:
    if not CERT.exists():
        return None
    try:
        d = ssl._ssl._test_decode_cert(str(CERT))
        exp = datetime.fromtimestamp(ssl.cert_time_to_seconds(d["notAfter"]), timezone.utc)
        return {"subject": dict(x[0] for x in d.get("subject", [])),
                "not_after": exp.isoformat(timespec="seconds"),
                "days_left": (exp - datetime.now(timezone.utc)).days}
    except Exception:  # noqa: BLE001
        return {"path": str(CERT), "error": "无法解析证书"}


def _db_path() -> Path | None:
    if not settings.db_url.startswith("sqlite:///"):
        return None
    p = settings.db_url[len("sqlite:///"):]
    return None if p == ":memory:" else Path(p).resolve()


def summary(db: Session) -> dict:
    db_path = _db_path()
    backup_dir = (db_path.parent / "backups") if db_path else Path("data/backups")
    backups = sorted(backup_dir.glob("ontology-*.db"), key=lambda p: p.stat().st_mtime,
                     reverse=True) if backup_dir.exists() else []
    latest = backups[0] if backups else None
    now = datetime.now()
    expiring = db.scalar(select(func.count(ApiToken.id)).where(
        ApiToken.revoked_at.is_(None), ApiToken.expires_at.is_not(None),
        ApiToken.expires_at <= now + timedelta(days=7))) or 0
    states = dict(db.execute(select(AnnotationBatch.state, func.count())
                             .group_by(AnnotationBatch.state)).all())
    disk = shutil.disk_usage(db_path.parent if db_path else Path("."))
    return {
        "time": now.isoformat(timespec="seconds"),
        "db": {
            "path": str(db_path) if db_path else settings.db_url,
            "size_mb": round(db_path.stat().st_size / 1024 / 1024, 2) if db_path and db_path.exists() else None,
            "disk_free_gb": round(disk.free / 1024 ** 3, 1),
        },
        "counts": {
            "concepts": db.scalar(select(func.count(Concept.id))) or 0,
            "operators": db.scalar(select(func.count(Operator.id))) or 0,
            "rules": db.scalar(select(func.count(OntologyRule.id))) or 0,
            "individuals": db.scalar(select(func.count(OntologyIndividual.id))) or 0,
            "users": db.scalar(select(func.count(User.id))) or 0,
            "versions": db.scalar(select(func.count(OntologyVersion.id))) or 0,
            "suggestions_pending": db.scalar(select(func.count(OntologySuggestion.id))
                                             .where(OntologySuggestion.status == "pending")) or 0,
        },
        "annotations": {"by_state": states, "total": sum(states.values())},
        "tokens": {
            "active": db.scalar(select(func.count(ApiToken.id)).where(
                ApiToken.revoked_at.is_(None))) or 0,
            "expiring_7d": expiring,
            "no_expiry": db.scalar(select(func.count(ApiToken.id)).where(
                ApiToken.revoked_at.is_(None), ApiToken.expires_at.is_(None))) or 0,
        },
        "backup": {
            "latest": latest.name if latest else None,
            "age_hours": round((time.time() - latest.stat().st_mtime) / 3600, 1) if latest else None,
            "size_mb": round(latest.stat().st_size / 1024 / 1024, 2) if latest else None,
            "count": len(backups),
        },
        "certificate": _cert_info(),
        "services": {
            "oms_http_8020": _port_ok(8020),
            "oms_tls_8443": _port_ok(8443),
            "keol_http_8321": _port_ok(8321),
            "keol_tls_8444": _port_ok(8444),
        },
    }


@router.get("/api/ops/summary")
def ops_summary_api(db: Session = Depends(get_db),
                    user: dict = Depends(require_role("admin"))):
    return summary(db)


@router.get("/ops")
def ops_page(request: Request, db: Session = Depends(get_db),
             user: dict = Depends(require_role("admin"))):
    return templates.TemplateResponse(request=request, name="ops.html", context={
        "request": request, "user": user, "s": summary(db),
    })
