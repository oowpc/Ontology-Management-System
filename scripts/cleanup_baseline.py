"""整理本体基线：删除遗留测试概念，并给 CO v0.8 业务基线打上明确来源。

用法：
    python scripts/cleanup_baseline.py            # dry-run
    python scripts/cleanup_baseline.py --apply    # 实际写库（先自行备份）

规则：
- 精确删除 `测试_流程验证_1020` 及其测试 submission/TMP/backfill/父类链接；
- 保留 FIBO/CMNS 导入；
- 将 `DEMO_CONCEPTS` 标记为 `domain=业务基线`、`ontology_module=CO-v0.8`、
  `source_uri=internal:co-v0.8`；
- 将 `DEMO_OPERATORS` 标记为同源；
- 所有变更写 audit（维护事件 + 字段 before/after）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import log_change
from app.db import SessionLocal
from app.models import (BackfillRecord, Concept, ConceptParent, Operator,
                        QuestionConcept, Submission, TmpMapping, UsageStat)
from app.seed import DEMO_CONCEPTS, DEMO_OPERATORS

TEST_NAMES = {"测试_流程验证_1020"}
BASELINE_DOMAIN = "业务基线"
BASELINE_MODULE = "CO-v0.8"
BASELINE_SOURCE = "internal:co-v0.8"


def _tag_concept(db: Session, c: Concept, actor: str) -> bool:
    changes = []
    if c.domain != BASELINE_DOMAIN:
        changes.append(("domain", c.domain, BASELINE_DOMAIN))
        c.domain = BASELINE_DOMAIN
    if c.ontology_module != BASELINE_MODULE:
        changes.append(("ontology_module", c.ontology_module, BASELINE_MODULE))
        c.ontology_module = BASELINE_MODULE
    if (c.source_uri or "") != BASELINE_SOURCE:
        changes.append(("source_uri", c.source_uri, BASELINE_SOURCE))
        c.source_uri = BASELINE_SOURCE
    if changes:
        before = {"concept": c.name, "changes": {k: old for k, old, _ in changes}}
        after = {"concept": c.name, "changes": {k: new for k, _, new in changes}}
        log_change(db, "concept", c.id, "baseline_tag", before, after, actor)
        return True
    return False


def _tag_operator(db: Session, o: Operator, actor: str) -> bool:
    changes = []
    if o.ontology_module != BASELINE_MODULE:
        changes.append(("ontology_module", o.ontology_module, BASELINE_MODULE))
        o.ontology_module = BASELINE_MODULE
    if (o.source_uri or "") != BASELINE_SOURCE:
        changes.append(("source_uri", o.source_uri, BASELINE_SOURCE))
        o.source_uri = BASELINE_SOURCE
    if changes:
        before = {"operator": o.name, "changes": {k: old for k, old, _ in changes}}
        after = {"operator": o.name, "changes": {k: new for k, _, new in changes}}
        log_change(db, "operator", o.id, "baseline_tag", before, after, actor)
        return True
    return False


def _remove_test(db: Session, actor: str) -> dict:
    concepts = db.scalars(select(Concept).where(Concept.name.in_(TEST_NAMES))).all()
    if not concepts:
        return {"test_concepts": 0}
    ids = [c.id for c in concepts]
    ops = db.scalars(select(Operator).where(
        (Operator.subject_concept_id.in_(ids)) | (Operator.owner_concept_id.in_(ids)))).all()
    if ops:
        raise SystemExit(f"拒绝删除：测试概念仍被 {len(ops)} 个算子引用")
    removed = {}
    for model, label, cond in (
        (BackfillRecord, "backfill_records", BackfillRecord.concept_id.in_(ids)),
        (TmpMapping, "tmp_mappings", TmpMapping.concept_id.in_(ids)),
        (Submission, "submissions", Submission.concept_id.in_(ids)),
        (QuestionConcept, "question_concepts", QuestionConcept.concept_id.in_(ids)),
        (UsageStat, "usage_stats", UsageStat.concept_id.in_(ids)),
        (ConceptParent, "concept_parents",
         (ConceptParent.concept_id.in_(ids)) | (ConceptParent.parent_id.in_(ids))),
    ):
        rows = db.scalars(select(model).where(cond)).all()
        for row in rows:
            db.delete(row)
        removed[label] = len(rows)
    before = {"concepts": [{"id": c.id, "name": c.name} for c in concepts]}
    for c in concepts:
        db.delete(c)
    removed["concepts"] = len(concepts)
    log_change(db, "maintenance", 0, "cleanup_test_data", before, removed, actor)
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写库；默认 dry-run")
    ap.add_argument("--actor", default="admin")
    ap.add_argument("--library", default="co-baseline", help="本体库 key")
    args = ap.parse_args()
    from app.libraries import get_library
    from app.library_context import library_scope
    probe = SessionLocal()
    try:
        lib = get_library(probe, args.library)
    finally:
        probe.close()
    if not lib:
        print(f"本体库不存在：{args.library}")
        return 1
    scope = library_scope(lib.id)
    scope.__enter__()
    db = SessionLocal()
    try:
        print("test concepts:", [c.name for c in db.scalars(
            select(Concept).where(Concept.name.in_(TEST_NAMES))).all()])
        print("baseline concepts:", len(DEMO_CONCEPTS), "baseline operators:", len(DEMO_OPERATORS))
        if not args.apply:
            print("dry-run：加 --apply 才写库")
            return 0
        removed = _remove_test(db, args.actor)
        tagged_c = tagged_o = 0
        for name, *_ in DEMO_CONCEPTS:
            c = db.scalar(select(Concept).where(Concept.name == name))
            if c and _tag_concept(db, c, args.actor):
                tagged_c += 1
        for name, subject, *_ in DEMO_OPERATORS:
            c = db.scalar(select(Concept).where(Concept.name == subject))
            if not c:
                continue
            o = db.scalar(select(Operator).where(
                Operator.name == name, Operator.subject_concept_id == c.id))
            if o and _tag_operator(db, o, args.actor):
                tagged_o += 1
        db.commit()
        print("removed:", removed)
        print("tagged concepts:", tagged_c, "tagged operators:", tagged_o)
        print("counts:", db.scalar(select(func.count()).select_from(Concept)),
              db.scalar(select(func.count()).select_from(Operator)))
        return 0
    finally:
        db.close()
        scope.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())
