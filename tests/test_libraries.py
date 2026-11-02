"""本体库隔离：同库同名、跨库不可见、库切换与标注批次边界。"""
from conftest import login


def _switch(client, key):
    r = client.get("/library/switch", params={"key": key, "next": "/"}, follow_redirects=False)
    assert r.status_code == 303, r.text


def test_library_catalog_and_switch(client):
    login(client, "admin")
    r = client.get("/api/libraries")
    assert r.status_code == 200
    keys = {x["key"] for x in r.json()["items"]}
    assert {"fibo-cmns", "co-baseline", "pilot-yuer"} <= keys
    base_total = client.get("/api/concepts").json()["total"]
    assert base_total >= 20
    _switch(client, "pilot-yuer")
    pilot_total = client.get("/api/concepts").json()["total"]
    assert pilot_total >= 0
    assert client.get("/api/concepts", headers={"X-Ontology-Library": "co-baseline"}).json()["total"] == base_total
    assert client.get("/api/concepts", headers={"X-Ontology-Library": "missing"}).status_code == 400


def test_same_name_allowed_in_separate_libraries(client):
    login(client, "admin")
    _switch(client, "co-baseline")
    before = client.get("/api/concepts").json()["total"]
    _switch(client, "pilot-yuer")
    r = client.post("/api/concepts", json={"name": "机构", "definition": "pilot 机构"})
    assert r.status_code == 201, r.text
    pilot_id = r.json()["id"]
    assert client.get("/api/concepts").json()["total"] == 1
    assert client.get(f"/api/concepts/{pilot_id}").status_code == 200

    _switch(client, "co-baseline")
    assert client.get("/api/concepts").json()["total"] == before
    assert client.get(f"/api/concepts/{pilot_id}").status_code == 404
    names = [x["name"] for x in client.get("/api/concepts", params={"q": "机构"}).json()["items"]]
    assert "机构" in names

    # 直接 Session / db.get 也按库校验
    from app.db import SessionLocal
    from app.library_context import library_scope
    from app.models import Concept
    from sqlalchemy import select
    with library_scope(3):
        db = SessionLocal()
        try:
            assert db.get(Concept, pilot_id).definition == "pilot 机构"
            assert db.get(Concept, 1) is None
            assert db.scalar(select(Concept).where(Concept.name == "机构")).id == pilot_id
        finally:
            db.close()


def test_rules_versions_and_annotation_batches_are_scoped(client):
    login(client, "admin")
    _switch(client, "pilot-yuer")
    r = client.post("/api/rules", json={
        "name": "pilot-rule", "body": None,
        "head": [{"lhs": "x", "rhs": "y"}], "status": "approved",
    })
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert client.get("/api/rules").json()["total"] == 1

    r = client.post("/api/annotation/batches", json={
        "batch_id": "PILOT-ISO-1", "name": "隔离批次",
        "source_name": "x", "source_version": "v1", "source_checksum": "x",
        "source_type": "policy", "domain": "测试", "guide_version": "v1",
    })
    assert r.status_code == 201, r.text
    bid = r.json()["id"]

    _switch(client, "co-baseline")
    assert not any(x["name"] == "pilot-rule" for x in client.get("/api/rules").json()["items"])
    assert client.get(f"/api/rules/{rid}").status_code == 404
    assert "PILOT-ISO-1" not in [x["batch_id"] for x in client.get("/api/annotation/batches").json()["items"]]
    assert client.get(f"/api/annotation/batches/{bid}").status_code == 404

    _switch(client, "pilot-yuer")
    assert any(x["name"] == "pilot-rule" for x in client.get("/api/rules").json()["items"])
    assert client.get(f"/api/annotation/batches/{bid}").status_code == 200
