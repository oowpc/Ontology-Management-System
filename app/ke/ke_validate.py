"""M20 · KE 内容校验：成品库 KE 文本的结构 / 闭词表 / 签名 / 绑定检查。

参考 NL2KE ke_validate 的分层口径（解析 → 算子提取 → light 静态检查），
但面向本系统的生产方言（现行协作 xlsx 实际写法，与 paster 的 KEOL 识别同源）：

  concept 名 / concept 子 <: 父
  const 名 : 类型
  op 名(主体类型, 参数类型...) : 返回类型        # 返回类型 set(X) / Set[X] 均可
  output = { 表达式 | var x: 类型, x = 常量, 约束... }   # {} 与 var 关键字可省；可跨行

级别语义（对应 NL2KE「规则校验失败不得冻结」）：
- error   结构/形态错误（无法识别的行、缺 output、括号失衡、已知签名 arity 不符…）
          → 阻断定稿（reviewed→gold 强校验，可用 GOLD_FINALIZE_VALIDATE=0 关闭）
- warning 闭词表外引用（概念/算子/常量未入库）→ 不阻断：标注先行、本体后补是正常流程，
          但这些名字是「大本体整理」阶段的待办信号
本系统不存实例，const 只查本文声明；校验结果随条目缓存（gold_items.validation）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Concept, Operator

ENGINE_VERSION = 1          # 校验口径变更时 +1（缓存结果带版本，可据此判旧）
MAX_ISSUES = 30             # 每级最多入列条数（防爆 JSON），超出只计数

BUILTIN_TYPES = {"string", "number", "bool", "date", "timeinterval", "real", "name", "concept"}
BUILTIN_CALLS = {"sympy_expression"}   # 字面量构造子（参数是字符串），不算库外算子
BOOL_LITERALS = {"true", "false", "null"}

_NAME = r"A-Za-z0-9_一-鿿"
RE_DECL = re.compile(r"^(concept|const|op|output)\b")
RE_CONCEPT = re.compile(rf"^concept\s+([{_NAME}]+?)(?:\s*<:\s*([{_NAME}]+))?$")
RE_CONST = re.compile(rf"^const\s+([{_NAME}]+(?:\s*,\s*[{_NAME}]+)*)\s*:\s*(\S+)$")  # 支持并列 const A,B:类型
RE_OP = re.compile(rf"^op\s+([{_NAME}][{_NAME}\-、]*)\s*\(([^)]*)\)\s*[:=]\s*(.+?)$")  # 返回类型分隔 : 或 =
RE_BULLET = re.compile(r"^[*\-•·▪]\s*(?=(?:concept|const|op|output)\b)")
RE_CALL = re.compile(rf"(?<![{_NAME}])([{_NAME}][{_NAME}\-、]*)\s*\(")
RE_NAME_TOK = re.compile(rf"[{_NAME}]+")
RE_MEMBER = re.compile(rf"^(?:var\s+)?([{_NAME}]+(?:\s+[{_NAME}]+)*)\s*:\s*(\S+)$")
RE_SET_WRAP = re.compile(r"^(?:set|Set)\s*[\(〔\[]\s*(.+?)\s*[\)〕\]]$")
# 绑定段内的比较/断言符（顶层）：<: 归属断言、!= >= <= > <、= 赋值/等式断言
RE_BINOP = re.compile(r"<:|!=|>=|<=|(?<![!<>])=(?!=)|>|<")

# 全角标点 → 半角（标注实景常见：op 名（主体）：类型）；字符串字面量内部不动
_FULLWIDTH = str.maketrans({"（": "(", "）": ")", "，": ",", "：": ":", "；": ";",
                            "＝": "=", "｜": "|", "｛": "{", "｝": "}", "【": "[", "】": "]"})


def _normalize_punct(line: str) -> str:
    masked = _mask_strings(line)
    return "".join(ch if masked[i] == " " else ch.translate(_FULLWIDTH)
                   for i, ch in enumerate(line))


@dataclass
class ParsedKE:
    concepts: list[dict] = field(default_factory=list)   # {name, parent, line}
    consts: list[dict] = field(default_factory=list)     # {name, type, line}
    ops: list[dict] = field(default_factory=list)        # {name, subject, params, ret, line}
    outputs: list[dict] = field(default_factory=list)    # {head, bindings, line}
    errors: list[dict] = field(default_factory=list)     # {line, kind, message}
    warnings: list[dict] = field(default_factory=list)   # 解析期宽待（如行首项目符号已忽略）


def _issue(line: int, kind: str, message: str) -> dict:
    return {"line": line, "kind": kind, "message": message}


def _mask_strings(text: str) -> str:
    """字符串字面量内容替换为等长空格（后续括号/逗号扫描不被串内字符干扰）。"""
    out, in_q, i = list(text), None, 0
    while i < len(text):
        ch = text[i]
        if in_q:
            if ch == "\\":
                out[i] = " "
                if i + 1 < len(text):
                    out[i + 1] = " "
                i += 2
                continue
            if ch == in_q:
                in_q = None
            else:
                out[i] = " "
        elif ch in "\"'":
            in_q = ch
        i += 1
    return "".join(out)


def _strip_comment(line: str) -> str:
    """去行尾注释（-- 或 //；引号内不算；行首 # 整行注释由调用方处理）。"""
    masked = _mask_strings(line)
    cut = len(line)
    for marker in ("--", "//"):
        p = masked.find(marker)
        if p >= 0:
            cut = min(cut, p)
    return line[:cut].strip()


def _depth(text: str) -> int:
    """括号净深度（{} () [] 合计；字符串已屏蔽）。"""
    masked = _mask_strings(text)
    d = 0
    for ch in masked:
        if ch in "{([":
            d += 1
        elif ch in "})]":
            d -= 1
    return d


def _split_top(text: str, seps: str = ",") -> list[str]:
    """按顶层分隔符切分（忽略引号与括号内的；支持中文逗号，）。"""
    parts, depth, cur = [], 0, []
    masked = _mask_strings(text)
    for i, ch in enumerate(text):
        m = masked[i]
        if m in "{([":
            depth += 1
        elif m in "})]":
            depth -= 1
        elif depth == 0 and m in seps:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _logical_lines(text: str, errors: list[dict], warnings: list[dict]) -> list[tuple[int, str]]:
    """物理行 → 逻辑语句（output 括号未闭合时续行拼接；markdown 围栏/注释/空行跳过）。"""
    stmts: list[tuple[int, str]] = []
    open_depth = 0
    in_fence = False
    for ln, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        line = _strip_comment(line)
        if not line or line.startswith("#"):
            continue
        line = _normalize_punct(line)
        if open_depth > 0:                      # output 语句未闭合 → 续行
            ln0, prev = stmts[-1]
            stmts[-1] = (ln0, prev + " " + line)
            open_depth = _depth(stmts[-1][1])
            continue
        if RE_BULLET.match(line):               # 标注实景：* concept … 之类项目符号前缀
            line = RE_BULLET.sub("", line)
            warnings.append(_issue(ln, "bullet_prefix", "行首项目符号已忽略"))
        m = RE_DECL.match(line)
        if m:
            stmts.append((ln, line))
            open_depth = _depth(line) if m.group(1) == "output" else 0
        else:
            errors.append(_issue(ln, "unrecognized_line",
                                 f"无法识别的行：{line[:60]}{'…' if len(line) > 60 else ''}"
                                 "（应为 concept / const / op / output 声明或注释）"))
    if open_depth > 0 and stmts:
        ln0, s = stmts[-1]
        errors.append(_issue(ln0, "unbalanced_brackets", "括号/花括号未闭合（语句跨行未结束）"))
    return stmts


def _find_close(text: str, open_idx: int) -> int:
    """从开括号位置找配对闭括号（引号屏蔽）；找不到返回 -1。"""
    masked = _mask_strings(text)
    depth = 0
    for i in range(open_idx, len(text)):
        ch = masked[i]
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _extract_calls(expr: str) -> list[tuple[str, int]]:
    """表达式 → [(算子名, 实参个数)]。字符串字面量已屏蔽；嵌套调用逐层提取。"""
    masked = _mask_strings(expr)
    calls = []
    for m in RE_CALL.finditer(masked):
        name = m.group(1)
        open_idx = masked.index("(", m.start(1))
        close_idx = _find_close(expr, open_idx)
        if close_idx < 0:
            calls.append((name, -1))            # -1 = 括号不配对（结构错误另行报告）
            continue
        inner = expr[open_idx + 1:close_idx].strip()
        argc = len(_split_top(inner)) if inner else 0
        calls.append((name, argc))
    return calls


def _parse_output(text: str, line: int, errors: list[dict]) -> dict | None:
    """output 语句 → {head, bindings:[{kind,...}], line}；形态错记 errors 并返回 None。"""
    m = re.match(r"^output\s*=\s*(.*)$", text, re.S)
    if not m:
        errors.append(_issue(line, "bad_output", "output 语句缺少 ="))
        return None
    body = m.group(1).strip().rstrip(";").strip()
    if not body:
        errors.append(_issue(line, "bad_output", "output 缺少表达式"))
        return None
    if body.startswith("{"):
        close = _find_close(body, 0)
        if close < 0:
            errors.append(_issue(line, "unbalanced_brackets", "output 花括号未闭合"))
            return None
        inner = body[1:close]
        trailing = body[close + 1:].strip().rstrip(";").strip()
        if trailing:
            errors.append(_issue(line, "bad_output",
                                 f"output 闭合括号后有多余内容：{trailing[:40]}"))
            return None
    else:
        inner = body
    parts = _split_top(inner, seps="|")
    head = parts[0].strip()
    bindings_raw = parts[1] if len(parts) > 1 else ""
    if len(parts) > 2:
        errors.append(_issue(line, "bad_output", "output 含多个顶层 |（只能有一个绑定段）"))
        return None
    if not head:
        errors.append(_issue(line, "bad_output", "output 缺少输出表达式"))
        return None
    bindings = []
    for piece in _split_top(bindings_raw):
        mm = RE_MEMBER.match(piece) if "<:" not in piece else None
        if mm:
            for var in mm.group(1).split():
                bindings.append({"kind": "member", "var": var, "type": mm.group(2).strip()})
            continue
        masked = _mask_strings(piece)
        eq = RE_BINOP.search(masked)
        if eq:
            bindings.append({"kind": "assert",
                             "left": piece[:eq.start()].strip(),
                             "right": piece[eq.end():].strip()})
            continue
        if RE_CALL.search(piece):
            # 裸调用谓词约束（如 是否符合条件(a)，省略 =true 的写法）
            bindings.append({"kind": "assert", "left": piece.strip(), "right": ""})
            continue
        errors.append(_issue(line, "bad_binding",
                             f"绑定/约束形态无法识别：{piece[:50]}（应为 var x: 类型、x: 类型、表达式 比较符 值 或 谓词调用）"))
    return {"head": head, "bindings": bindings, "line": line}


def parse_ke(text: str) -> ParsedKE:
    """KE 文本 → 结构化解析（声明 + output）。不做词表检查（那是 validate_ke 的事）。"""
    out = ParsedKE()
    if not (text or "").strip():
        out.errors.append(_issue(0, "empty", "KE 内容为空"))
        return out
    for ln, stmt in _logical_lines(text, out.errors, out.warnings):
        kw = RE_DECL.match(stmt).group(1)
        if kw == "concept":
            m = RE_CONCEPT.match(stmt)
            if m:
                out.concepts.append({"name": m.group(1), "parent": m.group(2), "line": ln})
            else:
                out.errors.append(_issue(ln, "bad_concept", f"concept 声明形态错：{stmt[:60]}"))
        elif kw == "const":
            m = RE_CONST.match(stmt)
            if m:
                for name in re.split(r"\s*,\s*", m.group(1)):   # 并列声明 const A,B:类型
                    out.consts.append({"name": name, "type": m.group(2), "line": ln})
            else:
                out.errors.append(_issue(ln, "bad_const",
                                         f"const 声明形态错：{stmt[:60]}（应为 const 名 : 类型，半角冒号）"))
        elif kw == "op":
            m = RE_OP.match(stmt)
            if m:
                args = _split_top(m.group(2))
                out.ops.append({"name": m.group(1), "subject": args[0] if args else "",
                                "params": args[1:] if len(args) > 1 else [],
                                "ret": m.group(3).strip(), "line": ln})
            else:
                out.errors.append(_issue(ln, "bad_op",
                                         f"op 声明形态错：{stmt[:60]}（应为 op 名(主体类型, …) : 返回类型）"))
        else:  # output
            parsed = _parse_output(stmt, ln, out.errors)
            if parsed:
                out.outputs.append(parsed)
    if not out.outputs:
        out.errors.append(_issue(0, "no_output", "缺少 output 语句"))
    elif len(out.outputs) > 1:
        out.errors.append(_issue(out.outputs[1]["line"], "multi_output",
                                 f"output 语句有 {len(out.outputs)} 个，要求恰好 1 个"))
    return out


def _check_type_expr(expr: str, known: set[str]) -> str | None:
    """类型表达式合法性：内置类型（大小写不论）/ Set 嵌套 / 已知名。None=合法。"""
    expr = (expr or "").strip().rstrip(";")
    if not expr:
        return "类型为空"
    m = RE_SET_WRAP.match(expr)
    if m:
        return _check_type_expr(m.group(1), known)
    if expr.lower() in BUILTIN_TYPES or expr in known:
        return None
    return f"类型「{expr}」未声明（非内置类型、非库内概念、非本文 concept 声明）"


def _capped(issues: list[dict]) -> tuple[list[dict], int]:
    """(截断后的列表, 总数)。"""
    return issues[:MAX_ISSUES], len(issues)


def validate_ke(db: Session, text: str, concepts_field: list | None = None) -> dict:
    """一条 KE 的完整校验。返回可 JSON 序列化的结果（也是 gold_items.validation 的缓存形态）。"""
    parsed = parse_ke(text)
    errors: list[dict] = list(parsed.errors)
    warnings: list[dict] = list(parsed.warnings)

    known_concepts: dict[str, str] = {c.name: c.status for c in db.scalars(select(Concept)).all()}
    db_ops = db.scalars(select(Operator)).all()
    subject_names = {o.subject_concept_id for o in db_ops}
    concept_ids = {c.id: c.name for c in db.scalars(select(Concept)).all()}
    known_ops: dict[str, int] = {}   # 名 → 库内 arity（同名不同主体取最小， arity 冲突另记 warning）
    for o in db_ops:
        arity = 1 + len(o.param_types or [])
        known_ops.setdefault(o.name, arity)
    local_concepts = {c["name"] for c in parsed.concepts}
    known_all = set(known_concepts) | local_concepts

    # ---- 声明级检查 ----
    seen_concept, seen_op = set(), set()
    for c in parsed.concepts:
        if c["name"] in seen_concept:
            warnings.append(_issue(c["line"], "dup_decl", f"概念「{c['name']}」重复声明"))
        seen_concept.add(c["name"])
        st = known_concepts.get(c["name"])
        if st is None:
            warnings.append(_issue(c["line"], "unknown_concept", f"概念「{c['name']}」不在本体库（待入库或待提交）"))
        elif st == "disabled":
            warnings.append(_issue(c["line"], "disabled_concept", f"概念「{c['name']}」在库中已停用"))
        if c["parent"] and c["parent"] not in known_all:
            warnings.append(_issue(c["line"], "unknown_parent", f"父类「{c['parent']}」未声明也未入库"))
    for o in parsed.ops:
        if o["name"] in seen_op:
            warnings.append(_issue(o["line"], "dup_decl", f"算子「{o['name']}」重复声明"))
        seen_op.add(o["name"])
        if not o["subject"]:
            errors.append(_issue(o["line"], "bad_op", f"算子「{o['name']}」缺少主体概念（括号第一个参数）"))
        elif o["subject"] not in known_all:
            warnings.append(_issue(o["line"], "unknown_concept",
                                   f"算子「{o['name']}」的主体概念「{o['subject']}」未声明也未入库"))
        for t in [*o["params"], o["ret"]]:
            why = _check_type_expr(t, known_all)
            if why:
                warnings.append(_issue(o["line"], "unknown_type", f"算子「{o['name']}」：{why}"))
        if o["name"] not in known_ops:
            warnings.append(_issue(o["line"], "unknown_operator", f"算子「{o['name']}」不在本体库（待入库）"))
    for c in parsed.consts:
        why = _check_type_expr(c["type"], known_all)
        if why:
            warnings.append(_issue(c["line"], "unknown_type", f"常量「{c['name']}」：{why}"))

    # 签名表：库内算子 + 本文声明（本文优先，因为可能尚未入库）
    sigs = dict(known_ops)
    for o in parsed.ops:
        sigs[o["name"]] = (1 + len(o["params"])) if o["subject"] else len(o["params"])
    local_consts = {c["name"] for c in parsed.consts}

    # ---- output 检查（至多一个，见 parse_ke） ----
    for out in parsed.outputs[:1]:
        ln = out["line"]
        bound, const_assigned = set(), set()
        for b in out["bindings"]:
            if b["kind"] == "member":
                bound.add(b["var"])
                why = _check_type_expr(b["type"], known_all)
                if why:
                    warnings.append(_issue(ln, "unknown_type", f"绑定 var {b['var']}：{why}"))
            else:  # assert：左值是表达式或常量赋值
                lm = RE_NAME_TOK.fullmatch(b["left"])
                if lm and not RE_CALL.search(b["left"]):
                    const_assigned.add(b["left"])
        # 算子调用：head + assert 两侧
        exprs = [out["head"]]
        for b in out["bindings"]:
            if b["kind"] == "assert":
                exprs += [b["left"], b["right"]]
        for expr in exprs:
            for name, argc in _extract_calls(expr):
                if name in BUILTIN_CALLS:
                    continue
                sig = sigs.get(name)
                if sig is None:
                    warnings.append(_issue(ln, "unknown_operator", f"算子「{name}」不在本体库（output 中调用）"))
                elif argc >= 0 and argc != sig:
                    errors.append(_issue(ln, "arity_mismatch",
                                         f"算子「{name}」实参 {argc} 个，签名要求 {sig} 个"))
        # 常量赋值右值：裸名字须在本文 const 声明里（本系统不存实例，不查库）
        for b in out["bindings"]:
            if b["kind"] != "assert":
                continue
            lm = RE_NAME_TOK.fullmatch(b["left"])
            if lm and not RE_CALL.search(b["left"]):
                rv = b["right"].strip()
                if RE_NAME_TOK.fullmatch(rv) and not rv.replace("_", "").isdigit() \
                        and rv.lower() not in BOOL_LITERALS and rv not in local_consts \
                        and rv not in bound:
                    warnings.append(_issue(ln, "unknown_const",
                                           f"常量「{rv}」未经本文 const 声明"))
        # 裸名字绑定检查：head 与 assert 中非调用、非字面量的名字须 ∈ 绑定 ∪ const ∪ 算子 ∪ 概念
        allowed = bound | const_assigned | local_consts | set(sigs) | known_all | BUILTIN_CALLS
        for expr in exprs:
            masked = _mask_strings(expr)
            call_spans = [m.span(1) for m in RE_CALL.finditer(masked)]
            for tok in RE_NAME_TOK.finditer(masked):
                name = tok.group(0)
                if any(s == tok.start() for s, _ in call_spans):
                    continue                    # 算子位置已查
                if name.replace("_", "").isdigit() or name.lower() in BOOL_LITERALS:
                    continue
                if name not in allowed:
                    warnings.append(_issue(ln, "unbound_name", f"名字「{name}」未绑定（非绑定变量/常量/算子/概念）"))

    # ---- 引用概念字段（gold_items.concepts） ----
    for name in (concepts_field or []):
        if name not in known_concepts and not str(name).startswith("TMP-"):
            warnings.append(_issue(0, "unknown_ref", f"引用概念「{name}」不在本体库"))

    err_list, err_total = _capped(errors)
    warn_list, warn_total = _capped(warnings)
    return {
        "engine": ENGINE_VERSION,
        "ok": not errors,
        "errors": err_list, "error_count": err_total,
        "warnings": warn_list, "warning_count": warn_total,
        "stats": {"concepts": len(parsed.concepts), "operators": len(parsed.ops),
                  "consts": len(parsed.consts), "outputs": len(parsed.outputs)},
        "validated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------- 条目级封装（缓存 + 定稿门槛） ----------------

def validate_item(db: Session, it) -> dict:
    """校验并写回 gold_items.validation 缓存（不 commit，由调用方决定事务边界）。"""
    result = validate_ke(db, it.ke_text or "", concepts_field=it.concepts or [])
    it.validation = result
    return result


def validate_all(db: Session, *, only_stale: bool = True) -> dict:
    """批量重校。only_stale=True 只跑无缓存或引擎版本旧的条目。返回汇总。"""
    from ..models import GoldItem
    items = db.scalars(select(GoldItem)).all()
    summary = {"total": len(items), "checked": 0, "skipped": 0, "ok": 0, "warn": 0, "error": 0}
    for it in items:
        cached = it.validation or {}
        if only_stale and cached.get("engine") == ENGINE_VERSION:
            summary["skipped"] += 1
            continue
        res = validate_item(db, it)
        summary["checked"] += 1
        if not res["ok"]:
            summary["error"] += 1
        elif res["warning_count"]:
            summary["warn"] += 1
        else:
            summary["ok"] += 1
    db.commit()
    return summary
