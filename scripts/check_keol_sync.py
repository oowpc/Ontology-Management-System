"""检查 KEOL ontology.json 与指定 OMS 库的差异（只记录，不审批）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.keol_sync import check, record  # noqa: E402
from app.library_context import library_session  # noqa: E402
from app.models import OntologyLibrary  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default=settings.keol_sync_library)
    args = parser.parse_args()
    init_db()
    with library_session(None) as db:
        lib = db.scalar(select(OntologyLibrary).where(OntologyLibrary.key == args.library))
        if lib is None:
            raise SystemExit(f"library not found: {args.library}")
        lid = lib.id
    with library_session(lid) as db:
        data = check(db)
        row = record(db, data, "cli")
        print(f"run={row.id} changed={data['changed']} source={data['source_counts']} target={data['target_counts']}")
        print(data["diff"])


if __name__ == "__main__":
    main()
