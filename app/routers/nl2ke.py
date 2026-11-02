"""OMS × NL2KE：Archer 单库 NL→KE PoC 与运行记录。"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AnnotationBatch, AnnotationMember, AnnotationUnit, GoldItem, Nl2keRun, OntologySource
from ..security import current_user
from ..templating import templates
from .. import ke2sql as sql_bridge
from .. import ke_matcher
from .. import nl2ke as bridge

router = APIRouter(tags=["nl2ke"])


class RunIn(BaseModel):
    db_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=4000)
    evidence: str = ""
    verify: bool = True
    agent: str | None = None
    reasoning_effort: str | None = None
    max_retries: int = Field(2, ge=0, le=5)
    use_fc: bool = True
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    refine_ontology: bool = True
    verify_ontology: bool = True
    verify_semantic: bool = True
    fewshot: bool = True
    fewshot_k: int = Field(5, ge=1, le=8)
    run_sql: bool = True


class BatchIn(BaseModel):
    db_id: str = Field(min_length=1, max_length=128)
    questions: list[str] = Field(min_length=1, max_length=20)
    verify: bool = True
    agent: str | None = None
    reasoning_effort: str | None = None
    max_retries: int = Field(2, ge=0, le=5)
    use_fc: bool = True
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    refine_ontology: bool = True
    verify_ontology: bool = True
    verify_semantic: bool = True
    fewshot: bool = True
    fewshot_k: int = Field(5, ge=1, le=8)
    run_sql: bool = True


class EquivalenceIn(BaseModel):
    reference_ke: str = Field(min_length=1, max_length=20000)
    request_timeout_s: float = Field(60, ge=1, le=120)


def _archer(request: Request):
    lib = getattr(request.state, "library", None)
    if lib is None or lib.key != "archer":
        raise HTTPException(400, "请先切换到 Archer Benchmark 库")
    return lib


NL2KE_TRACE_STEPS = (
    ("schema", "Schema"),
    ("meta", "问题元信息"),
    ("ontology", "本体精炼"),
    ("fewshot", "Few-shot"),
    ("attempts", "生成与校验"),
    ("prompt", "初始 Prompt"),
    ("timings", "分阶段耗时"),
)
NL2KE_STEP_DESC = {
    "schema": "数据库 DDL、表名与字段",
    "meta": "从问题中提取的元信息",
    "ontology": "精炼前后的概念与算子",
    "fewshot": "检索到的参考题目、KE 和 SQL",
    "attempts": "每次生成的 KE 与校验结果",
    "prompt": "发送给模型的初始提示词",
    "timings": "各阶段耗时拆解",
}


def _run_or_404(db: Session, lib, rid: int) -> Nl2keRun:
    rec = db.get(Nl2keRun, rid)
    if rec is None or rec.library_id != lib.id:
        raise HTTPException(404, "运行记录不存在")
    return rec


def _step_trace(trace: dict, step: str) -> dict:
    if step == "attempts":
        return trace.get("verify_loop") or {}
    return trace.get(step) or {}


def _steps(r: Nl2keRun) -> list[dict]:
    d = r.result_payload or {}
    meta = d.get("request_meta") or {}
    return [
        {"key": "schema", "label": "加载数据库 schema",
         "output": {"db_id": r.db_id, "table_count": meta.get("table_count"),
                    "table_names": meta.get("table_names")}},
        {"key": "ontology", "label": "构建 Archer 子本体",
         "output": {"concept_count": meta.get("concept_count"),
                    "operator_count": meta.get("operator_count")}},
        {"key": "request", "label": "调用 NL2KE BYOS",
         "output": {"agent": (r.params or {}).get("agent"),
                    "verify": r.verify,
                    "params": r.params or {},
                    "request_id": r.request_id,
                    "model_info": d.get("model_info")}},
        {"key": "generation", "label": "生成 KE",
         "output": {"ke": r.ke, "attempts": r.attempts,
                    "latency_ms": r.latency_ms, "usage": r.usage,
                    "timings": r.timings}},
        {"key": "validation", "label": "本体 / 语义校验",
         "output": {"verify_passed": r.verify_passed,
                    "formal_verify_passed": d.get("formal_verify_passed"),
                    "reason": d.get("reason"),
                    "reconstructed_nl": d.get("reconstructed_nl")}},
        {"key": "sql", "label": "KE → SQL / SQLite 执行",
         "output": r.sql_payload or {"skipped": True}},
        {"key": "persist", "label": "保存运行记录",
         "output": {"run_id": r.id, "status": r.status,
                    "gold_item_id": r.gold_item_id,
                    "batch_id": r.annotation_batch_id,
                    "unit_id": r.annotation_unit_id}},
    ]


def _run_sql(db: Session, db_id: str, ke: str) -> dict:
    if not (ke or "").strip():
        return {"ok": False, "error": "没有 KE，无法翻译 SQL"}
    src = db.scalar(select(OntologySource).where(OntologySource.key == db_id))
    if not src or not src.sqlite_path:
        return {"ok": False, "error": "该数据库没有注册 SQLite 文件"}
    _table, ontology = bridge.sub_ontology(db_id, db)
    return sql_bridge.run(ke, ontology, db_id, src.sqlite_path)


def _out(r: Nl2keRun, *, include_trace: bool = True) -> dict:
    payload = dict(r.result_payload or {})
    if not include_trace:
        payload.pop("trace", None)
    return {
        "id": r.id, "db_id": r.db_id, "batch_tag": r.batch_tag, "question": r.question,
        "ke": r.ke, "status": r.status, "verify": r.verify,
        "verify_passed": r.verify_passed, "attempts": r.attempts,
        "latency_ms": r.latency_ms, "request_id": r.request_id,
        "usage": r.usage, "timings": r.timings, "error": r.error,
        "gold_item_id": r.gold_item_id,
        "annotation_batch_id": r.annotation_batch_id,
        "annotation_unit_id": r.annotation_unit_id,
        "params": r.params or {},
        "result": payload,
        "trace": (payload.get("trace") or {}) if include_trace else {},
        "equivalence": payload.get("equivalence"),
        "steps": _steps(r),
        "sql_payload": r.sql_payload or {},
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat(timespec="seconds") if r.created_at else None,
    }


@router.get("/api/nl2ke/databases")
def databases(request: Request, db: Session = Depends(get_db),
              user: dict = Depends(current_user)):
    _archer(request)
    return {"items": bridge.catalog(db)}


@router.get("/api/nl2ke/questions")
def questions(request: Request, db_id: str, limit: int = 50,
              user: dict = Depends(current_user)):
    _archer(request)
    return {"items": bridge.questions(db_id, max(1, min(limit, 200)))}


@router.post("/api/nl2ke/run")
def run(body: RunIn, request: Request, db: Session = Depends(get_db),
        user: dict = Depends(current_user)):
    lib = _archer(request)
    try:
        params = body.model_dump()
        data = bridge.run(db, body.db_id, body.question,
                          evidence=body.evidence, verify=body.verify, params=params)
    except Exception as e:  # noqa: BLE001
        rec = Nl2keRun(library_id=lib.id, db_id=body.db_id, question=body.question,
                       evidence=body.evidence, status="error", verify=body.verify,
                       error=str(e)[:4000], created_by=user["username"])
        db.add(rec)
        db.commit()
        raise HTTPException(502, str(e)[:1000]) from e
    sql_payload = _run_sql(db, body.db_id, data.get("ke") or "") if body.run_sql else None
    rec = Nl2keRun(
        library_id=lib.id, db_id=body.db_id, question=body.question,
        evidence=body.evidence, ke=data.get("ke") or "",
        status="ok", verify=body.verify,
        verify_passed=bool(data.get("verify_passed")),
        attempts=int(data.get("attempts") or 1),
        latency_ms=int(data.get("latency_ms") or 0),
        request_id=str(data.get("request_id") or ""),
        usage=data.get("usage"), timings=data.get("timings"),
        params=params, result_payload=data, sql_payload=sql_payload,
        created_by=user["username"],
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return _out(rec)


@router.post("/api/nl2ke/batch")
def batch(body: BatchIn, request: Request, db: Session = Depends(get_db),
          user: dict = Depends(current_user)):
    lib = _archer(request)
    tag = f"NL2KE-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
    params = body.model_dump()
    items = []
    for question in body.questions:
        question = question.strip()
        if not question:
            continue
        try:
            data = bridge.run(db, body.db_id, question, verify=body.verify, params=params)
            sql_payload = _run_sql(db, body.db_id, data.get("ke") or "") if body.run_sql else None
            rec = Nl2keRun(
                library_id=lib.id, db_id=body.db_id, batch_tag=tag, question=question,
                ke=data.get("ke") or "", status="ok", verify=body.verify,
                verify_passed=bool(data.get("verify_passed")),
                attempts=int(data.get("attempts") or 1),
                latency_ms=int(data.get("latency_ms") or 0),
                request_id=str(data.get("request_id") or ""),
                usage=data.get("usage"), timings=data.get("timings"),
                params=params, result_payload=data, sql_payload=sql_payload,
                created_by=user["username"],
            )
        except Exception as e:  # noqa: BLE001
            rec = Nl2keRun(
                library_id=lib.id, db_id=body.db_id, batch_tag=tag, question=question,
                status="error", verify=body.verify, error=str(e)[:4000],
                created_by=user["username"],
            )
        db.add(rec)
        db.commit()
        db.refresh(rec)
        items.append(_out(rec))
    return {"batch_tag": tag, "items": items}


@router.get("/api/nl2ke/batches")
def batches(request: Request, db: Session = Depends(get_db),
            user: dict = Depends(current_user)):
    _archer(request)
    rows = db.scalars(select(AnnotationBatch).where(
        AnnotationBatch.state == "draft").order_by(AnnotationBatch.id.desc()).limit(50)).all()
    return {"items": [{"id": x.id, "batch_id": x.batch_id, "name": x.name,
                       "state": x.state} for x in rows]}


@router.post("/api/nl2ke/runs/{rid}/to-batch")
def to_batch(rid: int, request: Request, db: Session = Depends(get_db),
             user: dict = Depends(current_user), batch_id: int | None = None):
    _archer(request)
    run = db.get(Nl2keRun, rid)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    if run.annotation_unit_id:
        unit = db.get(AnnotationUnit, run.annotation_unit_id)
        batch = db.get(AnnotationBatch, run.annotation_batch_id) if unit else None
        return {"run": _out(run), "batch_id": run.annotation_batch_id,
                "unit_id": run.annotation_unit_id,
                "batch_url": f"/annotation/batches/{run.annotation_batch_id}" if batch else None}
    if not (run.ke or "").strip():
        raise HTTPException(400, "该运行没有 KE")
    if batch_id:
        batch = db.get(AnnotationBatch, batch_id)
        if not batch:
            raise HTTPException(404, "批次不存在")
    else:
        batch = db.scalar(select(AnnotationBatch).where(
            AnnotationBatch.batch_id == f"NL2KE-{run.db_id}"))
        if batch is None:
            batch = AnnotationBatch(
                batch_id=f"NL2KE-{run.db_id}"[:64],
                name=f"NL2KE · {run.db_id}",
                source_name=run.db_id, source_version="NL2KE",
                source_checksum=f"nl2ke:{run.db_id}", source_type="other",
                domain="Archer", guide_version="NL2KE", state="draft",
                note="由 OMS NL2KE 生成结果转入",
                created_by=user["username"],
            )
            db.add(batch)
            db.flush()
            db.add(AnnotationMember(batch_id=batch.id, username=user["username"],
                                    role="batch_owner", side=None,
                                    group_name=user.get("group") or ""))
            db.flush()
    if batch.state != "draft":
        raise HTTPException(400, "只能加入草稿状态的标注批次")
    unit_id = f"NL2KE-{run.id}"
    unit = db.scalar(select(AnnotationUnit).where(
        AnnotationUnit.batch_id == batch.id, AnnotationUnit.unit_id == unit_id))
    if unit is None:
        unit = AnnotationUnit(
            batch_id=batch.id, unit_id=unit_id,
            source_id=f"nl2ke:{run.db_id}:{run.id}",
            source_type="other",
            source_locator={"nl2ke_run_id": run.id, "db_id": run.db_id},
            raw_text=run.question,
            source_payload={"nl2ke_run_id": run.id, "db_id": run.db_id,
                            "question": run.question, "ke": run.ke,
                            "params": run.params or {},
                            "steps": _steps(run),
                            "verify": run.verify, "verify_passed": run.verify_passed,
                            "nl2ke_status": "pending"},
            status="pending",
        )
        db.add(unit)
        db.flush()
    run.annotation_batch_id = batch.id
    run.annotation_unit_id = unit.id
    run.status = "in_batch"
    db.commit()
    db.refresh(run)
    return {"run": _out(run), "batch_id": batch.id, "unit_id": unit.id,
            "batch_url": f"/annotation/batches/{batch.id}"}


@router.post("/api/nl2ke/runs/{rid}/accept")
def accept_run(rid: int, request: Request, db: Session = Depends(get_db),
               user: dict = Depends(current_user)):
    lib = _archer(request)
    run = db.get(Nl2keRun, rid)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    if run.gold_item_id:
        return {"run": _out(run), "gold_item_id": run.gold_item_id}
    if not (run.ke or "").strip():
        raise HTTPException(400, "该运行没有 KE")
    gold = GoldItem(
        article_code="NL2KE",
        ke_id=f"NL2KE-{run.id}",
        group_name="NL2KE",
        ke_text=run.ke,
        concepts=[],
        status="draft",
        version=1,
        created_by=user["username"],
        updated_by=user["username"],
    )
    db.add(gold)
    db.flush()
    run.gold_item_id = gold.id
    run.status = "accepted"
    db.commit()
    db.refresh(run)
    return {"run": _out(run), "gold_item_id": gold.id}


@router.post("/api/nl2ke/runs/{rid}/reject")
def reject_run(rid: int, request: Request, db: Session = Depends(get_db),
               user: dict = Depends(current_user)):
    _archer(request)
    run = db.get(Nl2keRun, rid)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    run.status = "rejected"
    db.commit()
    return _out(run)


@router.get("/api/nl2ke/runs")
def runs(request: Request, limit: int = 30, db: Session = Depends(get_db),
         user: dict = Depends(current_user)):
    _archer(request)
    rows = db.scalars(select(Nl2keRun).order_by(Nl2keRun.id.desc())
                      .limit(max(1, min(limit, 100)))).all()
    return {"items": [_out(x, include_trace=False) for x in rows]}


@router.get("/api/nl2ke/runs/{rid}")
def run_detail(rid: int, request: Request, db: Session = Depends(get_db),
               user: dict = Depends(current_user)):
    lib = _archer(request)
    rec = db.get(Nl2keRun, rid)
    if rec is None or rec.library_id != lib.id:
        raise HTTPException(404, "运行记录不存在")
    return _out(rec)


@router.post("/api/nl2ke/runs/{rid}/equivalence")
def equivalence(rid: int, body: EquivalenceIn, request: Request,
                db: Session = Depends(get_db), user: dict = Depends(current_user)):
    lib = _archer(request)
    rec = db.get(Nl2keRun, rid)
    if rec is None or rec.library_id != lib.id:
        raise HTTPException(404, "运行记录不存在")
    if not (rec.ke or "").strip():
        raise HTTPException(400, "该运行没有 KE，无法做等价校验")
    try:
        result = ke_matcher.judge(body.reference_ke, rec.ke,
                                  request_timeout_s=body.request_timeout_s)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)[:1000]) from e
    payload = dict(rec.result_payload or {})
    payload["equivalence"] = {"reference_ke": body.reference_ke, **result}
    rec.result_payload = payload
    db.commit()
    return {"run_id": rec.id, "equivalence": payload["equivalence"]}


@router.get("/nl2ke/runs/{rid}")
def run_overview(rid: int, request: Request, db: Session = Depends(get_db),
                 user: dict = Depends(current_user)):
    lib = _archer(request)
    rec = _run_or_404(db, lib, rid)
    trace = (rec.result_payload or {}).get("trace") or {}
    return templates.TemplateResponse(request=request, name="nl2ke_run.html", context={
        "request": request, "user": user, "run": _out(rec, include_trace=False),
        "trace_available": bool(trace),
        "steps": [{"key": k, "label": label, "desc": NL2KE_STEP_DESC.get(k, "")}
                  for k, label in NL2KE_TRACE_STEPS],
    })


@router.get("/nl2ke/runs/{rid}/steps/{step}")
def run_step(rid: int, step: str, request: Request, db: Session = Depends(get_db),
             user: dict = Depends(current_user)):
    lib = _archer(request)
    rec = _run_or_404(db, lib, rid)
    keys = [k for k, _ in NL2KE_TRACE_STEPS]
    if step not in keys:
        raise HTTPException(404, "未知步骤")
    trace = (rec.result_payload or {}).get("trace") or {}
    idx = keys.index(step)
    return templates.TemplateResponse(request=request, name="nl2ke_step.html", context={
        "request": request, "user": user,
        "run": _out(rec, include_trace=False),
        "trace_available": bool(trace),
        "trace": trace,
        "step": step,
        "step_label": dict(NL2KE_TRACE_STEPS)[step],
        "step_desc": NL2KE_STEP_DESC.get(step, ""),
        "step_index": idx + 1,
        "data": _step_trace(trace, step),
        "steps": [{"key": k, "label": label, "desc": NL2KE_STEP_DESC.get(k, "")}
                  for k, label in NL2KE_TRACE_STEPS],
        "prev_step": NL2KE_TRACE_STEPS[idx - 1] if idx > 0 else None,
        "next_step": NL2KE_TRACE_STEPS[idx + 1] if idx + 1 < len(NL2KE_TRACE_STEPS) else None,
    })


@router.get("/nl2ke")
def page(request: Request, db: Session = Depends(get_db),
         user: dict = Depends(current_user)):
    lib = getattr(request.state, "library", None)
    is_archer = bool(lib and lib.key == "archer")
    rows = []
    if is_archer:
        rows = db.scalars(select(Nl2keRun).order_by(Nl2keRun.id.desc()).limit(20)).all()
    return templates.TemplateResponse(request=request, name="nl2ke.html", context={
        "request": request, "user": user, "archer": is_archer,
        "runs": [_out(x, include_trace=False) for x in rows],
        "base_url": bridge.BASE_URL, "agent": bridge.AGENT,
    })
