"""M25 · KEOL 实例/事实镜像同步。

复用 KEOL 侧 tools/load_db.py 的解析器（parse_node/canon/kind_of/iter_keol_lines/sources），
规范化口径与 keol.db 完全一致；扫描范围也与其一致（含 _ 开头的评审文件、排除 archive/），
与词表同步（scripts/sync_keol.py 的 concept/op 部分，跳过候选池）是有意不同的口径：
镜像表的目标是「KEOL 侧知道什么」，故全量镜像。

重建方式：单事务 delete-all + 批量插入（幂等；镜像数据不逐条留审计痕，synced_at 记批次时间）。
"""
from __future__ import annotations

import importlib
import os
import re
import sys
import time

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import KeolEntity, KeolFact

RE_CONST = re.compile(r"^const\s+(__[^\s:：]+)\s*[:：]\s*(.+)$")
RE_CONCEPT = re.compile(r"^concept\s")
RE_OP = re.compile(r"^op\s")
RE_SKIP = re.compile(r"^(output|var)\b")
RE_FACT = re.compile(r"^([^(){}]+?)\((.*)\)\s*=\s*(.+)$", re.S)


def _load_keol_parser(src: str):
    """把 KEOL 侧 tools/ 加入 sys.path 并 import 其 load_db（权威解析口径）。"""
    tools = os.path.join(src, "tools")
    if not os.path.isfile(os.path.join(tools, "load_db.py")):
        raise FileNotFoundError(f"KEOL 源目录缺 tools/load_db.py：{src}")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    importlib.invalidate_caches()
    return importlib.import_module("load_db")


def parse_instances(src: str) -> tuple[list[dict], list[dict], dict]:
    """解析 KEOL 源 → (entities, facts, stats)。entities 按名去重（同源重复声明后者覆盖）。"""
    loader = _load_keol_parser(src)
    entities: dict[str, dict] = {}
    facts: list[dict] = []
    files = 0
    for path in loader.sources():
        rel = os.path.relpath(path, src).replace("\\", "/")
        rows = list(loader.iter_keol_lines(path))
        if not rows:
            continue
        files += 1
        for ln, line in rows:
            if RE_CONCEPT.match(line) or RE_OP.match(line) or RE_SKIP.match(line):
                continue
            m = RE_CONST.match(line)
            if m:
                entities[m.group(1)] = {"name": m.group(1), "type": m.group(2).strip(),
                                        "src": f"{rel}:{ln}"}
                continue
            m = RE_FACT.match(line)
            if m:
                args = [loader.parse_node(x) for x in loader.split_top(m.group(2))]
                vnode = loader.parse_node(m.group(3))
                facts.append({
                    "pred": m.group(1).strip(),
                    "key": ", ".join(loader.canon(a) for a in args),
                    "subj": loader.canon(args[0]) if args else "",
                    "value": loader.canon(vnode),
                    "kind": loader.kind_of(vnode),
                    "src": f"{rel}:{ln}", "line": ln,
                })
    stats = {"files": files,
             "consts": len(entities), "facts": len(facts),
             "preds": len({f["pred"] for f in facts}),
             "types": len({e["type"] for e in entities.values()})}
    return list(entities.values()), facts, stats


def sync_instances(db: Session, src: str, chunk: int = 5000) -> dict:
    """全量重建 keol_entities / keol_facts。返回统计（含耗时）。"""
    t0 = time.time()
    entities, facts, stats = parse_instances(src)
    db.execute(delete(KeolFact))
    db.execute(delete(KeolEntity))
    for i in range(0, len(entities), chunk):
        db.execute(KeolEntity.__table__.insert(), entities[i:i + chunk])
    for i in range(0, len(facts), chunk):
        db.execute(KeolFact.__table__.insert(), facts[i:i + chunk])
    db.commit()
    stats["elapsed_s"] = round(time.time() - t0, 1)
    return stats


def mirror_status(db: Session) -> dict:
    """镜像表现状（API/页面共用）。"""
    latest = db.scalar(select(func.max(KeolEntity.synced_at)))
    return {
        "entities": db.scalar(select(func.count(KeolEntity.id))) or 0,
        "facts": db.scalar(select(func.count(KeolFact.id))) or 0,
        "types": db.scalar(select(func.count(func.distinct(KeolEntity.type)))) or 0,
        "preds": db.scalar(select(func.count(func.distinct(KeolFact.pred)))) or 0,
        "synced_at": latest.isoformat(timespec="seconds") if latest else None,
    }
