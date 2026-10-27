"""粘贴文本 → 自动识别本体（concept/op/const）与 KE 事实行 → 报告 → 导入。

解析目标形态（与 KE BNF / keol_check 的文本习惯一致）：
  concept 名 / concept 名 <: 父类
  op 名称(参数类型, ...) : 返回类型
  const __实例 : 类型
  算子(实参) = 值          —— KE 事实（值可为 {集合}、"字符串"、数字、引用）
识别报告标注：new / exists / similar / invalid；导入时 concept/op 进本体库
（可指定目标领域），const 仅报告（本系统不存实例），KE 事实行进成品库。
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Concept, GoldItem, Operator
from .services import create_concept, create_operator
from .similarity import find_similar

BUILTIN_TYPES = {"String", "Number", "Bool", "Date", "TimeInterval", "Real", "Name", "Concept"}

RE_CONCEPT = re.compile(r"^concept\s+(\S+?)(?:\s*<:\s*(\S+))?$")
RE_OP = re.compile(r"^op\s+([^\s(]+)\s*\(([^)]*)\)\s*:\s*(.+?)$")
RE_CONST = re.compile(r"^const\s+(\S+)\s*:\s*(\S+)$")
RE_FACT = re.compile(r"^([^\s(=]+)\s*\((.+?)\)\s*=\s*(.+?)$")
RE_CONST_REF = re.compile(r"__[\w一-鿿]+")
RE_SET_EXPR = re.compile(r"^Set\[(.+)\]$")


def _strip_comment(line: str) -> str:
    """去掉行尾 -- 注释（双引号内的 -- 不算）。"""
    out, in_str = [], False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            in_str = not in_str
        if not in_str and line[i:i + 2] == "--":
            break
        out.append(ch)
        i += 1
    return "".join(out).strip()


def _split_top(text: str, sep: str = ",") -> list[str]:
    """按顶层逗号切分（忽略 {} "" 内的逗号）。"""
    parts, depth, in_str, cur = [], 0, False, []
    for ch in text:
        if ch == '"':
            in_str = not in_str
        if not in_str:
            if ch in "{(":
                depth += 1
            elif ch in "})":
                depth -= 1
            elif ch == sep and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


def parse_keol(text: str) -> dict:
    """文本 → 结构化条目。返回 {concepts, operators, consts, facts, errors}。"""
    out = {"concepts": [], "operators": [], "consts": [], "facts": [], "errors": []}
    concepts_by_name: dict[str, dict] = {}
    in_fence = False
    for ln, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        # 围栏外的 Markdown 标题/引用行跳过；围栏内与无围栏文本都解析
        if not line or line.startswith("#") or line.startswith(">"):
            continue
        line = _strip_comment(line)
        if not line:
            continue
        m = RE_CONCEPT.match(line)
        if m:
            name, parent = m.group(1), m.group(2)
            row = concepts_by_name.get(name)
            if row is None:
                concepts_by_name[name] = {
                    "name": name, "parent": parent,
                    "parents": [parent] if parent else [], "line": ln,
                }
            else:
                if parent and parent not in row["parents"]:
                    row["parents"].append(parent)
                if row["parent"] is None:
                    row["parent"] = parent
            continue
        m = RE_OP.match(line)
        if m:
            args = _split_top(m.group(2))
            out["operators"].append({
                "name": m.group(1),
                "subject": args[0] if args else "",      # 括号第一个 = 主体概念
                "param_types": args[1:] if len(args) > 1 else [],
                "return_type": m.group(3).strip(), "line": ln,
            })
            continue
        m = RE_CONST.match(line)
        if m:
            out["consts"].append({"name": m.group(1), "type": m.group(2), "line": ln})
            continue
        m = RE_FACT.match(line)
        if m:
            out["facts"].append({
                "operator": m.group(1), "args": _split_top(m.group(2)),
                "value": m.group(3).strip().rstrip(";"), "line": ln, "raw": line,
            })
            continue
        out["errors"].append({"line": ln, "text": line[:120], "reason": "无法识别的行"})
    out["concepts"] = list(concepts_by_name.values())
    return out


# ---------------- 类型表达式校验（CO 侧补强） ----------------

def check_type_expr(expr: str, known: set[str]) -> str | None:
    """返回 None=合法；否则返回错误原因。支持 内置类型 / 概念名 / Set[X] 嵌套。"""
    expr = (expr or "").strip()
    if not expr:
        return "类型为空"
    m = RE_SET_EXPR.match(expr)
    if m:
        return check_type_expr(m.group(1), known)
    if expr in BUILTIN_TYPES or expr in known:
        return None
    return f"类型「{expr}」既不是内置类型也不是已声明概念"


# ---------------- 识别报告 ----------------

def build_report(db: Session, parsed: dict, domain: str = "通用") -> dict:
    known = {c.name for c in db.scalars(select(Concept)).all()}
    by_name = {c.name: c for c in db.scalars(select(Concept)).all()}
    ops_known = {(o.name, o.subject_concept_id) for o in db.scalars(select(Operator)).all()}
    subject_ids = {c.name: c.id for c in by_name.values()}
    parsed_concept_names = {c["name"] for c in parsed["concepts"]}

    concepts = []
    for item in parsed["concepts"]:
        row = {**item, "status": "new", "similar": []}
        if item["name"] in known:
            row["status"] = "exists"
            row["existing_status"] = by_name[item["name"]].status
        else:
            sim = find_similar(db, name=item["name"], aliases=(), limit=3)
            if sim:
                row["status"] = "similar"
                row["similar"] = [{"name": s["name"], "score": s["score"]} for s in sim]
        parent_names = item.get("parents") or ([] if not item.get("parent") else [item["parent"]])
        missing = [p for p in parent_names if p not in known and p not in parsed_concept_names]
        if missing:
            row.setdefault("warnings", []).append(
                "父类「" + "、".join(missing) + "」未声明（将挂空）")
        concepts.append(row)

    operators = []
    for item in parsed["operators"]:
        row = {**item, "status": "new", "issues": []}
        if not item["subject"]:
            row["issues"].append("缺少主体概念（括号第一个参数）")
        elif item["subject"] not in known and item["subject"] not in parsed_concept_names:
            row["issues"].append(f"主体概念「{item['subject']}」未声明")
        for t in item["param_types"]:
            err = check_type_expr(t, known | parsed_concept_names)
            if err:
                row["issues"].append(f"参数类型：{err}")
        err = check_type_expr(item["return_type"], known | parsed_concept_names)
        if err:
            row["issues"].append(f"返回类型：{err}")
        sid = subject_ids.get(item["subject"])
        if sid and (item["name"], sid) in ops_known:
            row["status"] = "exists"
        if row["issues"]:
            row["status"] = "invalid"
        operators.append(row)

    consts = []
    for item in parsed["consts"]:
        row = {**item, "issues": []}
        if not item["name"].startswith("__"):
            row["issues"].append("实例常量须以 __ 开头")
        err = check_type_expr(item["type"], known | parsed_concept_names)
        if err:
            row["issues"].append(err)
        consts.append(row)

    op_sigs = {o.name: (1 + len(o.param_types or [])) for o in db.scalars(select(Operator)).all()}
    parsed_fact_ops = {o["name"]: (1 + len(o["param_types"])) for o in parsed["operators"]}
    facts = []
    for item in parsed["facts"]:
        row = {**item, "issues": [],
               "const_refs": RE_CONST_REF.findall(" ".join(item["args"]) + " " + item["value"])}
        sig = op_sigs.get(item["operator"]) or parsed_fact_ops.get(item["operator"])
        if sig is None:
            row["issues"].append(f"算子「{item['operator']}」未声明")
        elif len(item["args"]) != sig:
            row["issues"].append(f"参数个数 {len(item['args'])} 与签名 {sig} 不符")
        for ref in row["const_refs"]:
            if not ref.startswith("__"):
                row["issues"].append(f"引用「{ref}」形态不规范")
        facts.append(row)

    return {
        "domain": domain,
        "concepts": concepts, "operators": operators,
        "consts": consts, "facts": facts, "errors": parsed["errors"],
        "counts": {
            "concepts_new": sum(1 for c in concepts if c["status"] == "new"),
            "concepts_exists": sum(1 for c in concepts if c["status"] == "exists"),
            "concepts_similar": sum(1 for c in concepts if c["status"] == "similar"),
            "operators_new": sum(1 for o in operators if o["status"] == "new"),
            "operators_invalid": sum(1 for o in operators if o["status"] == "invalid"),
            "consts": len(consts), "facts": len(facts),
            "facts_issues": sum(1 for f in facts if f["issues"]),
            "unparsed": len(parsed["errors"]),
        },
    }


# ---------------- 导入 ----------------

def import_parsed(db: Session, parsed: dict, *, domain: str, actor: str,
                  import_concepts: bool = True, import_operators: bool = True,
                  import_facts: bool = True, article_code: str = "") -> dict:
    stats = {"concepts_created": 0, "concepts_skipped": 0,
             "operators_created": 0, "operators_skipped": 0,
             "facts_created": 0, "facts_skipped": 0, "problems": []}
    name_to_id = {c.name: c.id for c in db.scalars(select(Concept)).all()}

    if import_concepts:
        for item in parsed["concepts"]:
            if item["name"] in name_to_id:
                stats["concepts_skipped"] += 1
                continue
            parent_names = item.get("parents") or ([] if not item.get("parent") else [item["parent"]])
            parent_ids = []
            for parent_name in parent_names:
                pid = name_to_id.get(parent_name)
                if pid is None:
                    stats["problems"].append(
                        f"concept {item['name']}: 父类「{parent_name}」未声明，已跳过该父类")
                else:
                    parent_ids.append(pid)
            try:
                c = create_concept(db, name=item["name"], aliases=[], definition="",
                                   parent_id=parent_ids[0] if parent_ids else None,
                                   parents=parent_ids, status="approved", actor=actor,
                                   domain=domain)
                name_to_id[c.name] = c.id
                stats["concepts_created"] += 1
            except Exception as e:  # noqa: BLE001  单条失败不阻断整批
                stats["problems"].append(f"concept {item['name']}: {getattr(e, 'detail', e)}")

    if import_operators:
        existing_ops = {(o.name, o.subject_concept_id) for o in db.scalars(select(Operator)).all()}
        for item in parsed["operators"]:
            sid = name_to_id.get(item["subject"])
            if not sid:
                stats["operators_skipped"] += 1
                stats["problems"].append(f"op {item['name']}: 主体概念「{item['subject'] or '缺失'}」缺失，已跳过")
                continue
            if (item["name"], sid) in existing_ops:
                stats["operators_skipped"] += 1
                continue
            try:
                create_operator(db, name=item["name"], subject_concept_id=sid,
                                owner_concept_id=None, param_types=item["param_types"],
                                return_type=item["return_type"], actor=actor)
                existing_ops.add((item["name"], sid))
                stats["operators_created"] += 1
            except Exception as e:  # noqa: BLE001
                stats["problems"].append(f"op {item['name']}: {getattr(e, 'detail', e)}")

    if import_facts and article_code:
        existing_items = {(g.article_code, g.ke_id) for g in db.scalars(select(GoldItem)).all()}
        for idx, item in enumerate(parsed["facts"], 1):
            ke_id = f"PASTE-{item['line']:04d}"
            if (article_code, ke_id) in existing_items:
                stats["facts_skipped"] += 1
                continue
            refs = RE_CONST_REF.findall(" ".join(item["args"]) + " " + item["value"])
            g = GoldItem(article_code=article_code, ke_id=ke_id, group_name=domain,
                         ke_text=item["raw"],
                         concepts=[item["operator"], *refs],
                         status="draft", created_by=actor, updated_by=actor)
            db.add(g)
            existing_items.add((article_code, ke_id))
            stats["facts_created"] += 1
        db.commit()
    return stats
