"""NL2KE BYOS 集成：把 OMS Archer 库按数据库导出，调用 NL2KE /v1/ke。"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import urllib.error
import urllib.request

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Concept, OntologySource, Operator

ARCHER_SRC = Path(os.environ.get("NL2KE_ARCHER_SRC", "/home/oowpc/NL2KE/Archer"))
QUESTION_DIRS = (ARCHER_SRC, Path("/home/oowpc/NL2SQL/data/archer"))
BASE_URL = os.environ.get("NL2KE_BASE_URL", "http://127.0.0.1:18004").rstrip("/")
TOKEN = os.environ.get("NL2KE_TOKEN", "").strip()
AGENT = os.environ.get("NL2KE_AGENT", "qwen3_coder_plus").strip()
TIMEOUT = float(os.environ.get("NL2KE_TIMEOUT", "300"))


def _load(src: Path | None = None) -> tuple[dict, list[dict]]:
    root = src or ARCHER_SRC
    so = json.loads((root / "super_ontology.json").read_text(encoding="utf-8"))
    tables = json.loads((root / "tables.json").read_text(encoding="utf-8"))
    return so, tables


def table_entry(db: Session | None, db_id: str, src: Path | None = None) -> dict:
    if db is not None:
        row = db.scalar(select(OntologySource).where(OntologySource.key == db_id))
        if row:
            return row.schema_payload
    _so, tables = _load(src)
    for t in tables:
        if t.get("db_id") == db_id:
            return t
    raise KeyError(f"Archer 数据库不存在：{db_id}")


def questions(db_id: str, limit: int = 100, src: Path | None = None) -> list[dict]:
    roots = (src,) if src else QUESTION_DIRS
    out = []
    seen = set()
    for root in roots:
        for fname, dataset in (("en_dev.json", "en_dev"), ("zh_dev.json", "zh_dev"),
                               ("train_ke_sql_enall.json", "train")):
            p = root / fname
            if not p.exists():
                continue
            try:
                rows = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            for i, row in enumerate(rows):
                if str(row.get("db_id")) != db_id:
                    continue
                q = str(row.get("question") or "").strip()
                key = (dataset, q)
                if q and key not in seen:
                    seen.add(key)
                    out.append({"id": f"{dataset}-{i+1}", "dataset": dataset,
                                "db_id": db_id, "question": q})
                    if len(out) >= limit:
                        return out
    return out


def catalog(db: Session | None = None, src: Path | None = None) -> list[dict]:
    if db is not None:
        rows = db.scalars(select(OntologySource).order_by(OntologySource.key)).all()
        if rows:
            out = []
            for r in rows:
                p = Path(r.sqlite_path) if r.sqlite_path else None
                out.append({"db_id": r.key, "has_sqlite": bool(p and p.exists()),
                            "size": p.stat().st_size if p and p.exists() else 0})
            return out
    try:
        _so, tables = _load(src)
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    return [{"db_id": t["db_id"], "has_sqlite": False, "size": 0} for t in tables]


def databases(db: Session | None = None, src: Path | None = None) -> list[str]:
    if db is not None:
        rows = db.scalars(select(OntologySource.key).order_by(OntologySource.key)).all()
        if rows:
            return list(rows)
    try:
        _so, tables = _load(src)
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    return sorted(str(t["db_id"]) for t in tables)


def _from_payloads(db: Session, db_id: str) -> tuple[dict, dict]:
    """从 OMS source_payload 组装；OMS 是 Archer 结构的主数据源。"""
    table = table_entry(db, db_id)
    concepts = {}
    for c in db.scalars(select(Concept).where(Concept.source_payload.is_not(None))).all():
        raw = c.source_payload or {}
        sources = raw.get("sources") or {}
        if db_id not in sources:
            continue
        item = copy.deepcopy(raw)
        item["sources"] = {db_id: sources[db_id]}
        concepts[c.name] = item
    operators = {}
    for o in db.scalars(select(Operator).where(Operator.source_payload.is_not(None))).all():
        raw = o.source_payload or {}
        if db_id not in (raw.get("databases") or []):
            continue
        if raw.get("type") == "PROPERTY":
            if raw.get("concept") not in concepts:
                continue
        elif raw.get("type") == "LINK":
            sig = raw.get("signature") or []
            if not sig or any(x not in concepts for x in sig):
                continue
        else:
            continue
        operators[o.name] = copy.deepcopy(raw)
    return table, {"concepts": concepts, "operators": operators}


def sub_ontology(db_id: str, db: Session | None = None,
                 src: Path | None = None) -> tuple[dict, dict]:
    """优先读 OMS 的 source_payload/schema；没有时回退源 JSON。"""
    if db is not None:
        if db.scalar(select(OntologySource.id).where(OntologySource.key == db_id)) is not None:
            return _from_payloads(db, db_id)
    so, tables = _load(src)
    table = next((t for t in tables if t.get("db_id") == db_id), None)
    if table is None:
        raise KeyError(f"Archer 数据库不存在：{db_id}")
    concepts = {}
    for name, c in (so.get("concepts") or {}).items():
        sources = (c.get("sources") or {})
        if db_id not in sources:
            continue
        item = copy.deepcopy(c)
        item["sources"] = {db_id: sources[db_id]}
        concepts[str(name)] = item
    operators = {}
    for name, op in (so.get("operators") or {}).items():
        if db_id not in (op.get("databases") or []):
            continue
        if op.get("type") == "PROPERTY":
            if op.get("concept") not in concepts:
                continue
        elif op.get("type") == "LINK":
            sig = op.get("signature") or []
            if not sig or any(x not in concepts for x in sig):
                continue
        else:
            continue
        operators[str(name)] = copy.deepcopy(op)
    return table, {"concepts": concepts, "operators": operators}


PARAM_KEYS = ("max_retries", "use_fc", "temperature", "refine_ontology",
              "verify_ontology", "verify_semantic", "fewshot", "fewshot_k",
              "reasoning_effort")


def run(db: Session | None, db_id: str, question: str, *, evidence: str = "",
        verify: bool = True, params: dict | None = None,
        src: Path | None = None) -> dict:
    if not TOKEN:
        raise RuntimeError("未配置 NL2KE_TOKEN")
    table, ontology = sub_ontology(db_id, db, src)
    body = {
        "nl_query": question,
        "db_id": db_id,
        "tables_json": table,
        "ontology": ontology,
        "verify": bool((params or {}).get("verify", verify)),
        "evidence": evidence or None,
        "agent": (params or {}).get("agent") or AGENT or None,
        "debug": True,
    }
    for key in PARAM_KEYS:
        value = (params or {}).get(key)
        if value is not None and value != "":
            body[key] = value
    req = urllib.request.Request(
        BASE_URL + "/v1/ke",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {TOKEN}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            data["request_meta"] = {
                "db_id": db_id,
                "table_names": table.get("table_names_original") or [],
                "table_count": len(table.get("table_names_original") or []),
                "concept_count": len(ontology.get("concepts") or {}),
                "operator_count": len(ontology.get("operators") or {}),
                "params": {k: body.get(k) for k in ("verify", "agent", *PARAM_KEYS)},
            }
            return data
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        raise RuntimeError(f"NL2KE {e.code}: {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"NL2KE 请求失败：{e}") from e
