"""按批次回滚 KEOL 导入：恢复更新项，删除本批新建且未被外部引用的项。

用法：
  python scripts/rollback_batch.py --batch-id 20261027-100000-abcd1234
  python scripts/rollback_batch.py --batch-id ... --force --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser(description="按批次回滚 KEOL 导入")
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--force", action="store_true",
                    help="允许清理外部父类链接/外部算子引用（业务数据仍会阻止）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from app.batches import rollback_batch
    from app.db import SessionLocal, init_db
    init_db()
    db = SessionLocal()
    try:
        result = rollback_batch(db, args.batch_id, force=args.force, dry_run=args.dry_run)
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in ("rolled_back", "dry_run", "already_rolled_back") else 1


if __name__ == "__main__":
    raise SystemExit(main())
