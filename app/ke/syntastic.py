"""Syntastic_and_Lexical-Verifier 适配层。

外部仓库通过 KE_FORM_VALIDATOR_PATH 指定；不可用时返回 None，由 OMS 内部校验器降级。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType

_DEFAULT = "/home/oowpc/Syntastic_and_Lexical-Verifier"
_MODULE: ModuleType | None = None
_FAILED = False


def _load() -> ModuleType | None:
    global _MODULE, _FAILED
    if _MODULE is not None or _FAILED:
        return _MODULE
    root = Path(os.environ.get("KE_FORM_VALIDATOR_PATH", _DEFAULT))
    src = root / "src"
    if not (src / "keval" / "form_validator").is_dir():
        _FAILED = True
        return None
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        from keval.form_validator import validate
        import keval.form_validator as module
        _MODULE = module
        # 保留 validate 入口，避免模块属性被后续 import 覆盖
        _MODULE.validate = validate
        return _MODULE
    except Exception:  # noqa: BLE001
        _FAILED = True
        return None


def ontology(db) -> dict:
    """把 OMS 本体映射成 Syntastic 需要的 concepts/operators。"""
    from sqlalchemy import select
    from ..models import Concept, Operator
    concept_rows = db.scalars(select(Concept)).all()
    concepts = {c.name: {"sources": {}} for c in concept_rows}
    names = {c.id: c.name for c in concept_rows}
    operators = {}
    for o in db.scalars(select(Operator)).all():
        subject = names.get(o.subject_concept_id)
        spec = {"type": "PROPERTY"}
        if subject:
            spec["concept"] = subject
        if o.param_types:
            spec["signature"] = ([subject] if subject else []) + list(o.param_types or [])
        operators[o.name] = spec
    return {"concepts": concepts, "operators": operators}


def validate_ke(db, text: str) -> dict | None:
    module = _load()
    if module is None:
        return None
    try:
        result = module.validate(
            text or "", ontology(db), strip_prose=True,
            block_grammar_gaps=False, block_prose=False,
        )
        errors, warnings = [], []
        for f in result.findings:
            pos = getattr(f, "position", None)
            item = {
                "line": pos[0] if pos else 0,
                "kind": getattr(getattr(f, "code", None), "value", str(getattr(f, "code", ""))),
                "message": getattr(f, "message", ""),
            }
            (warnings if result._is_warn(f) else errors).append(item)
        return {
            "passed": result.passed, "complete": result.complete,
            "degraded": result.degraded, "layer_status": result.layer_status(),
            "errors": errors, "warnings": warnings,
        }
    except Exception:  # noqa: BLE001
        return None
