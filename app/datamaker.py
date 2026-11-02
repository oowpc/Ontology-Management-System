"""DataMaker 任务：导出 OMS Archer 本体、启动 CLI、记录日志并导入草稿批次。"""
from __future__ import annotations

import json
import os
import re
import signal
from collections import Counter
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import nl2ke as bridge
from .db import SessionLocal
from .library_context import library_scope
from .models import (AnnotationBatch, AnnotationMember, AnnotationUnit, Concept,
                     DatamakerJob, Operator, OperatorInput, OntologyLibrary,
                     OntologySource)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATAMAKER_DIR = Path(os.environ.get("DATAMAKER_DIR", "/home/oowpc/datamaker")).resolve()
DATAMAKER_PYTHON = Path(os.environ.get(
    "DATAMAKER_PYTHON", str(DATAMAKER_DIR / ".venv" / "bin" / "python")))
WORK_ROOT = Path(os.environ.get("DATAMAKER_WORK_ROOT", str(PROJECT_ROOT / "data" / "datamaker")))
if not WORK_ROOT.is_absolute():
    WORK_ROOT = (PROJECT_ROOT / WORK_ROOT).resolve()
LLM_BASE_URL = os.environ.get("DATAMAKER_LLM_BASE_URL", "").strip()
LLM_API_KEY = os.environ.get("DATAMAKER_LLM_API_KEY", "").strip()
LLM_MODEL = os.environ.get("DATAMAKER_LLM_MODEL", "qwen3-coder-flash").strip()
MODELS = [x.strip() for x in os.environ.get("DATAMAKER_MODELS", LLM_MODEL).split(",") if x.strip()]
KE2SQL_PATH = os.environ.get("DATAMAKER_KE2SQL_PATH", "/home/oowpc/ke2sql-v031").strip()
VERIFIER_PATH = os.environ.get(
    "DATAMAKER_VERIFIER_PATH", "/home/oowpc/Syntastic_and_Lexical-Verifier").strip()
Z3_PATH = os.environ.get("DATAMAKER_Z3_PATH", "/home/oowpc/KE-matcher").strip()
SERVICE_URL = os.environ.get("DATAMAKER_SERVICE_URL", "http://127.0.0.1:8081").strip()
MODES = ("gen-ke", "roundtrip", "tri")
NL_SOURCES = ("ke", "sql")
MAX_ACTIVE = max(1, int(os.environ.get("DATAMAKER_MAX_ACTIVE", "1")))
TIMEOUT = max(60, int(os.environ.get("DATAMAKER_TIMEOUT", "7200")))

_active: set[int] = set()
_lock = threading.Lock()
_ARTIFACTS = ("ke1.jsonl", "ontology.json", "spec.json", "coverage.json",
              "run_manifest.jsonl", "job.log", "registry.json", "name_map.json", "equiv.jsonl", "nli.jsonl",
              "report.json", "equiv_tri.jsonl", "z3_report_tri.jsonl",
              "report_tri.json", "report_tri.md", "sql.jsonl", "nl2.jsonl",
              "ke2.jsonl", "ke3.jsonl")


def _now():
    return datetime.now()


def job_dict(j: DatamakerJob) -> dict:
    return {
        "id": j.id, "db_id": j.db_id, "status": j.status, "model": j.model,
        "mode": (j.params or {}).get("mode", "gen-ke"),
        "n": j.n, "workers": j.workers, "ke_types": j.ke_types,
        "progress": j.progress, "result_count": j.result_count,
        "imported_count": j.imported_count, "pid": j.pid,
        "annotation_batch_id": j.annotation_batch_id,
        "error": j.error or "", "created_by": j.created_by,
        "created_at": j.created_at.isoformat(timespec="seconds") if j.created_at else None,
        "started_at": j.started_at.isoformat(timespec="seconds") if j.started_at else None,
        "finished_at": j.finished_at.isoformat(timespec="seconds") if j.finished_at else None,
    }


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("question") and row.get("ke"):
            out.append(row)
    return out


def _tail(path: Path, limit: int = 8000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = re.sub(r"(--key\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[REDACTED]", text)
    return text[-limit:]


def _ascii_name(name: str, prefix: str, ident: int, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", str(name or "")).strip("_")
    if not base or not re.match(r"^[A-Za-z_]", base):
        base = f"{prefix}{ident}"
    candidate = base
    n = 2
    while candidate in used:
        candidate = f"{base}_{n}"
        n += 1
    used.add(candidate)
    return candidate


def _generic_registry(db: Session, library_id: int) -> tuple[None, dict]:
    """非 Archer / 无 SQLite 库：生成 datamaker ontology-only registry。"""
    rows = db.scalars(select(Concept).order_by(Concept.id)).all()
    used_c: set[str] = set()
    names = {}
    concepts = {}
    for c in rows:
        if c.status == "disabled":
            continue
        name = _ascii_name(c.name, "Concept", c.id, used_c)
        names[c.id] = name
        concepts[name] = {
            "sources": {},
            "description": c.description or c.definition or "",
            "parents": [],
        }
    input_rows = db.scalars(select(OperatorInput).order_by(
        OperatorInput.operator_id, OperatorInput.position)).all()
    inputs = {}
    for oi in input_rows:
        if oi.concept_id and oi.concept_id in names:
            inputs.setdefault(oi.operator_id, []).append(names[oi.concept_id])
    ops = db.scalars(select(Operator).order_by(Operator.id)).all()
    used_o: set[str] = set()
    operators = {}
    op_names = {}
    for o in ops:
        if o.status == "disabled" or o.subject_concept_id not in names:
            continue
        op_name = _ascii_name(o.name, "Operator", o.id, used_o)
        op_names[op_name] = o.name
        sig = inputs.get(o.id) or ([names[o.subject_concept_id]]
                                   if o.subject_concept_id in names else [])
        if not sig:
            continue
        out_name = names.get(o.output_concept_id)
        if out_name:
            operators[op_name] = {
                "type": "LINK", "signature": sig, "path": [],
                "description": o.description or "",
            }
        else:
            operators[op_name] = {
                "type": "PROPERTY", "concept": sig[0], "slot": o.name,
                "description": o.description or "",
            }
    if not concepts or not operators:
        raise ValueError("该库没有可导出的本体")
    return None, {
        "_note": "OMS → datamaker registry (ontology-only)",
        "_name_map": {
            "concepts": {names[c.id]: c.name for c in rows if c.id in names},
            "operators": op_names,
        },
        "concepts": concepts,
        "operators": operators,
    }


def _registry(db: Session, db_id: str, library_id: int) -> tuple[Path | None, dict]:
    if not db_id or db_id == "__ontology__":
        return _generic_registry(db, library_id)
    src = db.scalar(select(OntologySource).where(OntologySource.key == db_id))
    if src is None or not src.sqlite_path:
        raise ValueError("该数据库没有注册 SQLite")
    db_path = Path(src.sqlite_path)
    if not db_path.exists():
        raise ValueError(f"SQLite 不存在：{db_path}")
    _table, ontology = bridge.sub_ontology(db_id, db)
    concepts = ontology.get("concepts") or {}
    operators = ontology.get("operators") or {}
    if not concepts or not operators:
        raise ValueError("该库没有可导出的 Archer 本体")
    return db_path, {
        "_note": "OMS → datamaker registry",
        "concepts": concepts,
        "operators": operators,
    }


def active_count(db: Session, library_id: int) -> int:
    return len(db.scalars(select(DatamakerJob.id).where(
        DatamakerJob.library_id == library_id,
        DatamakerJob.status.in_(("queued", "running")))).all())


def claim_next_job(worker_id: str) -> tuple[int, int] | None:
    """原子 claim 一个 queued 任务；多个 worker 抢同一任务时只有一个成功。"""
    with library_scope(None):
        db = SessionLocal()
        try:
            job = db.scalar(select(DatamakerJob).where(
                DatamakerJob.status == "queued").order_by(DatamakerJob.id).limit(1))
            if job is None:
                return None
            now = _now()
            result = db.execute(
                update(DatamakerJob)
                .where(DatamakerJob.id == job.id, DatamakerJob.status == "queued")
                .values(status="running", started_at=now, worker_id=worker_id,
                        heartbeat_at=now)
            )
            db.commit()
            return (job.id, job.library_id) if result.rowcount else None
        finally:
            db.close()


def start_job(db: Session, *, library, db_id: str, model: str, n: int,
              workers: int, ke_types: str, resume: bool, actor: str,
              mode: str = "gen-ke", nl_source: str = "ke", cross_check: bool = False,
              service_url: str = "", timeout_s: int | None = None,
              retry_of: int | None = None) -> DatamakerJob:
    if model not in MODELS:
        raise ValueError("不支持的模型")
    if mode not in MODES:
        raise ValueError("不支持的任务模式")
    if nl_source not in NL_SOURCES:
        raise ValueError("不支持的 NL 来源")
    if not LLM_API_KEY or not LLM_BASE_URL:
        raise ValueError("DATAMAKER_LLM_API_KEY / DATAMAKER_LLM_BASE_URL 未配置")
    if active_count(db, library.id) >= MAX_ACTIVE:
        raise ValueError("当前库已有运行中的生成任务")
    db_id = db_id or "__ontology__"
    if db_id != "__ontology__":
        src = db.scalar(select(OntologySource).where(
            OntologySource.library_id == library.id, OntologySource.key == db_id))
        if src is None:
            raise ValueError("数据库不属于当前本体库")
    db_path, registry = _registry(db, db_id, library.id)

    job = DatamakerJob(
        library_id=library.id, db_id=db_id, status="queued", model=model,
        n=n, workers=workers, ke_types=ke_types,
        params={
            "resume": bool(resume), "mode": mode, "nl_source": nl_source,
            "cross_check": bool(cross_check), "service_url": service_url or SERVICE_URL,
            "timeout_s": int(timeout_s or TIMEOUT),
            **({"retry_of": retry_of} if retry_of else {}),
        },
        created_by=actor,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    workdir = WORK_ROOT / f"job-{job.id}"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    if registry.get("_name_map"):
        (workdir / "name_map.json").write_text(
            json.dumps(registry["_name_map"], ensure_ascii=False, indent=2),
            encoding="utf-8")
    log_path = workdir / "job.log"
    log_path.write_text("", encoding="utf-8")
    job.workdir = str(workdir)
    job.log_path = str(log_path)
    db.commit()
    db.refresh(job)
    # 任务只入队；真实执行由独立 datamaker worker 完成。
    return job


def _env(model: str) -> dict:
    env = os.environ.copy()
    env.update({
        "LLM_API_KEY": LLM_API_KEY,
        "LLM_BASE_URL": LLM_BASE_URL,
        "LLM_MODEL": model,
        "KE_FORM_VALIDATOR_PATH": VERIFIER_PATH,
        "KE2SQL_PATH": KE2SQL_PATH,
        "PYTHONUNBUFFERED": "1",
    })
    return env


def _command(job: DatamakerJob, db_path: Path | None) -> list[str]:
    p = job.params or {}
    mode = p.get("mode", "gen-ke")
    if mode == "gen-ke":
        cmd = [
            str(DATAMAKER_PYTHON), str(DATAMAKER_DIR / "gen_ke.py"),
            *(["--db", str(db_path), "--db-id", job.db_id] if db_path else []),
            "--ontology", str(Path(job.workdir) / "registry.json"),
            "--workdir", job.workdir, "--n", str(job.n),
            "--workers", str(job.workers), "--model", job.model,
        ]
    else:
        cmd = [
            str(DATAMAKER_PYTHON), str(DATAMAKER_DIR / "roundtrip_pipeline.py"),
            "all" if mode == "roundtrip" else "tri",
            *(["--db", str(db_path), "--db-id", job.db_id] if db_path else []),
            "--ontology", str(Path(job.workdir) / "registry.json"),
            "--workdir", job.workdir, "--n", str(job.n),
            "--workers", str(job.workers), "--model", job.model,
            "--service-url", str(p.get("service_url") or SERVICE_URL),
            "--nl-source", str(p.get("nl_source") or "ke"),
            "--z3-path", Z3_PATH,
        ]
        if p.get("cross_check"):
            cmd += ["--cross-check"]
    if job.ke_types.strip():
        cmd += ["--ke-types", job.ke_types.strip()]
    if (job.params or {}).get("resume"):
        cmd += ["--resume"]
    if VERIFIER_PATH:
        cmd += ["--verifier-path", VERIFIER_PATH]
    if KE2SQL_PATH:
        cmd += ["--ke-path", KE2SQL_PATH]
    return cmd


def _set(job_id: int, **kw) -> DatamakerJob | None:
    db = SessionLocal()
    try:
        job = db.get(DatamakerJob, job_id)
        if job is None:
            return None
        for k, v in kw.items():
            setattr(job, k, v)
        db.commit()
        db.refresh(job)
        return job
    finally:
        db.close()


def _run_job(job_id: int) -> None:
    # 后台线程没有请求中间件设置的 library context；先跨库定位任务，再切到任务所属库。
    with library_scope(None):
        db = SessionLocal()
        try:
            job = db.get(DatamakerJob, job_id)
            library_id = job.library_id if job else None
        finally:
            db.close()
    if library_id is None:
        return
    with library_scope(library_id):
        _run_job_inner(job_id)


def _run_job_inner(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(DatamakerJob, job_id)
        if job is None or job.status == "cancelled":
            with _lock:
                _active.discard(job_id)
            return
        db_path, _ = _registry(db, job.db_id, job.library_id)
        job.status = "running"
        job.started_at = _now()
        db.commit()
        db.refresh(job)
        cmd = _command(job, db_path)
    except Exception as e:  # noqa: BLE001
        _set(job_id, status="failed", error=f"启动失败：{e}"[:4000], finished_at=_now())
        with _lock:
            _active.discard(job_id)
        db.close()
        return
    db.close()

    log_path = Path(job.log_path or "")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    timeout_s = int((job.params or {}).get("timeout_s") or TIMEOUT)
    proc = None
    try:
        with log_path.open("a", encoding="utf-8", errors="replace") as log:
            log.write("$ " + " ".join(cmd) + "\n")
            log.flush()
            proc = subprocess.Popen(
                cmd, cwd=str(DATAMAKER_DIR), stdout=log, stderr=subprocess.STDOUT,
                env=_env(job.model), start_new_session=True, text=True,
            )
            job = _set(job_id, pid=proc.pid)
            if job is None:
                proc.terminate()
                return
            out = Path(job.workdir) / "ke1.jsonl"
            while proc.poll() is None:
                time.sleep(1)
                count = len(_records(out))
                job = _set(job_id, progress=count, heartbeat_at=_now())
                if job is None:
                    proc.terminate()
                    break
                if job.status == "cancelled":
                    _kill_group(proc.pid)
                    break
                if time.monotonic() - started > timeout_s:
                    _kill_group(proc.pid)
                    _set(job_id, status="failed", error=f"超时 {timeout_s}s",
                         finished_at=_now())
                    return
            rc = proc.wait()
    except Exception as e:  # noqa: BLE001
        _set(job_id, status="failed", error=f"运行失败：{e}"[:4000], finished_at=_now())
        with _lock:
            _active.discard(job_id)
        return

    job = _set(job_id, pid=None)
    if job is None:
        with _lock:
            _active.discard(job_id)
        return
    if job.status == "cancelled":
        _set(job_id, finished_at=_now(), result_count=len(_records(Path(job.workdir) / "ke1.jsonl")))
    elif rc != 0:
        _set(job_id, status="failed", finished_at=_now(),
             error=(_tail(log_path) or f"exit code {rc}")[-4000:])
    else:
        try:
            db = SessionLocal()
            try:
                _import_results(db, job)
            finally:
                db.close()
            _set(job_id, status="succeeded", finished_at=_now())
        except Exception as e:  # noqa: BLE001
            _set(job_id, status="failed", finished_at=_now(), error=f"导入失败：{e}"[:4000])
    with _lock:
        _active.discard(job_id)


def _kill_group(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        pass


def _import_results(db: Session, job: DatamakerJob) -> None:
    # job 可能来自另一个已关闭的 session；必须在当前 db 重新挂载后再写回计数字段。
    row_job = db.get(DatamakerJob, job.id)
    if row_job is None:
        return
    rows = _records(Path(row_job.workdir) / "ke1.jsonl")
    row_job.result_count = len(rows)
    if not rows:
        row_job.imported_count = 0
        db.commit()
        return
    code = f"DM-{row_job.id}"
    batch = db.scalar(select(AnnotationBatch).where(AnnotationBatch.batch_id == code))
    if batch is None:
        batch = AnnotationBatch(
            library_id=row_job.library_id, batch_id=code,
            name=f"DataMaker #{row_job.id} · {row_job.db_id}",
            source_name="datamaker", source_version=f"job-{row_job.id}",
            source_type="query", domain="Archer", statistical_unit="题目",
            state="draft", note="DataMaker 自动生成的候选数据，未审核。",
            created_by=row_job.created_by,
        )
        db.add(batch)
        db.flush()
        db.add(AnnotationMember(library_id=row_job.library_id, batch_id=batch.id,
                                username=row_job.created_by, role="batch_owner"))
    for i, row in enumerate(rows, 1):
        unit_id = f"DM-{row_job.id}-{i:04d}"
        exists = db.scalar(select(AnnotationUnit.id).where(
            AnnotationUnit.batch_id == batch.id, AnnotationUnit.unit_id == unit_id))
        if exists:
            continue
        question = str(row.get("question") or "")
        lang = "zh" if re.search(r"[\u4e00-\u9fff]", question) else "en"
        db.add(AnnotationUnit(
            library_id=row_job.library_id, batch_id=batch.id, unit_id=unit_id,
            source_id=f"datamaker:{row_job.id}", source_type="query",
            raw_text=question, language=lang,
            source_payload={
                "ke": row.get("ke") or "", "evidence": row.get("evidence") or "",
                "type": row.get("type") or "", "tags": row.get("tags") or [],
                "datamaker_job_id": row_job.id, "source": "datamaker",
            },
            status="pending",
        ))
    row_job.annotation_batch_id = batch.id
    row_job.imported_count = len(rows)
    db.commit()


def quality(job: DatamakerJob) -> dict:
    root = Path(job.workdir) if job.workdir else Path("")
    rows = _records(root / "ke1.jsonl") if job.workdir else []
    types = Counter(str(r.get("type") or "unknown") for r in rows)
    domains = Counter(str(r.get("domain") or "unknown") for r in rows)
    cov = {}
    spec = {}
    try:
        cov = json.loads((root / "coverage.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        spec = json.loads((root / "spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    generated = len(rows) if rows else job.result_count
    requested = job.n or 0
    onto = cov.get("ontology") or {}
    builtin = cov.get("builtin") or {}
    report = {}
    equiv = []
    for name in ("report.json", "report_tri.json"):
        try:
            report = json.loads((root / name).read_text(encoding="utf-8"))
            break
        except (OSError, ValueError):
            continue
    for name in ("equiv_tri.jsonl", "equiv.jsonl"):
        try:
            equiv = [json.loads(x) for x in (root / name).read_text(
                encoding="utf-8").splitlines() if x.strip()]
            break
        except (OSError, ValueError):
            continue
    return {
        "requested": requested, "generated": generated,
        "imported": job.imported_count,
        "mode": (getattr(job, "params", None) or {}).get("mode", "gen-ke"),
        "completion_rate": round(generated * 100 / requested, 1) if requested else 0,
        "types": dict(types), "domains": dict(domains),
        "ontology_used": onto.get("used", 0), "ontology_total": onto.get("total", 0),
        "builtin_used": builtin.get("used", 0), "builtin_total": builtin.get("total", 0),
        "n_actual": spec.get("n_actual", generated),
        "coverage_missing": (spec.get("coverage") or {}).get("domain_missing") or [],
        "equiv_pairs": len(equiv),
        "equiv_pass": sum(1 for x in equiv if x.get("exec_status") == "pass"),
        "equiv_mismatch": sum(1 for x in equiv if x.get("exec_status") == "mismatch"),
        "tri_identical": (report.get("categories_all") or {}).get("identical", 0),
        "tri_equiv": (report.get("categories_all") or {}).get("sem_eq_diff_form", 0),
        "tri_diff": (report.get("categories_all") or {}).get("sem_diff", 0),
        "nli_paraphrase": (report.get("nli") or {}).get("paraphrase", 0),
        "nli_neutral": (report.get("nli") or {}).get("neutral", 0),
        "verdict_consistent": (report.get("verdict") or {}).get("consistent", 0),
    }


def aggregate(jobs: list[DatamakerJob]) -> dict:
    total = len(jobs)
    done = [j for j in jobs if j.status == "succeeded"]
    failed = [j for j in jobs if j.status in ("failed", "interrupted", "cancelled")]
    generated = sum(j.result_count for j in done)
    imported = sum(j.imported_count for j in done)
    return {
        "total": total, "succeeded": len(done), "failed": len(failed),
        "running": sum(1 for j in jobs if j.status in ("queued", "running")),
        "generated": generated, "imported": imported,
        "success_rate": round(len(done) * 100 / total, 1) if total else 0,
    }


def retry_job(db: Session, *, library, job: DatamakerJob, actor: str) -> DatamakerJob:
    if job.status in ("queued", "running"):
        raise ValueError("任务仍在运行，不能重试")
    return start_job(
        db, library=library, db_id=job.db_id, model=job.model, n=job.n,
        workers=job.workers, ke_types=job.ke_types,
        resume=bool((job.params or {}).get("resume")), actor=actor,
        mode=(job.params or {}).get("mode", "gen-ke"),
        nl_source=(job.params or {}).get("nl_source", "ke"),
        cross_check=bool((job.params or {}).get("cross_check")),
        service_url=(job.params or {}).get("service_url", ""),
        timeout_s=(job.params or {}).get("timeout_s"),
        retry_of=job.id,
    )


def cancel_job(db: Session, job: DatamakerJob) -> DatamakerJob:
    if job.status not in ("queued", "running"):
        return job
    pid = job.pid
    job.status = "cancelled"
    job.finished_at = _now()
    db.commit()
    if pid:
        _kill_group(pid)
    return job


def recover_stale(db: Session, *, timeout_s: int = 300) -> None:
    """只清理 heartbeat 超时的 running；活跃 worker 的任务不能被误杀。"""
    cutoff = _now() - timedelta(seconds=max(1, timeout_s))
    rows = db.scalars(select(DatamakerJob).where(
        DatamakerJob.status == "running",
        (DatamakerJob.heartbeat_at.is_(None)) | (DatamakerJob.heartbeat_at < cutoff)
    )).all()
    for job in rows:
        if job.pid:
            _kill_group(job.pid)
        job.status = "interrupted"
        job.pid = None
        job.worker_id = None
        job.finished_at = _now()
        job.error = job.error or "DataMaker worker heartbeat 超时，任务中断"
    db.commit()


def artifacts(job: DatamakerJob) -> list[dict]:
    root = Path(job.workdir)
    return [{"name": n, "size": (root / n).stat().st_size}
            for n in _ARTIFACTS if (root / n).exists()]


def log_tail(job: DatamakerJob, limit: int = 12000) -> str:
    return _tail(Path(job.log_path), limit) if job.log_path else ""


def artifact_path(job: DatamakerJob, name: str) -> Path | None:
    if name not in _ARTIFACTS:
        return None
    path = Path(job.workdir) / name
    return path if path.exists() else None


def preview(job: DatamakerJob, limit: int = 20) -> list[dict]:
    rows = _records(Path(job.workdir) / "ke1.jsonl")
    return rows[:max(0, limit)]
