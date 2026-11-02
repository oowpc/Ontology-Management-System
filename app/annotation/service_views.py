"""标注服务：视图/查询辅助子模块。

核心工作流在 service_core；本模块只放页面/API 需要的统计、单元上下文和查询执行。
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AnnotationUnit, QueryRun
from .service_core import *  # noqa: F401,F403
from .service_core import _has_current, _is_admin, _members  # noqa: F401

def batch_stats(db: Session, batch: AnnotationBatch) -> dict:
    units = list_units(db, batch)
    co_atoms = {"A": 0, "B": 0}
    aligned_atoms = 0
    for u in units:
        for c in current_co(db, u.id):
            co_atoms[c.side] += len(c.concepts or []) + len(c.constants or []) + len(c.operators or [])
        a = current_alignment(db, u.id)
        if a:
            aligned_atoms += sum(len((a.aligned or {}).get(k) or [])
                                 for k in ("concepts", "constants", "operators"))
    return {
        "units": len(units),
        "co_a": sum(1 for u in units if _has_current(db, CoAnnotation, u.id, side="A")),
        "co_b": sum(1 for u in units if _has_current(db, CoAnnotation, u.id, side="B")),
        "aligned": sum(1 for u in units if current_alignment(db, u.id)),
        "ke_a": sum(1 for u in units if _has_current(db, KeAnnotation, u.id, side="A")),
        "ke_b": sum(1 for u in units if _has_current(db, KeAnnotation, u.id, side="B")),
        "gold": sum(1 for u in units if current_gold(db, u.id)),
        "co_atoms_a": co_atoms["A"], "co_atoms_b": co_atoms["B"],
        "aligned_atoms": aligned_atoms,
    }

def batch_summary(db: Session, batch: AnnotationBatch) -> dict:
    members = _members(db, batch.id)
    return {
        "batch": batch,
        "stats": batch_stats(db, batch),
        "annotators": {m.side: m.username for m in members if m.role == "annotator" and m.side},
        "lead": next((m.username for m in members if m.role == "lead"), ""),
        "members": members,
    }

def unit_rows(db: Session, batch: AnnotationBatch) -> list[dict]:
    rows = []
    for u in list_units(db, batch):
        rows.append({
            "unit": u,
            "co_a": _has_current(db, CoAnnotation, u.id, side="A"),
            "co_b": _has_current(db, CoAnnotation, u.id, side="B"),
            "aligned": bool(current_alignment(db, u.id)),
            "ke_a": _has_current(db, KeAnnotation, u.id, side="A"),
            "ke_b": _has_current(db, KeAnnotation, u.id, side="B"),
            "gold": bool(current_gold(db, u.id)),
        })
    return rows

def unit_context(db: Session, unit: AnnotationUnit, user: dict) -> dict:
    batch = get_batch_or_404(db, unit.batch_id)
    roles = roles_for(db, batch, user)
    sides = sides_for(db, batch, user)
    can_view = can_view_unit_data(db, batch, user)
    co = {c.side: c for c in current_co(db, unit.id)} if can_view else {}
    ke = {k.side: k for k in current_ke(db, unit.id)} if can_view else {}
    state = batch.state
    privileged = _is_admin(user) or bool(roles & {"lead", "arbiter"})
    # CO 盲评隔离
    if can_view and state == "co_blind" and not privileged:
        co = {s: c for s, c in co.items() if s in sides}
    # KE 盲评隔离
    if can_view and state == "ke_blind" and not privileged:
        ke = {s: k for s, k in ke.items() if s in sides}
    align = current_alignment(db, unit.id) if can_view and state not in {
        "draft", "dataset_frozen", "co_blind"} else None
    gold = current_gold(db, unit.id) if can_view and state in {
        "gold_merge", "cross_review", "validation", "passed", "rework"} else None
    my_side = next(iter(sides), None)
    can_submit_ke_now = (
        state == "ke_blind" and my_side and can_annotate_side(db, batch, user, my_side)
        and align is not None and align.conclusion != "pending")
    runs = latest_runs(db, unit.id) if can_view else []
    if can_view and state == "ke_blind" and not privileged:
        runs = [r for r in runs if r.side in sides]
    latest_run = next((r for r in runs if r.side == my_side and r.status == "passed"), None)
    can_run = (can_view and state in {"ke_blind", "gold_merge", "cross_review",
                                      "validation", "rework"}
               and unit.source_type in {"sql", "lean", "sparql"}
               and (my_side or _is_admin(user) or bool(roles & {"lead", "arbiter"})))
    return {
        "batch": batch, "unit": unit, "evidence": list_evidence(db, unit),
        "co_a": co.get("A"), "co_b": co.get("B"),
        "alignment": align, "ke_a": ke.get("A"), "ke_b": ke.get("B"), "gold": gold,
        "runs": runs, "latest_run": latest_run,
        "roles": roles, "sides": sides, "my_side": my_side,
        "can_view_data": can_view,
        "can_submit_co": (state == "co_blind" and my_side is not None
                          and can_annotate_side(db, batch, user, my_side)),
        "can_align": state == "co_alignment" and can_align(db, batch, user),
        "can_submit_ke": bool(can_submit_ke_now),
        "can_submit_gold": state == "gold_merge" and can_align(db, batch, user),
        "can_run_query": can_run,
        "can_add_evidence": can_view and state != "passed",
        "state": state, "state_label": STATE_LABELS.get(state, state),
    }

def latest_runs(db: Session, unit_id: int, side: str | None = None) -> list[QueryRun]:
    stmt = select(QueryRun).where(QueryRun.unit_id == unit_id)
    if side:
        stmt = stmt.where(QueryRun.side == side)
    return db.scalars(stmt.order_by(QueryRun.id.desc())).all()

def run_query(db: Session, unit: AnnotationUnit, side: str, actor: dict) -> QueryRun:
    batch = get_batch_or_404(db, unit.batch_id)
    require_view_batch(db, batch, actor)
    if side not in {"A", "B"}:
        raise HTTPException(400, "side 必须是 A/B")
    if batch.state not in {"ke_blind", "gold_merge", "cross_review", "validation", "rework"}:
        raise HTTPException(400, "当前阶段不能执行查询/编译")
    roles = roles_for(db, batch, actor)
    if not (_is_admin(actor) or side in sides_for(db, batch, actor)
            or roles & {"lead", "arbiter", "batch_owner", "project_owner"}):
        raise HTTPException(403, "无权执行该侧查询")
    payload = unit.source_payload or {}
    source_type = unit.source_type
    status, stdout, stderr, result = "failed", "", "", {}
    command = {"source_type": source_type}
    if source_type == "sql":
        schema = str(payload.get("schema_sql") or "")
        target = str(payload.get("target_sql") or payload.get("sql") or "")
        command.update({"schema_sql": schema[:2000], "target_sql": target[:2000]})
        if not target.strip():
            raise HTTPException(400, "source_payload 缺 target_sql")
        if ";" in target.strip().rstrip(";"):
            raise HTTPException(400, "target_sql 只允许单条语句")
        conn = sqlite3.connect(":memory:")
        try:
            if schema.strip():
                conn.executescript(schema)
            plan = conn.execute("EXPLAIN " + target).fetchall()
            result = {"plan": [list(map(str, row)) for row in plan[:50]]}
            status = "passed"
            stdout = f"SQL 计划通过，{len(plan)} 行"
        except Exception as e:  # noqa: BLE001
            stderr = str(e)
        finally:
            conn.close()
    elif source_type == "sparql":
        try:
            from rdflib import Graph
        except ModuleNotFoundError:
            raise HTTPException(400, "SPARQL 执行需要安装 rdflib")
        rdf = str(payload.get("rdf") or payload.get("ttl") or "")
        query = str(payload.get("target_sparql") or payload.get("sparql") or "")
        command.update({"target_sparql": query[:2000]})
        if not rdf.strip() or not query.strip():
            raise HTTPException(400, "source_payload 缺 rdf / target_sparql")
        g = Graph()
        try:
            g.parse(data=rdf, format=str(payload.get("rdf_format") or "turtle"))
        except Exception:
            g.parse(data=rdf, format="xml")
        try:
            res = g.query(query)
            if res.type == "ASK":
                result = {"ask": bool(res.askAnswer)}
            else:
                rows = []
                for row in res:
                    if hasattr(row, "__getitem__") and res.vars:
                        rows.append({str(v): str(row[v]) for v in res.vars})
                    else:
                        rows.append([str(x) for x in row])
                    if len(rows) >= 100:
                        break
                result = {"vars": [str(v) for v in (res.vars or [])], "rows": rows}
            status = "passed"
            stdout = f"SPARQL 执行通过"
        except Exception as e:  # noqa: BLE001
            stderr = str(e)
    elif source_type == "lean":
        lean = os.environ.get("LEAN_BIN") or shutil.which("lean")
        source = str(payload.get("lean_source") or "")
        command["lean_bin"] = lean or ""
        if not lean:
            raise HTTPException(400, "未配置 LEAN 执行器（设置 LEAN_BIN）")
        if not source.strip():
            raise HTTPException(400, "source_payload 缺 lean_source")
        tmp = tempfile.NamedTemporaryFile("w", suffix=".lean", delete=False, encoding="utf-8")
        try:
            tmp.write(source)
            tmp.close()
            proc = subprocess.run([lean, tmp.name], capture_output=True, text=True, timeout=30)
            stdout, stderr = proc.stdout[-8000:], proc.stderr[-8000:]
            status = "passed" if proc.returncode == 0 else "failed"
            result = {"returncode": proc.returncode}
        except subprocess.TimeoutExpired:
            status, stderr = "failed", "LEAN 执行超时（30s）"
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
    else:
        raise HTTPException(400, "该单元不是 SQL/LEAN/SPARQL 类型")
    run = QueryRun(batch_id=batch.id, unit_id=unit.id, side=side,
                   source_type=source_type, command=command, status=status,
                   stdout=stdout[-8000:], stderr=stderr[-8000:], result=result,
                   created_by=actor["username"])
    db.add(run)
    db.flush()
    log_change(db, "query_run", run.id, "create", None, snapshot(run), actor["username"])
    db.commit()
    db.refresh(run)
    return run

