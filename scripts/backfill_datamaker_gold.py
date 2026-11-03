"""把已成功的 DataMaker 任务补成 GOLD 草稿（幂等，只建 draft）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app import datamaker  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.library_context import library_scope  # noqa: E402
from app.models import DatamakerJob  # noqa: E402


def main() -> None:
    init_db()
    with library_scope(None):
        db = SessionLocal()
        try:
            jobs = db.scalars(select(DatamakerJob).where(
                DatamakerJob.status == "succeeded").order_by(DatamakerJob.id)).all()
            targets = [(j.id, j.library_id) for j in jobs]
        finally:
            db.close()
    total = 0
    for job_id, library_id in targets:
        with library_scope(library_id):
            db = SessionLocal()
            try:
                job = db.get(DatamakerJob, job_id)
                if job is None:
                    continue
                datamaker._import_results(db, job)
                db.refresh(job)
                total += job.gold_count or 0
                print(f"job {job.id}: gold={job.gold_count}")
            finally:
                db.close()
    print(f"total GOLD drafts: {total}")


if __name__ == "__main__":
    main()
