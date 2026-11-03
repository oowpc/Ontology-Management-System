"""DataAgent Monitor 事件收件箱：幂等入站、Dashboard 同步、转 Issue。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .issues.service import create_issue
from .models import MonitorEvent

SEVERITIES = {"low", "medium", "high"}
STATUSES = {"new", "triaged", "issue", "ignored", "resolved"}


def _text(value, limit: int = 1200) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fingerprint(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ingest(db: Session, payload: dict) -> tuple[MonitorEvent, bool]:
    if not isinstance(payload, dict):
        raise HTTPException(400, "事件必须是 JSON 对象")
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    product_id = str(payload.get("product_id") or "").strip()
    source_id = str(payload.get("source_id") or "").strip()
    if not product_id or not source_id:
        raise HTTPException(400, "product_id / source_id 必填")
    severity = str(payload.get("severity") or "medium").strip()
    if severity not in SEVERITIES:
        severity = "medium"
    analysis = event.get("analysis") if isinstance(event.get("analysis"), dict) else {}
    category = str(analysis.get("category") or payload.get("category") or "content").strip()
    field = str(event.get("field") or "").strip()
    source_event_id = str(payload.get("event_id") or payload.get("source_event_id") or "").strip()
    fingerprint = str(payload.get("fingerprint") or "").strip() or _fingerprint(payload)
    if source_event_id:
        existing = db.scalar(select(MonitorEvent).where(
            MonitorEvent.source_event_id == source_event_id))
    else:
        existing = None
    existing = existing or db.scalar(select(MonitorEvent).where(
        MonitorEvent.fingerprint == fingerprint))
    if existing is not None:
        return existing, True
    summary = _text(analysis.get("summary"), 500) or f"{field or '内容'} 发生变化"
    before = _text(event.get("before"))
    after = _text(event.get("after"))
    if field == "text" and analysis.get("summary"):
        before = before or _text(event.get("before"))
        after = after or _text(event.get("after"))
    row = MonitorEvent(
        source_event_id=source_event_id, fingerprint=fingerprint,
        product_id=product_id, source_id=source_id, severity=severity,
        category=category or "content", field=field,
        analysis_status=str(event.get("analysis_status") or ""),
        summary=summary, before_preview=before, after_preview=after,
        official_url=str(evidence.get("official_url") or ""),
        payload=payload, status="new",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, False


def sync_dashboard(db: Session, data: dict) -> dict:
    imported = duplicate = 0
    for e in data.get("events") or []:
        change = e.get("change") if isinstance(e.get("change"), dict) else {}
        payload = {
            "event_id": e.get("id"),
            "product_id": e.get("product_id"), "source_id": e.get("source_id"),
            "severity": e.get("severity"), "fingerprint": f"dashboard:{e.get('id')}",
            "event": {
                "field": e.get("field"), "analysis_status": e.get("analysis_status"),
                "analysis": {"category": e.get("category"), "summary": e.get("summary")},
                "before": change.get("previous_preview"), "after": change.get("current_preview"),
            },
            "evidence": {},
        }
        row, dup = ingest(db, payload)
        if dup:
            duplicate += 1
        else:
            imported += 1
    return {"imported": imported, "duplicate": duplicate}


def event_dict(e: MonitorEvent) -> dict:
    return {
        "id": e.id, "source_event_id": e.source_event_id,
        "product_id": e.product_id, "source_id": e.source_id,
        "severity": e.severity, "category": e.category, "field": e.field,
        "analysis_status": e.analysis_status, "summary": e.summary,
        "before_preview": e.before_preview, "after_preview": e.after_preview,
        "official_url": e.official_url, "status": e.status, "issue_id": e.issue_id,
        "received_at": e.received_at.isoformat(timespec="seconds") if e.received_at else None,
    }


def create_issue_for_event(db: Session, event: MonitorEvent, actor: str) -> int:
    if event.issue_id:
        return event.issue_id
    title = f"{event.product_id}/{event.source_id} 的 {event.field or '内容'} 变化应如何处理？"
    desc = (
        f"来源：{event.product_id}/{event.source_id}\n"
        f"严重级别：{event.severity}；分类：{event.category}\n"
        f"摘要：{event.summary}\n"
        f"前值：{event.before_preview or '（无）'}\n"
        f"后值：{event.after_preview or '（无）'}\n"
    )
    issue = create_issue(
        db, title=title, type_tag="外部产品变化", description=desc,
        domain="外部产品监控", group_name="", importance=("高" if event.severity == "high" else "中"),
        urgency="中", owner=actor,
        cases=[{"source": event.official_url or f"{event.product_id}/{event.source_id}",
                "original_text": event.before_preview or "（无前值）",
                "variant_a": event.after_preview or "（无后值）",
                "diff_note": event.summary}],
        actor=actor,
    )
    event.issue_id = issue.id
    event.status = "issue"
    event.updated_at = datetime.now()
    db.commit()
    return issue.id
