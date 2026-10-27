"""来源元数据 / 导入批次回滚 / 多父类提交与图边。"""
import json
from pathlib import Path

from conftest import concept_id_by_name, login


def _mk(client, name, parents=None, **extra):
    payload = {"name": name, **extra}
    if parents is not None:
        payload["parents"] = parents
    r = client.post("/api/concepts", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_concept_provenance_roundtrip(client):
    login(client, "admin")
    cid = _mk(client, "来源验证概念", definition="来源验证定义",
              aliases=["来源别名"], source_uri="https://example.org/SourceConcept",
              ontology_module="ExampleModule")
    d = client.get(f"/api/concepts/{cid}").json()
    assert d["definition"] == "来源验证定义"
    assert d["aliases"] == ["来源别名"]
    assert d["source_uri"] == "https://example.org/SourceConcept"
    assert d["ontology_module"] == "ExampleModule"

    r = client.patch(f"/api/concepts/{cid}", json={"source_uri": "https://example.org/v2",
                                                   "ontology_module": "ExampleV2"})
    assert r.status_code == 200
    assert r.json()["source_uri"] == "https://example.org/v2"

    payload = client.get("/api/export/json").json()
    row = next(c for c in payload["concepts"] if c["name"] == "来源验证概念")
    assert row["source_uri"] == "https://example.org/v2"
    assert row["ontology_module"] == "ExampleV2"


def _batch_source(tmp_path: Path):
    area = tmp_path / "src" / "领域"
    area.mkdir(parents=True)
    md = area / "批次测试-CO-v0.1.md"
    md.write_text(
        "# 批次测试\n\n```keol\n"
        "concept 批次测试根\n"
        "concept 批次测试子 <: 批次测试根\n"
        "op 批次测试属性(批次测试子): String\n"
        "```\n", encoding="utf-8")
    meta = area / "批次测试-CO-v0.1.meta.json"
    meta.write_text(json.dumps({
        "concepts": {
            "批次测试根": {"uri": "https://example.org/BatchRoot",
                          "module": "BatchModule", "definition": "批次根定义", "aliases": ["根别名"]},
            "批次测试子": {"uri": "https://example.org/BatchChild",
                          "module": "BatchModule", "definition": "批次子定义", "aliases": []},
        },
        "operators": [
            {"name": "批次测试属性", "subject": "批次测试子",
             "uri": "https://example.org/batchProperty", "module": "BatchModule"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return str(tmp_path / "src")


def test_import_meta_batch_and_rollback(tmp_path):
    from app.batches import rollback_batch
    from app.db import SessionLocal, init_db
    from app.importer import import_payload
    from app.models import Concept, ImportBatch, Operator
    from scripts.sync_keol import build_payload
    from sqlalchemy import select

    init_db()
    src = _batch_source(tmp_path)
    payload, _stats = build_payload(src)
    db = SessionLocal()
    try:
        result = import_payload(db, payload, "test-batch-actor",
                                batch_id="test-batch-001", source=src, files=[])
        assert result["concepts_created"] == 2 and result["operators_created"] == 1
        c = db.scalar(select(Concept).where(Concept.name == "批次测试根"))
        assert c and c.definition == "批次根定义" and c.aliases == ["根别名"]
        assert c.source_uri == "https://example.org/BatchRoot"
        assert c.ontology_module == "BatchModule"
        o = db.scalar(select(Operator).where(Operator.name == "批次测试属性"))
        assert o and o.source_uri == "https://example.org/batchProperty"
        batch = db.scalar(select(ImportBatch).where(ImportBatch.batch_id == "test-batch-001"))
        assert batch and batch.status == "completed" and len(batch.created_concept_ids) == 2

        res = rollback_batch(db, "test-batch-001")
        assert res["status"] == "rolled_back"
        assert not db.scalar(select(Concept).where(Concept.name == "批次测试根"))
        assert not db.scalar(select(Operator).where(Operator.name == "批次测试属性"))
        batch = db.scalar(select(ImportBatch).where(ImportBatch.batch_id == "test-batch-001"))
        assert batch.status == "rolled_back"
    finally:
        db.close()


def test_rollback_blocks_external_reference(tmp_path):
    from app.batches import rollback_batch
    from app.db import SessionLocal, init_db
    from app.importer import import_payload
    from app.models import Concept
    from scripts.sync_keol import build_payload
    from sqlalchemy import select

    init_db()
    # 单概念批次
    area = tmp_path / "src2" / "领域"
    area.mkdir(parents=True)
    (area / "批次保护-CO-v0.1.md").write_text(
        "```keol\nconcept 批次保护根\n```\n", encoding="utf-8")
    payload, _ = build_payload(str(tmp_path / "src2"))
    db = SessionLocal()
    try:
        import_payload(db, payload, "test-batch-actor", batch_id="test-batch-002",
                       source="", files=[])
        root = db.scalar(select(Concept).where(Concept.name == "批次保护根"))
        child = Concept(name="批次外部子", parent_id=root.id, status="approved",
                        created_by="admin", reviewed_by="admin", domain="通用")
        db.add(child)
        db.flush()
        from app.models import ConceptParent
        db.add(ConceptParent(concept_id=child.id, parent_id=root.id,
                             is_primary=True, position=0))
        db.commit()

        res = rollback_batch(db, "test-batch-002")
        assert res["status"] == "blocked" and res["references"]
        assert db.scalar(select(Concept).where(Concept.name == "批次保护根"))
    finally:
        db.close()


def test_submission_multi_parent(client):
    login(client, "admin")
    a = _mk(client, "提交父A")
    b = _mk(client, "提交父B")
    r = client.post("/api/submissions", json={
        "name": "提交多父概念", "suggested_parent_id": a,
        "suggested_parent_ids": [a, b], "reason": "r", "sample_question": "q"})
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["suggested_parent_ids"] == [a, b]
    assert sub["suggested_parent_names"] == ["提交父A", "提交父B"]
    cid = sub["concept_id"]
    d = client.get(f"/api/concepts/{cid}").json()
    assert d["parent_names"] == ["提交父A", "提交父B"]
    assert "其他建议父类" in client.get("/submit").text


def test_graph_extra_parent_edges(client):
    login(client, "admin")
    a = _mk(client, "图父A")
    b = _mk(client, "图父B")
    x = _mk(client, "图子", [a, b])
    data = client.get("/api/graph").json()
    links = {tuple(sorted((l["source"], l["target"]))) for l in data["links"]}
    assert (min(a, x), max(a, x)) in links
    assert (min(b, x), max(b, x)) in links
    extra = [l for l in data["links"] if l["target"] == x and l.get("extra")]
    assert len(extra) == 1 and extra[0]["source"] == b
    assert client.get("/graph").status_code == 200


def test_concept_form_multi_parent(client):
    login(client, "admin")
    a = _mk(client, "表单父A")
    b = _mk(client, "表单父B")
    r = client.post("/concepts/new", data={
        "name": "表单多父子", "domain": "通用",
        "parent_id": str(a), "extra_parent_ids": [str(b)],
    }, follow_redirects=False)
    assert r.status_code == 303
    cid = concept_id_by_name(client, "表单多父子")
    d = client.get(f"/api/concepts/{cid}").json()
    assert d["parent_names"] == ["表单父A", "表单父B"]
