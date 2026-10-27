"""数据库备份（P2）：SQLite 在线备份 + 份数轮换；PostgreSQL 给出 pg_dump 口径。

SQLite 用 sqlite3 标准库的 online backup API（源库运行中可用，不阻塞读写——配合 WAL 更安全）。
备份文件：data/backups/ontology-YYYYMMDD-HHMMSS.db（默认保留最近 14 份，BACKUP_KEEP 可调）。
建议配合系统计划任务/cron 每日执行，例如 Windows 计划任务：
  schtasks /create /tn "ontology-backup" /tr "python C:\\path\\scripts\\backup_db.py" /sc daily /st 03:10

用法（项目根目录）：python scripts/backup_db.py [--keep 14]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_KEEP = int(os.environ.get("BACKUP_KEEP", "14"))


def sqlite_path(db_url: str) -> str | None:
    if not db_url.startswith("sqlite:///"):
        return None
    p = db_url[len("sqlite:///"):]
    return None if p == ":memory:" else p


def backup_sqlite(src_path: str, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / f"ontology-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(src_path)
    try:
        dst_con = sqlite3.connect(str(dst))
        with dst_con:
            src.backup(dst_con)
        dst_con.close()
    finally:
        src.close()
    return dst


def rotate(backup_dir: Path, keep: int) -> list[Path]:
    olds = sorted(backup_dir.glob("ontology-*.db"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    removed = olds[keep:]
    for p in removed:
        p.unlink()
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description="本体库备份（SQLite 在线备份；PG 给 pg_dump 口径）")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="保留份数（默认 BACKUP_KEEP=14）")
    args = ap.parse_args()

    from app.config import settings
    src = sqlite_path(settings.db_url)
    if src is None:
        print("非 SQLite 数据源。PostgreSQL 请用 pg_dump，例如：")
        print("  pg_dump \"$DB_URL\" -Fc -f backups/ontology-$(date +%Y%m%d-%H%M%S).dump")
        return 0
    if not os.path.exists(src):
        print(f"数据库文件不存在：{src}")
        return 1
    backup_dir = Path(src).parent / "backups"
    dst = backup_sqlite(src, backup_dir)
    size_kb = dst.stat().st_size // 1024
    removed = rotate(backup_dir, args.keep)
    print(f"备份完成：{dst}（{size_kb} KB）")
    if removed:
        print(f"轮换清理 {len(removed)} 份旧备份（保留最近 {args.keep} 份）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
