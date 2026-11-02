"""只读 LLM 审核：对标注批次当前 CO 对齐结果给出逐行保留/删除建议。

用法：
  python scripts/audit_alignment_llm.py --batch-id 1 --out docs/pilots/v1-llm-audit.json

不写数据库。密钥只从环境变量或 /home/oowpc/.keol-console.env 读取；
输出 JSON + Markdown，供人工确认后再 apply。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ontology.db"
ENV_FILE = Path("/home/oowpc/.keol-console.env")
BUILTINS = {"String", "Number", "Bool", "Date", "TimeInterval", "Real", "Name", "Concept"}
SYSTEM = """你是政府政策本体对齐审核员。输入是政策原文的标注单元、A/B 两份 CO 结果和当前对齐后的概念/算子候选。只审核给定候选项，不要新增候选项，不要改写原文。

角色定义：
- normative：可直接执行/判定的规范条款；
- context：背景、目标、总体要求、原则、宣传性表述；
- structural：章节标题；
- metadata：来源、日期、文号、编辑信息；
- noise：导航、页脚、打印/分享等网页噪声。

审核规则：
1. 概念必须能在原文中指称具体对象、角色、状态、文档、机构、时间、金额等；动作、口号、目标、原则、政策效果不作为概念。
2. 算子必须是可执行的关系/函数；subject 必须是概念；return_type 必须是已有概念名或内置类型（String/Number/Bool/Date/TimeInterval/Real/Name/Concept）。不满足则 drop。
3. 背景、总体要求、原则、宣传段落里的概念/算子原则上全部 drop；确实可独立判定的除外，但要说明。
4. 每个给定候选都必须给出一条 decision：keep 或 drop；rename 通过 new_name 给出（仅概念，可选）；不得遗漏。
5. evidence 必须引用原文短语；原文没有依据时 confidence 要低，并说明。
6. 只输出 JSON 对象，不要输出 Markdown、解释或代码围栏。

输出格式：
{"unit_role":"normative|context|structural|metadata|noise",
 "concept_decisions":[{"name":"原文候选名","decision":"keep|drop","new_name":null,"reason":"不超过40字","evidence":"原文短语","confidence":0.0}],
 "operator_decisions":[{"name":"候选算子名","subject":"候选主体","return_type":"候选返回类型","decision":"keep|drop","new_name":null,"new_subject":null,"new_return_type":null,"reason":"不超过40字","evidence":"原文短语","confidence":0.0}],
 "notes":"不超过80字"}"""


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in {"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"} and not os.environ.get(k):
            os.environ[k] = v


def llm_call(system: str, user: str) -> dict:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    last = None
    for attempt in range(3):
        try:
            r = httpx.post(
                base + "/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ], "temperature": 0, "max_tokens": 8192,
                    "response_format": {"type": "json_object"}},
                timeout=300,
            )
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            text = r.json()["choices"][0]["message"]["content"].strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:]
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("LLM 未返回 JSON 对象")
            return json.loads(text[start:end + 1])
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(str(last))


def read_data(db_path: Path, batch_id: int):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    batch = con.execute("SELECT * FROM annotation_batches WHERE id=?", (batch_id,)).fetchone()
    if not batch:
        raise SystemExit(f"批次不存在：{batch_id}")
    units = con.execute(
        "SELECT * FROM annotation_units WHERE batch_id=? ORDER BY id", (batch_id,)).fetchall()
    aligns = {r["unit_id"]: dict(r) for r in con.execute(
        "SELECT * FROM co_alignments WHERE batch_id=? AND is_current=1 ORDER BY unit_id",
        (batch_id,)).fetchall()}
    cos = {(r["unit_id"], r["side"]): dict(r) for r in con.execute(
        "SELECT * FROM co_annotations WHERE batch_id=? AND is_current=1", (batch_id,)).fetchall()}
    business = [r["name"] for r in con.execute(
        "SELECT name FROM concepts WHERE library_id=2 ORDER BY name").fetchall()]
    pilot = [r["name"] for r in con.execute(
        "SELECT name FROM concepts WHERE library_id=3 ORDER BY name").fetchall()]
    con.close()
    return batch, units, aligns, cos, business, pilot


def _json(v, default):
    if v is None or v == "":
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default


def _concept_rows(aligned: dict) -> list[dict]:
    rows = aligned.get("concept_rows")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict) and str(r.get("name") or "").strip()]
    return [{"name": n} for n in (aligned.get("concepts") or []) if str(n).strip()]


def _operator_rows(aligned: dict) -> list[dict]:
    rows = aligned.get("operator_rows")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict) and str(r.get("name") or "").strip()]
    out = []
    for line in aligned.get("operators") or []:
        s = str(line).strip()
        if "(" not in s or ")" not in s or "=" not in s:
            continue
        name_sub, ret = s.split("=", 1)
        name, sub = name_sub.split("(", 1)
        out.append({"name": name.strip(), "subject": sub.rstrip(")").strip(),
                    "return_type": ret.strip()})
    return out


def audit_unit(unit, alignment, co_a, co_b, business_names, pilot_names) -> dict:
    aligned = _json(alignment.get("aligned"), {})
    concepts = _concept_rows(aligned)
    ops = _operator_rows(aligned)
    co_a = _json(co_a.get("concepts") if co_a else None, [])
    co_b = _json(co_b.get("concepts") if co_b else None, [])
    payload = {
        "unit_id": unit["unit_id"],
        "source_id": unit["source_id"],
        "heading": (unit["source_locator"] if unit["source_locator"] else ""),
        "raw_text": unit["raw_text"],
        "co_a_concepts": co_a,
        "co_b_concepts": co_b,
        "aligned_concepts": [{"name": str(r.get("name")), "definition": r.get("definition") or "",
                              "aliases": r.get("aliases") or []} for r in concepts],
        "aligned_operators": [{"name": str(r.get("name")), "subject": r.get("subject") or "",
                               "return_type": r.get("return_type") or ""} for r in ops],
        "target_library": "pilot-yuer",
        "business_baseline_names": business_names,
        "pilot_existing_names": pilot_names,
        "reminder": "只可评估 aligned_concepts/aligned_operators，不得新增；每个候选必须给 decision。",
    }
    user = "请审核以下单元：\n" + json.dumps(payload, ensure_ascii=False)
    return llm_call(SYSTEM, user)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--batch-id", type=int, default=1)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/pilots/PILOT-20261028-01-llm-audit.json")
    ap.add_argument("--md", type=Path, default=ROOT / "docs/pilots/PILOT-20261028-01-llm-audit.md")
    ap.add_argument("--max-units", type=int, default=0)
    ap.add_argument("--from-json", type=Path, default=None,
                    help="复用已有原始 LLM JSON，只做确定性后处理")
    args = ap.parse_args()
    batch, units, aligns, cos, business, pilot = read_data(args.db, args.batch_id)
    if args.from_json:
        report = json.loads(args.from_json.read_text(encoding="utf-8"))
        report = postprocess(report)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        write_markdown(report, args.md, business, pilot)
        write_filtered(report, args.out.with_name(args.out.stem.replace("-audit", "-filtered") + ".json"))
        print(f"postprocessed JSON: {args.out}")
        print(f"filtered JSON: {args.out.with_name(args.out.stem.replace('-audit', '-filtered') + '.json')}")
        print(f"MD: {args.md}")
        return 0
    load_env()
    results = []
    if args.max_units:
        units = units[:args.max_units]
    print(f"batch={batch['batch_id']} units={len(units)}")
    for n, unit in enumerate(units, 1):
        uid = unit["unit_id"]
        al = aligns.get(unit["id"])
        if not al:
            print(f"[{n}/{len(units)}] {uid} 无对齐，跳过")
            results.append({"unit_id": uid, "error": "无当前对齐", "raw_text": unit["raw_text"]})
            continue
        print(f"[{n}/{len(units)}] {uid} ...", flush=True)
        try:
            data = audit_unit(unit, al, cos.get((unit["id"], "A")), cos.get((unit["id"], "B")),
                              business, pilot)
            data["unit_id"] = uid
            data["raw_text"] = unit["raw_text"]
            data["aligned"] = _json(al.get("aligned"), {})
            data["concept_rows"] = _concept_rows(data["aligned"])
            data["operator_rows"] = _operator_rows(data["aligned"])
            results.append(data)
        except Exception as e:  # noqa: BLE001
            print(f"    ERROR {e}")
            results.append({"unit_id": uid, "error": str(e), "raw_text": unit["raw_text"],
                            "aligned": _json(al.get("aligned"), {})})

    report = {"batch": {"id": batch["id"], "batch_id": batch["batch_id"],
                        "library_id": batch["library_id"], "state": batch["state"]},
              "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "units": results}
    report = postprocess(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    filtered_path = args.out.with_name(args.out.stem.replace("-audit", "-filtered") + ".json")
    write_filtered(report, filtered_path)
    write_markdown(report, args.md, business, pilot)
    print(f"JSON: {args.out}\nFiltered: {filtered_path}\nMD:   {args.md}")
    return 0


def _decision(row: dict, name_field: str = "name") -> str:
    return str(row.get("decision") or "").strip()


def postprocess(report: dict) -> dict:
    """确定性复核 LLM 输出：不信任越界/自造项，按上下文和返回类型收口。

    两遍处理：先收齐所有单元的有效概念，再校验算子主体/返回类型，
    避免跨单元引用因顺序被误删。
    """
    units = report.get("units") or []
    kept_c: dict[str, dict] = {}
    concept_conflicts: list[str] = []
    invented: list[str] = []
    forced_context: list[str] = []
    unreviewed: list[str] = []

    # 第一遍：概念
    for u in units:
        if u.get("error"):
            continue
        rows_c = u.get("concept_rows") or []
        input_c = {str(r.get("name")) for r in rows_c if r.get("name")}
        cdec = {str(x.get("name")): x for x in (u.get("concept_decisions") or []) if x.get("name")}
        for name in cdec:
            if name not in input_c:
                invented.append(f"{u['unit_id']}:概念 {name}")
        context = str(u.get("unit_role") or "") == "context"
        if context:
            u["notes"] = ("context 单元：按策略强制排除全部概念/算子。" + str(u.get("notes") or "")).strip()
        u["concept_review"] = []
        for row in rows_c:
            name = str(row.get("name"))
            d = cdec.get(name)
            if d is None:
                u["concept_review"].append({**row, "validated_decision": "unreviewed",
                                            "validated_reason": "LLM 未返回该候选"})
                unreviewed.append(f"{u['unit_id']}:概念 {name}")
                continue
            decision = _decision(d)
            reason = str(d.get("reason") or "")
            if context and decision != "drop":
                decision = "drop"
                reason = "context 单元强制排除；" + reason
                forced_context.append(f"{u['unit_id']}:概念 {name}")
            if decision not in {"keep", "drop"}:
                decision = "unreviewed"
                reason = "非法 decision；" + reason
                unreviewed.append(f"{u['unit_id']}:概念 {name}")
            new_name = str(d.get("new_name") or "").strip() or None
            final_name = new_name or name
            u["concept_review"].append({**row, "final_name": final_name,
                                        "validated_decision": decision,
                                        "validated_reason": reason,
                                        "confidence": d.get("confidence")})
            if decision == "keep":
                if final_name in kept_c:
                    concept_conflicts.append(
                        f"{final_name}（{u['unit_id']} 与 {kept_c[final_name]['unit_id']}）")
                else:
                    kept_c[final_name] = {**row, "name": final_name, "unit_id": u["unit_id"]}

    # 第二遍：算子（此时 kept_c 已完整）
    for u in units:
        if u.get("error"):
            continue
        rows_o = u.get("operator_rows") or []
        input_o = {(str(r.get("name")), str(r.get("subject") or ""))
                   for r in rows_o if r.get("name")}
        odec = {(str(x.get("name")), str(x.get("subject") or "")): x
                for x in (u.get("operator_decisions") or []) if x.get("name")}
        for key in odec:
            if key not in input_o:
                invented.append(f"{u['unit_id']}:算子 {key[0]}({key[1]})")
        context = str(u.get("unit_role") or "") == "context"
        u["operator_review"] = []
        for row in rows_o:
            key = (str(row.get("name")), str(row.get("subject") or ""))
            d = odec.get(key)
            if d is None:
                u["operator_review"].append({**row, "validated_decision": "unreviewed",
                                             "validated_reason": "LLM 未返回该候选"})
                unreviewed.append(f"{u['unit_id']}:算子 {key[0]}({key[1]})")
                continue
            decision = _decision(d)
            reason = str(d.get("reason") or "")
            if context and decision != "drop":
                decision = "drop"
                reason = "context 单元强制排除；" + reason
                forced_context.append(f"{u['unit_id']}:算子 {key[0]}")
            new_name = str(d.get("new_name") or "").strip() or str(row.get("name"))
            new_subject = str(d.get("new_subject") or "").strip() or str(row.get("subject") or "")
            new_return = str(d.get("new_return_type") or "").strip() or str(row.get("return_type") or "")
            if decision == "keep" and new_subject not in kept_c:
                decision = "drop"
                reason = f"主体「{new_subject}」未保留；" + reason
            if decision == "keep" and new_return not in kept_c and new_return not in BUILTINS:
                decision = "drop"
                reason = f"返回类型「{new_return}」未保留且非内置；" + reason
            if decision not in {"keep", "drop"}:
                decision = "unreviewed"
                reason = "非法 decision；" + reason
                unreviewed.append(f"{u['unit_id']}:算子 {key[0]}")
            u["operator_review"].append({**row, "final_name": new_name,
                                         "final_subject": new_subject,
                                         "final_return_type": new_return,
                                         "validated_decision": decision,
                                         "validated_reason": reason,
                                         "confidence": d.get("confidence")})

    kept_ops: dict[tuple, dict] = {}
    for u in units:
        for row in u.get("operator_review") or []:
            if row.get("validated_decision") == "keep":
                k = (row.get("final_name"), row.get("final_subject"), row.get("final_return_type"))
                kept_ops.setdefault(k, {
                    "name": row.get("final_name"),
                    "subject": row.get("final_subject"),
                    "return_type": row.get("final_return_type"),
                    "unit_id": u["unit_id"],
                })
    kept_units = []
    for u in units:
        if u.get("error"):
            continue
        kept_units.append({
            "unit_id": u["unit_id"],
            "unit_role": u.get("unit_role"),
            "concepts": [r["final_name"] for r in u.get("concept_review") or []
                         if r.get("validated_decision") == "keep"],
            "operators": [{"name": r.get("final_name"), "subject": r.get("final_subject"),
                           "return_type": r.get("final_return_type")}
                          for r in u.get("operator_review") or []
                          if r.get("validated_decision") == "keep"],
        })
    review = {
        "kept_unique_concepts": len(kept_c),
        "kept_unique_operators": len(kept_ops),
        "forced_context": forced_context,
        "unreviewed": unreviewed,
        "invented": invented,
        "duplicate_kept_names": concept_conflicts,
    }
    report["review_summary"] = review
    report["filtered"] = {
        "source_batch": report.get("batch"),
        "target_library": "pilot-yuer",
        "note": "LLM+确定性规则候选；未 apply，必须人工确认后才可写库",
        "concepts": list(kept_c.values()),
        "operators": list(kept_ops.values()),
        "units": kept_units,
    }
    return report


def write_filtered(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.get("filtered") or {}, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def write_markdown(report: dict, path: Path, business: list[str], pilot: list[str]) -> None:
    lines = [f"# LLM 对齐审核 · {report['batch']['batch_id']}", "",
             f"> 生成时间：{report['generated_at']}  ",
             f"> 目标库：pilot-yuer  ",
             f"> 只读审核，不写数据库；apply 前必须人工确认。", ""]
    summary = report.get("review_summary") or {}
    total_c = total_o = valid_c = valid_o = err = 0
    for u in report["units"]:
        if u.get("error"):
            lines += [f"## {u['unit_id']} · ERROR", "", u["error"], ""]
            err += 1
            continue
        role = u.get("unit_role", "?")
        crows = u.get("concept_review") or []
        orows = u.get("operator_review") or []
        kc = [x for x in crows if x.get("validated_decision") == "keep"]
        ko = [x for x in orows if x.get("validated_decision") == "keep"]
        total_c += len(crows); total_o += len(orows)
        valid_c += len(kc); valid_o += len(ko)
        lines += [f"## {u['unit_id']} · {role}", "",
                  f"原文：{u.get('raw_text','')[:160]}", "",
                  f"- 概念：{len(kc)} keep / {len(crows)-len(kc)} drop；算子：{len(ko)} keep / {len(orows)-len(ko)} drop"]
        if kc:
            lines.append("- 保留概念：" + "、".join(str(x.get("final_name")) for x in kc))
        if ko:
            lines.append("- 保留算子：" + "；".join(
                f"{x.get('final_name')}({x.get('final_subject')})→{x.get('final_return_type')}" for x in ko))
        drops = [x for x in crows + orows if x.get("validated_decision") == "drop"]
        if drops:
            lines.append("- 删除理由：" + "；".join(
                f"{x.get('final_name') or x.get('name')}：{x.get('validated_reason','')[:50]}" for x in drops[:12]))
        if u.get("notes"):
            lines.append(f"- 备注：{u['notes']}")
        lines.append("")
    lines += ["## 汇总", "",
              f"- 单元：{len(report['units'])}，失败：{err}",
              f"- 概念行：{total_c}，建议保留：{valid_c}；唯一概念：{summary.get('kept_unique_concepts', 0)}",
              f"- 算子行：{total_o}，建议保留：{valid_o}；唯一算子：{summary.get('kept_unique_operators', 0)}",
              f"- context 强制排除：{len(summary.get('forced_context') or [])}",
              f"- 未审/缺失 decision：{len(summary.get('unreviewed') or [])}",
              f"- LLM 越界/自造候选：{len(summary.get('invented') or [])}",
              f"- 重复保留概念：{len(summary.get('duplicate_kept_names') or [])}",
              f"- 业务基线已有概念：{len(business)}；Pilot 已有概念：{len(pilot)}",
              "", "详细逐行 validated decision 见同名 JSON；候选见同名 `-filtered.json`。"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
