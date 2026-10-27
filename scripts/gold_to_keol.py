"""GOLD 成品 → KEOL 源文件回流（M26）。

把成品库条目里的 concept/op/const 声明与可提取事实翻译成标准 KEOL，
整文件重生成（幂等）写到 KEOL 源树，供其 load_db.py 重建收录、keol_check.py 全库校验。

用法（项目根目录）：
  python scripts/gold_to_keol.py                     # gold 状态成品 → ke/回流/管理系统-GOLD.md
  python scripts/gold_to_keol.py --dry-run           # 只统计与预览，不写文件
  python scripts/gold_to_keol.py --status gold,reviewed
  python scripts/gold_to_keol.py --dst D:/path/xx.md --src D:/path/政府本体
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_SRC = r"C:\Users\wang\Desktop\工作项目\本体与政务\政府本体"


def main() -> int:
    ap = argparse.ArgumentParser(description="GOLD 成品 → KEOL 源文件回流（幂等整文件重生成）")
    ap.add_argument("--src", help="KEOL 源目录（缺省：KEOL_SRC 环境变量 > 内置默认）")
    ap.add_argument("--dst", help="输出 .md 路径（缺省 <src>/ke/回流/管理系统-GOLD.md）")
    ap.add_argument("--status", default="gold",
                    help="纳入的成品状态（逗号分隔，如 gold,reviewed；all=全部）")
    ap.add_argument("--dry-run", action="store_true", help="只统计预览，不写文件")
    args = ap.parse_args()

    from app.config import settings
    src = args.src or settings.keol_src or DEFAULT_SRC
    if not os.path.isdir(src):
        print(f"KEOL 源目录不存在：{src}")
        return 1
    dst = args.dst or os.path.join(src, "ke", "回流", "管理系统-GOLD.md")

    from sqlalchemy import select

    from app.db import SessionLocal, init_db
    from app.gold.keol_export import known_names, render_keol_md
    from app.models import GoldItem

    init_db()
    db = SessionLocal()
    try:
        stmt = select(GoldItem).order_by(GoldItem.article_code, GoldItem.ke_id)
        if args.status.strip().lower() != "all":
            wanted = {s.strip() for s in args.status.split(",") if s.strip()}
            stmt = stmt.where(GoldItem.status.in_(wanted))
        items = db.scalars(stmt).all()
    finally:
        db.close()

    known_c, known_o, known_k = known_names(src, exclude_file=dst)
    text, stats = render_keol_md(items, known_c, known_o, known_k)
    print(f"KEOL 池已收录：concept {len(known_c)} / op {len(known_o)} / const {len(known_k)}（回流文件自身已排除）")
    print(f"成品 {stats['items']} 条：解析通过 {stats['parsed_ok']}，结构错误跳过 {stats['skipped_error']}")
    print(f"新声明：concept {stats['concepts_new']} / op {stats['ops_new']} / const {stats['consts_new']}；"
          f"事实 {stats['facts']}；问法注释 {stats['questions']}；待定注释 {stats['commented']}")
    if args.dry_run:
        print("--- dry-run，文件未写。前 40 行预览 ---")
        print("\n".join(text.splitlines()[:40]))
        return 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"已写入 {dst}")
    print("下一步（KEOL 侧）：python tools/keol_check.py 校验全库；python tools/load_db.py 重建 keol.db")
    return 0


if __name__ == "__main__":
    sys.exit(main())
