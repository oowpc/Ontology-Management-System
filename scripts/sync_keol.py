"""KEOL 词表同步桥：政府本体库（.md 里的 ```keol 代码块）→ 本系统概念/算子库。

数据源（与 政府本体/tools/load_db.py 的扫描口径一致，.md 为权威源，不读派生的 keol.db）：
  <src>/中国政府本体-CO-v*.md、<src>/ke/**/*.md、<src>/领域/**/*.md（排除 archive/）

映射：
  concept 名 <: 父   → concepts（domain 按来源：根 CO 文件→通用；领域/<X>-CO-*.md→X；ke/→通用）
  op 名(主体, 参数…) : 返回 → operators（首参=主体概念，Set[X] 返回类型原样保留）
  const __实例 / 事实行 → 只统计不导入（本系统不存实例，实例留在 keol.db 查询侧）
  下划线开头文件（_概念池评审记录 等候选池）→ 默认跳过；--include-review-pool 时以 draft 导入

幂等：走 importer.import_payload（名称自然键 upsert + 审计留痕），重复同步零变化。

用法（项目根目录）：
  python scripts/sync_keol.py                          # 直连本库写入词表（默认源：--src > KEOL_SRC > 内置默认）
  python scripts/sync_keol.py --with-instances         # 词表 + 实例/事实镜像（keol_entities/keol_facts 全量重建）
  python scripts/sync_keol.py --instances-only         # 只重建实例/事实镜像
  python scripts/sync_keol.py --dry-run                # 只解析与比对，不写库、不迁移（需先 upgrade head）
  python scripts/sync_keol.py --src D:/path/政府本体    # 指定 KEOL 源目录
  python scripts/sync_keol.py --api http://127.0.0.1:8000 --token ke_...   # 词表改走 API（服务在线时；镜像仅支持直连模式）
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_SRC = r"C:\Users\wang\Desktop\工作项目\本体与政务\政府本体"
ACTOR = "keol-sync"


def sources(src: str) -> list[str]:
    """KEOL 源文件清单（口径同 政府本体/tools/load_db.py）。"""
    pats: list[str] = []
    pats += glob.glob(os.path.join(src, "中国政府本体-CO-v*.md"))
    pats += glob.glob(os.path.join(src, "ke", "**", "*.md"), recursive=True)
    pats += glob.glob(os.path.join(src, "领域", "**", "*.md"), recursive=True)
    return sorted(p for p in pats if "archive" not in p)


def domain_of(path: str, src: str) -> str:
    """来源路径 → 领域名。根 CO → 通用；领域/<X>-CO-v*.md → X；ke/ 与其他 → 通用。"""
    rel = os.path.relpath(path, src).replace("\\", "/")
    if rel.startswith("领域/"):
        stem = os.path.splitext(os.path.basename(rel))[0].lstrip("_")  # 育儿补贴-CO-v0.1
        return stem.split("-CO-")[0] or "通用"
    return "通用"


def iter_keol_block_text(path: str) -> str:
    """只取 ```keol 围栏内的行（与 keol_check/load_db 口径一致，围栏外散文不解析）。"""
    lines, in_block = [], False
    with open(path, encoding="utf-8") as f:
        for raw in f:
            s = raw.rstrip("\n")
            if s.strip().startswith("```"):
                if s.strip().startswith("```keol"):
                    in_block = True
                elif in_block:
                    in_block = False
                continue
            if in_block:
                lines.append(s)
    return "\n".join(lines)


def load_meta(path: str) -> tuple[dict, dict]:
    """读取 <md>.meta.json（转换器产出的定义/别名/来源）。缺省返回空表。"""
    p = Path(path)
    meta_path = p.with_name(p.stem + ".meta.json")
    if not meta_path.exists():
        return {}, {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 元数据解析失败 {meta_path}: {e}")
        return {}, {}
    concepts = data.get("concepts") or {}
    if isinstance(concepts, list):
        concepts = {c.get("name"): c for c in concepts if c.get("name")}
    ops = {(o.get("name"), o.get("subject")): o for o in (data.get("operators") or [])
           if o.get("name") and o.get("subject")}
    return concepts, ops


def build_payload(src: str, include_review_pool: bool = False) -> tuple[dict, dict]:
    """解析全部源文件 → (import_payload 载荷, 统计)。概念按名去重（先见先得，冲突记告警）。

    下划线开头文件（评审记录/候选池）默认跳过；include_review_pool=True 时以 draft 状态导入。
    """
    from app.paster import parse_keol

    concepts: dict[str, dict] = {}
    operators: dict[tuple[str, str], dict] = {}
    stats = {"files": 0, "review_files_skipped": [], "consts_skipped": 0,
             "facts_skipped": 0, "parse_errors": 0, "warnings": []}

    for path in sources(src):
        base = os.path.basename(path)
        if base.startswith("_") and not include_review_pool:
            stats["review_files_skipped"].append(base)
            continue
        # 候选池条目一律 draft（待评审）；正式 CO/ke 文件为 approved
        status = "draft" if base.startswith("_") else "approved"
        text = iter_keol_block_text(path)
        if not text.strip():
            continue
        stats["files"] += 1
        parsed = parse_keol(text)
        dom = domain_of(path, src)
        stats["consts_skipped"] += len(parsed["consts"])
        stats["facts_skipped"] += len(parsed["facts"])
        stats["parse_errors"] += len(parsed["errors"])
        meta_c, meta_o = load_meta(path)
        for c in parsed["concepts"]:
            name = c["name"]
            parents = list(c.get("parents") or ([] if not c.get("parent") else [c["parent"]]))
            cm = meta_c.get(name) or {}
            row = {
                "name": name, "domain": dom,
                "parent": parents[0] if parents else "", "parents": parents,
                "definition": cm.get("definition") or "",
                "aliases": list(cm.get("aliases") or []),
                "source_uri": cm.get("uri") or "",
                "ontology_module": cm.get("module") or dom,
                "status": status,
            }
            prev = concepts.get(name)
            if prev is None:
                concepts[name] = row
                continue
            # 多文件同名概念：域/状态/定义保留先见，父类与别名取并集
            for p in row["parents"]:
                if p not in prev["parents"]:
                    prev["parents"].append(p)
            prev["parent"] = prev["parents"][0] if prev["parents"] else ""
            for a in row["aliases"]:
                if a not in prev["aliases"]:
                    prev["aliases"].append(a)
            if not prev.get("definition"):
                prev["definition"] = row["definition"]
            if not prev.get("source_uri"):
                prev["source_uri"] = row["source_uri"]
            if not prev.get("ontology_module"):
                prev["ontology_module"] = row["ontology_module"]
            if prev["domain"] != row["domain"] or prev["status"] != row["status"]:
                stats["warnings"].append(
                    f"概念「{name}」多处声明且域/状态不一致，保留先见："
                    f"domain={prev['domain']},status={prev['status']}；忽略："
                    f"domain={row['domain']},status={row['status']}")
        for o in parsed["operators"]:
            if not o.get("subject"):
                stats["warnings"].append(f"算子「{o.get('name')}」缺主体概念，跳过（{path}:{o.get('line')}）")
                continue
            key = (o["name"], o["subject"])
            om = meta_o.get(key) or {}
            row = {
                "name": o["name"], "subject": o["subject"],
                "param_types": o.get("param_types") or [],
                "return_type": o.get("return_type") or "",
                "source_uri": om.get("uri") or "",
                "ontology_module": om.get("module") or dom,
                "status": status,
            }
            prev = operators.get(key)
            if prev is None:
                operators[key] = row
            else:
                if not prev.get("source_uri"):
                    prev["source_uri"] = row["source_uri"]
                if not prev.get("ontology_module"):
                    prev["ontology_module"] = row["ontology_module"]
    payload = {"concepts": list(concepts.values()), "operators": list(operators.values())}
    return payload, stats


def post_api(api: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        api.rstrip("/") + "/api/import/json",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="KEOL 词表 → 本系统概念/算子库（幂等同步）")
    ap.add_argument("--src", help="KEOL 源目录（缺省：KEOL_SRC 环境变量 > 内置默认）")
    ap.add_argument("--dry-run", action="store_true", help="只解析比对，不写库")
    ap.add_argument("--with-instances", action="store_true", help="同时重建实例/事实镜像（仅直连模式）")
    ap.add_argument("--instances-only", action="store_true", help="只重建实例/事实镜像（跳过词表）")
    ap.add_argument("--api", help="改走 API：管理系统 base URL（需配合 --token）")
    ap.add_argument("--token", default=os.environ.get("ONTOLOGY_TOKEN", ""), help="API Token（或环境变量 ONTOLOGY_TOKEN）")
    ap.add_argument("--include-review-pool", action="store_true",
                    help="同时导入 _ 开头的评审候选池文件（条目以 draft 状态入库）")
    ap.add_argument("--batch-id", help="导入批次 ID（缺省自动生成）；用于批量回滚")
    args = ap.parse_args()

    from app.config import settings
    args.src = args.src or settings.keol_src or DEFAULT_SRC
    if not os.path.isdir(args.src):
        print(f"源目录不存在：{args.src}")
        return 1
    want_vocab = not args.instances_only
    want_instances = args.with_instances or args.instances_only

    if want_instances and args.api:
        print("实例/事实镜像只支持直连模式（去掉 --api）")
        return 1

    stats = {"review_files_skipped": [], "files": 0, "consts_skipped": 0,
             "facts_skipped": 0, "parse_errors": 0, "warnings": []}
    payload = {"concepts": [], "operators": []}
    if want_vocab:
        payload, stats = build_payload(args.src, include_review_pool=args.include_review_pool)
        print(f"扫描源文件 {len(sources(args.src))} 个（含 KEOL 块 {stats['files']} 个）")
        if stats["review_files_skipped"]:
            print(f"跳过评审候选池文件 {len(stats['review_files_skipped'])} 个（--include-review-pool 可并入，状态=draft）："
                  + "、".join(stats["review_files_skipped"]))
        print(f"解析得到：concept {len(payload['concepts'])}，op {len(payload['operators'])}；"
              f"跳过 const {stats['consts_skipped']}、事实 {stats['facts_skipped']}（本系统不存实例）")
        if stats["parse_errors"]:
            print(f"提示：{stats['parse_errors']} 行未识别（KEOL 块内的非声明行，不影响 concept/op 同步）")
        for w in stats["warnings"][:20]:
            print("  WARN", w)

    if args.dry_run:
        from sqlalchemy import select

        from app.db import SessionLocal
        from app.models import Concept, Operator
        # dry-run 只读：不执行 init_db()/Alembic 迁移，避免顺带改库。
        # 若库结构落后，请先手动 `alembic upgrade head`。
        db = SessionLocal()
        try:
            if want_vocab:
                have_c = {c.name for c in db.scalars(select(Concept)).all()}
                have_o = {(o.name, o.subject_concept_id) for o in db.scalars(select(Operator)).all()}
                id2name = {c.id: c.name for c in db.scalars(select(Concept)).all()}
                have_o = {(n, id2name.get(sid)) for n, sid in have_o}
                new_c = [c["name"] for c in payload["concepts"] if c["name"] not in have_c]
                new_o = [f"{o['name']}({o['subject']})" for o in payload["operators"]
                         if (o["name"], o["subject"]) not in have_o]
                print(f"dry-run：新概念 {len(new_c)} / 新算子 {len(new_o)}（其余为既有条目，导入将按差异更新）")
                print("  新概念样例:", "、".join(new_c[:15]) + ("…" if len(new_c) > 15 else ""))
                print("  新算子样例:", "、".join(new_o[:15]) + ("…" if len(new_o) > 15 else ""))
            if want_instances:
                from app.keolsync import parse_instances
                entities, facts, istats = parse_instances(args.src)
                print(f"dry-run：实例 {len(entities)} / 事实 {len(facts)}"
                      f"（谓词 {istats['preds']} 种、类型 {istats['types']} 种，将全量重建镜像表）")
        finally:
            db.close()
        return 0

    if args.api:
        if not args.token:
            print("--api 模式需要 --token 或环境变量 ONTOLOGY_TOKEN")
            return 1
        result = post_api(args.api, args.token, payload)
        print("导入结果:", json.dumps(result, ensure_ascii=False))
        return 0

    from app.db import SessionLocal, init_db
    init_db()
    db = SessionLocal()
    try:
        if want_vocab:
            from app.importer import import_payload
            batch_id = args.batch_id or f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
            files = []
            for p in sources(args.src):
                meta = Path(p).with_name(Path(p).stem + ".meta.json")
                for f in ([Path(p)] + ([meta] if meta.exists() else [])):
                    files.append({"path": str(f), "size": f.stat().st_size,
                                  "sha256": hashlib.sha256(f.read_bytes()).hexdigest()})
            result = import_payload(db, payload, f"{ACTOR}:{batch_id}",
                                    batch_id=batch_id, source=args.src, files=files)
            print("导入批次:", batch_id)
            print("导入结果:", json.dumps(result, ensure_ascii=False))
        if want_instances:
            from app.keolsync import mirror_status, sync_instances
            istats = sync_instances(db, args.src)
            print(f"实例镜像重建：实体 {istats['consts']} / 事实 {istats['facts']}"
                  f"（谓词 {istats['preds']} 种），耗时 {istats['elapsed_s']}s")
            print("镜像现状:", json.dumps(mirror_status(db), ensure_ascii=False))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
