"""M26 · GOLD 成品 → KEOL 源文件回流。

把成品库条目（默认 gold 状态）里的 concept/op/const 声明与可提取的事实性绑定，
翻译成标准 KEOL，整文件重生成写进 KEOL 源树（默认 ke/回流/管理系统-GOLD.md）。
KEOL 侧 tools/load_db.py 下次重建自然收录；tools/keol_check.py 可对全库校验。

方言差异处理：
- GOLD 常量名无 __ 前缀 → 回流统一加 __（KEOL 命名规范）
- set(X) → Set[X]（KEOL  canonical 写法）
- GOLD 多为问题模式（output={谓词(a)|var a: 类型}）：变量按绑定代入后写成 `-- 问：` 注释；
  事实只从绑定段的「调用 = 值」断言提取（op=="="、左侧为调用、参数全部可坐实）
- 已被 KEOL 池收录的 concept/op/const 不重复声明（keol_check 对重复 const 报错）
- 类型/父类/主体不在 KEOL 池也不在本批新声明里 → 整行注释化（-- 待定），保证 keol_check 全绿
"""
from __future__ import annotations

import glob
import os
import re
from datetime import datetime

from ..ke.ke_validate import RE_CALL, RE_NAME_TOK, parse_ke

# KEOL 侧内置类型（与 keol_check.py 的 BUILTIN_TYPES 一致）
KEOL_BUILTIN_TYPES = {"String", "Number", "Bool", "Date", "TimeInterval", "Real", "Name", "Concept"}

RE_SET_PAREN = re.compile(r"^set\((.+)\)$", re.S)


def norm_type(t: str) -> str:
    """生产方言类型 → KEOL canonical：set(X) → Set[X]，其余原样。"""
    t = (t or "").strip().rstrip(";")
    m = RE_SET_PAREN.match(t)
    return f"Set[{m.group(1).strip()}]" if m else t


def iter_keol_text(path: str) -> str:
    """只取 ```keol 围栏内的行（与 sync_keol.py / keol_check.py 口径一致）。"""
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


def keol_sources(src: str) -> list[str]:
    pats: list[str] = []
    pats += glob.glob(os.path.join(src, "中国政府本体-CO-v*.md"))
    pats += glob.glob(os.path.join(src, "ke", "**", "*.md"), recursive=True)
    pats += glob.glob(os.path.join(src, "领域", "**", "*.md"), recursive=True)
    return sorted(p for p in pats if "archive" not in p)


def known_names(src: str, exclude_file: str | None = None) -> tuple[set[str], set[str], set[str]]:
    """KEOL 源已收录的名字：(concept 名, op 名, const 名)。exclude_file 用于排除回流文件自身。"""
    from ..paster import parse_keol
    concepts, ops, consts = set(), set(), set()
    for path in keol_sources(src):
        if exclude_file and os.path.abspath(path) == os.path.abspath(exclude_file):
            continue
        parsed = parse_keol(iter_keol_text(path))
        concepts.update(c["name"] for c in parsed["concepts"])
        ops.update(o["name"] for o in parsed["operators"])
        consts.update(c["name"] for c in parsed["consts"])
    return concepts, ops, consts


def _substitute(expr: str, var_map: dict[str, str]) -> str:
    """表达式里的变量名按绑定表替换为 __常量（整体 token 匹配）。"""
    if not var_map:
        return expr
    return RE_NAME_TOK.sub(lambda m: var_map.get(m.group(0), m.group(0)), expr)


def render_keol_md(items, known_concepts: set[str], known_ops: set[str],
                   known_consts: set[str], now: str | None = None) -> tuple[str, dict]:
    """GOLD 条目 → KEOL 文本 + 统计。items 需有 article_code/ke_id/group_name/ke_text 属性。"""
    now = now or datetime.now().isoformat(timespec="seconds")
    new_concepts: dict[str, dict] = {}
    new_ops: dict[tuple[str, str], dict] = {}
    new_consts: dict[str, dict] = {}
    sections: list[str] = []
    stats = {"items": 0, "parsed_ok": 0, "skipped_error": 0,
             "concepts_new": 0, "ops_new": 0, "consts_new": 0, "facts": 0,
             "commented": 0, "questions": 0}

    for it in items:
        stats["items"] += 1
        parsed = parse_ke(it.ke_text or "")
        if parsed.errors:
            stats["skipped_error"] += 1
            continue
        stats["parsed_ok"] += 1
        label = f"{it.article_code or '?'} · {it.ke_id or '?'} · {it.group_name or '?'}"
        body: list[str] = []

        local_consts: dict[str, str] = {}   # 本地名 → __名
        for c in parsed.consts:
            kname = c["name"] if c["name"].startswith("__") else "__" + c["name"]
            local_consts[c["name"]] = kname
            if kname in known_consts or kname in new_consts:
                continue
            new_consts[kname] = {"type": c["type"], "label": label}

        for c in parsed.concepts:
            parents = list(c.get("parents") or ([] if not c.get("parent") else [c["parent"]]))
            if c["name"] in known_concepts:
                continue
            if c["name"] in new_concepts:
                for p in parents:
                    if p not in new_concepts[c["name"]]["parents"]:
                        new_concepts[c["name"]]["parents"].append(p)
                continue
            new_concepts[c["name"]] = {"parents": parents, "label": label}
        for o in parsed.ops:
            key = (o["name"], o["subject"])
            if o["name"] in known_ops or key in new_ops:
                continue
            new_ops[key] = {"params": o["params"], "ret": o["ret"], "label": label}

        # 变量/常量代入表：本地 const 一律映射为 __名；x = 常量 的绑定变量同样坐实
        var_map: dict[str, str] = dict(local_consts)
        for out in parsed.outputs[:1]:
            for b in out["bindings"]:
                if b["kind"] == "assert" and b.get("op") == "=" and not RE_CALL.search(b["left"]):
                    rv = b["right"].strip()
                    if RE_NAME_TOK.fullmatch(rv) and rv in local_consts:
                        var_map[b["left"]] = local_consts[rv]
        # 问法注释（变量已坐实的代入 const）
        for out in parsed.outputs[:1]:
            head = _substitute(out["head"], var_map)
            body.append(f"-- 问：{head}")
            stats["questions"] += 1
        # 事实提取：绑定段「调用 = 值」且参数全部坐实
        for out in parsed.outputs[:1]:
            for b in out["bindings"]:
                if b["kind"] != "assert" or b.get("op") != "=" or not RE_CALL.search(b["left"]):
                    continue
                left = _substitute(b["left"], var_map)
                right = _substitute(b["right"], var_map)
                if RE_NAME_TOK.fullmatch(right) and not right.startswith("__") \
                        and not right.replace("_", "").isdigit():
                    continue  # 右值是未坐实的变量名
                masked_left = re.sub(r'"[^"]*"', '""', left)
                call_names = {m.group(1) for m in RE_CALL.finditer(masked_left)}
                unbound = [t for t in RE_NAME_TOK.findall(masked_left)
                           if t not in call_names and not t.startswith("__")
                           and not t.replace("_", "").isdigit()
                           and t.lower() not in ("true", "false", "null")]
                if unbound:
                    continue  # 参数含未坐实变量，只是查询模式不是事实
                body.append(f"{left} = {right}")
                stats["facts"] += 1
        if body:
            sections.append(f"-- ===== {label} =====\n" + "\n".join(body))

    # ---- 头部声明区：按依赖序输出（concept → op → const），不可解析的类型注释化 ----
    allowed = set(known_concepts) | KEOL_BUILTIN_TYPES | set(new_concepts)
    decl: list[str] = []
    for name, c in sorted(new_concepts.items()):
        parents = list(c.get("parents") or ([] if not c.get("parent") else [c["parent"]]))
        valid = [p for p in parents if p in allowed]
        missing = [p for p in parents if p not in allowed]
        if not valid and missing:
            decl.append(f"concept {name}  -- 来源 {c['label']}；"
                        f"原父类「{'、'.join(missing)}」未入库，待评审")
            stats["commented"] += 1
        elif valid:
            for parent in valid:
                decl.append(f"concept {name} <: {parent}")
            if missing:
                decl.append(f"-- 待定：concept {name} 的父类「{'、'.join(missing)}」未入库")
                stats["commented"] += 1
        else:
            decl.append(f"concept {name}")
        stats["concepts_new"] += 1
    for (name, subj), o in sorted(new_ops.items()):
        params = [norm_type(p) for p in o["params"]]
        ret = norm_type(o["ret"])

        def _type_ok(t: str) -> bool:
            m = re.fullmatch(r"Set\[(.+)\]", t)
            return (m.group(1).strip() if m else t) in allowed

        bad = [t for t in [subj, *params, ret] if not _type_ok(t)]
        if bad:
            decl.append(f"-- 待定：op {name}({', '.join([subj, *params])}) : {ret}"
                        f"（类型未入库：{'、'.join(bad)}；来源 {o['label']}）")
            stats["commented"] += 1
            continue
        decl.append(f"op {name}({', '.join([subj, *params])}) : {ret}")
        stats["ops_new"] += 1
    for kname, c in sorted(new_consts.items()):
        t = norm_type(c["type"])
        if t not in allowed:
            decl.append(f"-- 待定：const {kname} : {t}（类型未入库；来源 {c['label']}）")
            stats["commented"] += 1
            continue
        decl.append(f"const {kname} : {t}")
        stats["consts_new"] += 1

    header = [
        "# 管理系统 GOLD 成品回流（自动生成，请勿手改）",
        f"# 生成时间：{now}；重生成：在本体管理系统根目录运行 python scripts/gold_to_keol.py",
        f"# 条目 {stats['items']}（解析通过 {stats['parsed_ok']}，跳过 {stats['skipped_error']}）；"
        f"新声明：concept {stats['concepts_new']} / op {stats['ops_new']} / const {stats['consts_new']}；"
        f"事实 {stats['facts']}；待定注释 {stats['commented']}",
        "",
        "```keol",
    ]
    parts = header + decl
    if sections:
        parts += ["", "-- ============ 各条目的问法与事实 ============", ""] + sections
    parts.append("```")
    parts.append("")
    return "\n".join(parts), stats
