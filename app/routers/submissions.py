"""提交与审核 API：/api/submissions"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Submission
from ..review import (SUB_STATUS, approve, batch_action, create_submission,
                      get_submission_or_404, merge, reject, submission_dict)
from ..schemas import BatchReviewAction, ReviewAction, SubmissionCreate
from ..security import require_role

router = APIRouter(prefix="/api/submissions", tags=["submissions"])


@router.get("")
def list_submissions(status: str | None = "pending", page: int = 1,
                     size: int = Query(50, le=200),
                     db: Session = Depends(get_db), user: dict = Depends(require_role())):
    stmt = select(Submission)
    if status:
        if status not in SUB_STATUS:
            raise HTTPException(400, "非法状态")
        stmt = stmt.where(Submission.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(Submission.id.desc()).offset((page - 1) * size).limit(size)
    ).all()
    return {"items": [submission_dict(db, s) for s in items],
            "total": total, "page": page, "size": size}


@router.post("", status_code=201)
def create(data: SubmissionCreate, db: Session = Depends(get_db),
           user: dict = Depends(require_role())):
    sub = create_submission(
        db, name=data.name, aliases=data.aliases, definition=data.definition,
        suggested_parent_id=data.suggested_parent_id, reason=data.reason,
        sample_question=data.sample_question, actor=user["username"],
    )
    return submission_dict(db, sub)


@router.get("/{sid}")
def detail(sid: int, db: Session = Depends(get_db), user: dict = Depends(require_role())):
    return submission_dict(db, get_submission_or_404(db, sid))


@router.post("/{sid}/approve")
def approve_ep(sid: int, data: ReviewAction, db: Session = Depends(get_db),
               user: dict = Depends(require_role("reviewer", "admin"))):
    sub = get_submission_or_404(db, sid)
    return submission_dict(db, approve(db, sub, data.comment, user["username"]))


@router.post("/{sid}/reject")
def reject_ep(sid: int, data: ReviewAction, db: Session = Depends(get_db),
              user: dict = Depends(require_role("reviewer", "admin"))):
    sub = get_submission_or_404(db, sid)
    return submission_dict(db, reject(db, sub, data.comment, user["username"]))


@router.post("/{sid}/merge")
def merge_ep(sid: int, data: ReviewAction, db: Session = Depends(get_db),
             user: dict = Depends(require_role("reviewer", "admin"))):
    if data.merge_target_id is None:
        raise HTTPException(400, "merge_target_id 必填")
    sub = get_submission_or_404(db, sid)
    return submission_dict(db, merge(db, sub, data.comment, data.merge_target_id, user["username"]))


@router.post("/batch")
def batch_ep(data: BatchReviewAction, db: Session = Depends(get_db),
             user: dict = Depends(require_role("reviewer", "admin"))):
    processed, skipped = batch_action(db, data.ids, data.action, data.comment, user["username"])
    return {"processed": processed, "skipped": skipped}
