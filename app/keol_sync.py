"""KEOL 本体源与 OMS 目标库的持续同步检查（不自动审批）。"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Concept, KeolSyncRun, OntologyRule, Operator

DEFAULT_SOURCE = "/home/oowpc/KEOL/console/data/ontology.json"


def source_path() -> Path:
    return Path(os.environ.get("KEOL_ONTOLOGY_PATH", settings.keol_ontology_path or DEFAULT_SOURCE))


def load_source() -> tuple[dict, str]:
    path = source_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = json.dumps({
        "concepts": data.get("concepts") or [],
        "operators": data.get("operators") or [],
        "rules": data.get("rules") or [],
    }, ensure_ascii=False, sort_keys=True, default=str)
    return data, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _name(row: dict) -> str:
    return str(row.get("canonical_name") or row.get("name") or "").strip()


def check(db: Session) -> dict:
    data, digest = load_source()
    concepts = {_name(c) for c in data.get("concepts") or [] if _name(c)}
    concept_names = {str(c.get("id")): _name(c) for c in data.get("concepts") or []}
    ops = {}
    for op in data.get("operators") or []:
        name = _name(op)
        inputs = op.get("inputs") or op.get("input_concepts") or []
        subject = ""
        if isinstance(inputs, list) and inputs:
            first = inputs[0]
            if isinstance(first, dict):
                subject = str(first.get("canonical_name") or first.get("name") or "")
            else:
                subject = concept_names.get(str(first), str(first))
        ops[(name, subject)] = op
    rules = {_name(r): r for r in data.get("rules") or [] if _name(r)}

    db_concepts = {c.name: c for c in db.scalars(select(Concept)).all()}
    rows_ops = db.scalars(select(Operator)).all()
    db_ops = {(o.name, db.get(Concept, o.subject_concept_id).name if db.get(Concept, o.subject_concept_id) else ""): o for o in rows_ops}
    db_rules = {r.name: r for r in db.scalars(select(OntologyRule)).all()}
    added_c = sorted(concepts - set(db_concepts))
    removed_c = sorted(set(db_concepts) - concepts)
    added_o = sorted(k for k in ops if k not in db_ops)
    removed_o = sorted(k for k in db_ops if k not in ops)
    added_r = sorted(set(rules) - set(db_rules))
    removed_r = sorted(set(db_rules) - set(rules))
    changed_r = sorted(name for name in set(rules) & set(db_rules)
                       if (rules[name].get("description") or "") != (db_rules[name].description or ""))
    result = {
        "source": str(source_path()), "source_hash": digest,
        "source_counts": {"concepts": len(concepts), "operators": len(ops), "rules": len(rules)},
        "target_counts": {"concepts": len(db_concepts), "operators": len(db_ops), "rules": len(db_rules)},
        "diff": {
            "concepts": {"added": added_c[:100], "removed": removed_c[:100],
                         "added_count": len(added_c), "removed_count": len(removed_c)},
            "operators": {"added": [list(x) for x in added_o[:100]],
                          "removed": [list(x) for x in removed_o[:100]],
                          "added_count": len(added_o), "removed_count": len(removed_o)},
            "rules": {"added": added_r[:100], "removed": removed_r[:100],
                      "changed": changed_r[:100], "added_count": len(added_r),
                      "removed_count": len(removed_r), "changed_count": len(changed_r)},
        },
    }
    result["changed"] = any(result["diff"][k][f"{x}_count"] for k in
                            ("concepts", "operators", "rules")
                            for x in ("added", "removed", "changed")
                            if f"{x}_count" in result["diff"][k])
    return result


def record(db: Session, data: dict, actor: str, status: str = "checked") -> KeolSyncRun:
    run = KeolSyncRun(source_path=data["source"], source_hash=data["source_hash"],
                      status=status, counts={"source": data["source_counts"],
                                             "target": data["target_counts"]},
                      diff=data["diff"], error="", created_by=actor)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def latest(db: Session) -> KeolSyncRun | None:
    return db.scalar(select(KeolSyncRun).order_by(KeolSyncRun.id.desc()).limit(1))
