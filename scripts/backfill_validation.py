"""历史 rule / operator / GOLD KE 校验回填（只记录，不阻断）。

用法：
  .venv/bin/python scripts/backfill_validation.py [--limit 1000]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import init_db  # noqa: E402
from app.library_context import DEFAULT_LIBRARY_ID, library_session  # noqa: E402
from app.models import GoldItem, OntologyLibrary, OntologyRule, Operator  # noqa: E402
from app.validation import run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    init_db()
    summary = {"checked": 0, "ok": 0, "error": 0}
    with library_session(None) as db:
        lib_ids = list(db.scalars(select(OntologyLibrary.id).order_by(OntologyLibrary.id)))
    for lib_id in lib_ids:
        with library_session(lib_id) as db:
            for kind, model in (("rule", OntologyRule), ("operator", Operator)):
                for obj in db.scalars(select(model).order_by(model.id).limit(args.limit)):
                    res = run(db, kind, obj, stage="backfill", actor="system", enforce=False)
                    summary["checked"] += 1
                    summary["ok" if res.ok else "error"] += 1
            db.commit()
        print(f"library {lib_id}: {summary}")
    with library_session(DEFAULT_LIBRARY_ID) as db:
        for obj in db.scalars(select(GoldItem).order_by(GoldItem.id).limit(args.limit)):
            res = run(db, "gold_item", obj, stage="backfill", actor="system", enforce=False)
            summary["checked"] += 1
            summary["ok" if res.ok else "error"] += 1
        db.commit()
    print(f"total: {summary}")


if __name__ == "__main__":
    main()
