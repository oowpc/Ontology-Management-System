"""SOP 手工标注工作流：批次/单元/双盲/对齐/KE/GOLD/交叉检查/版本。"""
import json
import uuid

from conftest import login


def _create_batch(client, batch_id):
    r = client.post("/annotation/batches", data={
        "batch_id": batch_id, "name": "测试标注批次",
        "source_name": "测试来源", "source_version": "v1", "source_checksum": "abc",
        "source_type": "sql", "domain": "测试", "guide_version": "v0.4",
        "annotator_a": "annotator", "annotator_b": "annotator2",
        "lead": "reviewer", "arbiter": "admin",
        "cross_checkers": "annotator3,reviewer", "project_owner": "admin",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text
    path = r.headers["location"].split("?", 1)[0].rstrip("/")
    return int(path.split("/")[-1])


def _db():
    from app.db import SessionLocal
    return SessionLocal()


def _ok(r):
    assert r.status_code == 303, r.text
    loc = r.headers.get("location", "")
    assert "err=" not in loc, loc
    return r


def test_annotation_core_workflow(client):
    from sqlalchemy import select
    from app.models import AnnotationBatch, AnnotationUnit, OntologyVersion

    login(client, "admin")
    batch_id = "TEST-ANN-" + uuid.uuid4().hex[:6]
    bid = _create_batch(client, batch_id)
    db = _db()
    try:
        assert db.get(AnnotationBatch, bid).state == "draft"
    finally:
        db.close()

    units = [{
        "unit_id": "U-1", "source_id": "doc-1", "source_type": "sql",
        "raw_text": "测试原文", "translation_text": "测试译文",
        "language": "zh", "source_locator": {"page": 1, "paragraph": 2},
        "source_payload": {
            "schema_sql": "CREATE TABLE t (id INTEGER, name TEXT);",
            "target_sql": "SELECT * FROM t",
        },
    }]
    r = client.post(f"/annotation/batches/{bid}/units/import",
                    data={"units_json": json.dumps(units, ensure_ascii=False)},
                    follow_redirects=False)
    assert r.status_code == 303

    # 冻结数据集 → CO 双盲
    _ok(client.post(f"/annotation/batches/{bid}/state", data={"action": "freeze"},
                       follow_redirects=False))
    _ok(client.post(f"/annotation/batches/{bid}/state", data={"action": "start_co"},
                       follow_redirects=False))

    db = _db()
    try:
        unit = db.scalar(select(AnnotationUnit).where(AnnotationUnit.batch_id == bid))
        uid = unit.id
    finally:
        db.close()

    # A 提交 CO
    login(client, "annotator")
    r = client.post(f"/annotation/units/{uid}/evidence", data={
        "source_id": "doc-1", "kind": "text", "language": "zh",
        "quote": "测试原文证据", "locator": '{"page":1}',
    }, follow_redirects=False)
    assert r.status_code == 303
    db = _db()
    try:
        from app.models import AnnotationEvidence
        ev = db.scalar(select(AnnotationEvidence).where(AnnotationEvidence.unit_id == uid))
        evid = ev.id
    finally:
        db.close()
    r = client.post(f"/annotation/units/{uid}/co", data={
        "side": "A", "decision": "include", "concepts": "CO_A_CONCEPT\n共享概念",
        "constants": "", "operators": "OP_A", "reason": "A 理由",
        "evidence_ids": [str(evid)],
    }, follow_redirects=False)
    assert r.status_code == 303

    page_a = client.get(f"/annotation/units/{uid}")
    assert page_a.status_code == 200 and "CO_A_CONCEPT" in page_a.text
    assert "CO_B_CONCEPT" not in page_a.text

    # B 提交 CO；A 仍看不到 B
    login(client, "annotator2")
    r = client.post(f"/annotation/units/{uid}/co", data={
        "side": "B", "decision": "include", "concepts": "CO_B_CONCEPT\n共享概念",
        "constants": "", "operators": "OP_B", "reason": "B 理由",
    }, follow_redirects=False)
    assert r.status_code == 303
    page_b = client.get(f"/annotation/units/{uid}")
    assert "CO_B_CONCEPT" in page_b.text and "CO_A_CONCEPT" not in page_b.text
    login(client, "annotator")
    page_a2 = client.get(f"/annotation/units/{uid}")
    assert "CO_A_CONCEPT" in page_a2.text and "CO_B_CONCEPT" not in page_a2.text

    # 组长开始对齐；对齐结论
    login(client, "admin")
    _ok(client.post(f"/annotation/batches/{bid}/state",
                       data={"action": "start_alignment"}, follow_redirects=False))
    login(client, "reviewer")
    page = client.get(f"/annotation/units/{uid}")
    assert page.status_code == 200 and "CO_A_CONCEPT" in page.text and "CO_B_CONCEPT" in page.text
    r = client.post(f"/annotation/units/{uid}/align", data={
        "conclusion": "aligned", "decision": "include",
        "concepts": "ALIGNED_CONCEPT\n共享概念", "constants": "",
        "operators": "ALIGNED_OP", "differences": "名称统一", "reason": "一致",
        "concept_rows": json.dumps([
            {"name": "ALIGNED_CONCEPT", "definition": "对齐概念"},
            {"name": "共享概念", "definition": "共享概念"},
        ], ensure_ascii=False),
        "operator_rows": json.dumps([
            {"name": "ALIGNED_OP", "subject": "ALIGNED_CONCEPT", "return_type": "String"},
        ], ensure_ascii=False),
    }, follow_redirects=False)
    assert r.status_code == 303

    # 本体合并 → 自动应用对齐 → 版本冻结/发布 → KE
    login(client, "admin")
    _ok(client.post(f"/annotation/batches/{bid}/state",
                       data={"action": "start_merge"}, follow_redirects=False))
    _ok(client.post(f"/annotation/batches/{bid}/apply", data={
        "label": "TEST-CO_M-" + uuid.uuid4().hex[:6],
    }, follow_redirects=False))
    db = _db()
    try:
        version = db.scalar(select(OntologyVersion).where(OntologyVersion.batch_id == bid)
                            .order_by(OntologyVersion.id.desc()))
        vid = version.id
        assert version.validation and version.validation.get("ok"), version.validation
        from app.models import Concept, Operator
        assert db.scalar(select(Concept).where(Concept.name == "ALIGNED_CONCEPT"))
        assert db.scalar(select(Operator).where(Operator.name == "ALIGNED_OP"))
    finally:
        db.close()
    _ok(client.post(f"/annotation/versions/{vid}/freeze", follow_redirects=False))
    _ok(client.post(f"/annotation/versions/{vid}/release", follow_redirects=False))
    _ok(client.post(f"/annotation/batches/{bid}/state",
                       data={"action": "start_ke"}, follow_redirects=False))

    # A/B 执行 SQL + 提交 KE
    for username, side in (("annotator", "A"), ("annotator2", "B")):
        login(client, username)
        r = client.post(f"/annotation/units/{uid}/run-query", data={"side": side},
                        follow_redirects=False)
        _ok(r)
        r = client.post(f"/annotation/units/{uid}/ke", data={
            "side": side, "content": f"KE_{side} = True",
            "execution": '{"ok": true}', "self_check": '{"ok": true}',
        }, follow_redirects=False)
        assert r.status_code == 303

    login(client, "admin")
    _ok(client.post(f"/annotation/batches/{bid}/state",
                       data={"action": "start_gold"}, follow_redirects=False))

    # GOLD GOLD（组长）
    login(client, "reviewer")
    r = client.post(f"/annotation/units/{uid}/gold", data={
        "source": "merged", "content": "GOLD_KE = True", "differences": "合并",
        "reason": "一致",
    }, follow_redirects=False)
    assert r.status_code == 303

    login(client, "admin")
    _ok(client.post(f"/annotation/batches/{bid}/state",
                       data={"action": "start_cross"}, follow_redirects=False))
    # 交叉检查
    login(client, "annotator3")
    r = client.post(f"/annotation/batches/{bid}/cross-review", data={
        "scope": "CO_M / GOLD", "conclusion": "pass", "findings": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    login(client, "admin")
    _ok(client.post(f"/annotation/batches/{bid}/state",
                       data={"action": "validate"}, follow_redirects=False))
    _ok(client.post(f"/annotation/batches/{bid}/state",
                       data={"action": "pass"}, follow_redirects=False))

    db = _db()
    try:
        b = db.get(AnnotationBatch, bid)
        assert b.state == "passed"
        assert b.finished_at is not None
    finally:
        db.close()

    # 归档 ZIP
    r = client.get(f"/annotation/batches/{bid}/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/zip")
    import io
    import zipfile
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {"batch.json", "units.json", "co_annotations.json", "gold_kes.json",
            "ontology_versions.json", "README.md"} <= names
