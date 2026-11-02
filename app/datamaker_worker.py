"""DataMaker 独立 worker：原子 claim queued 任务并执行。"""
from __future__ import annotations

import logging
import os
import socket
import time
import uuid

from . import datamaker as service
from .library_context import library_scope

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("datamaker.worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _recover() -> None:
    from .db import SessionLocal
    with library_scope(None):
        db = SessionLocal()
        try:
            service.recover_stale(db)
        finally:
            db.close()


def main() -> None:
    log.info("worker started id=%s", WORKER_ID)
    _recover()
    last_recover = time.monotonic()
    while True:
        if time.monotonic() - last_recover > 60:
            _recover()
            last_recover = time.monotonic()
        found = service.claim_next_job(WORKER_ID)
        if found is None:
            time.sleep(2)
            continue
        job_id, library_id = found
        log.info("job %s claimed", job_id)
        try:
            with library_scope(library_id):
                service._run_job_inner(job_id)
            log.info("job %s finished", job_id)
        except Exception:  # noqa: BLE001
            log.exception("job %s crashed", job_id)
            time.sleep(2)


if __name__ == "__main__":
    main()
