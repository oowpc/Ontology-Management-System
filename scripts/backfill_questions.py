"""从现有 KE 上传数据（ke_tasks）回填 questions 表。

幂等：ext_id 唯一，重复运行不重复建题；字段有变化才更新。

用法（项目根目录）：
  python scripts/backfill_questions.py            # 回填 + CO 自动关联
  python scripts/backfill_questions.py --no-link  # 只回填题目，不挂概念/算子
  python scripts/backfill_questions.py --verbose
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal, init_db  # noqa: E402
from app.questions.service import backfill_from_ke_tasks  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="从 ke_tasks 回填 questions（幂等）")
    ap.add_argument("--no-link", action="store_true", help="不做概念/算子自动关联")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    init_db()
    db = SessionLocal()
    try:
        stats = backfill_from_ke_tasks(db, link_concepts=not args.no_link)
    finally:
        db.close()
    print(f"ke_tasks 行数: {stats['tasks']}")
    print(f"新建: {stats['created']}  更新: {stats['updated']}  跨组重号消歧: {stats['collisions']}")
    if not args.no_link:
        print(f"自动关联概念/算子: {stats['linked']}")
    print(f"questions 总数: {stats['total']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
