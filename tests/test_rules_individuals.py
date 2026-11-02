"""全局规则/个体：OMS 页面、导入导出、桥接载荷。"""
import json

from conftest import concept_id_by_name, login


def _create_concept(client, name):
    r = client.post("/api/concepts", json={"name": name, "domain": "测试"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_rule_and_individual_pages_and_export(client):
    login(client, "admin")
    cid = _create_concept(client, "规则个体测试概念")
    head = [{"lhs": {"symbol": "测试常量", "belong_concepts": f"oms-c-{cid}"}, "rhs": None}]
    r = client.post("/rules/new", data={
        "name": "规则个体测试规则", "domain": "测试", "description": "d",
        "body_text": "", "head_text": json.dumps(head, ensure_ascii=False),
        "sources_text": "test", "supply_text": "{}", "status": "approved",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text
    rid = int(r.headers["location"].rsplit("/", 1)[-1])
    assert client.get(f"/rules/{rid}").status_code == 200
    assert client.get(f"/rules/{rid}/edit").status_code == 200
    assert client.get("/rules").status_code == 200
    r = client.post(f"/rules/{rid}/edit", data={
        "name": "规则个体测试规则", "domain": "测试", "description": "d2",
        "body_text": "", "head_text": json.dumps(head, ensure_ascii=False),
        "sources_text": "test", "supply_text": "{}", "status": "approved",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text

    r = client.post("/individuals/new", data={
        "name": "规则个体测试个体", "concept_id": str(cid), "domain": "测试",
        "description": "", "sources_text": "", "supply_text": "{}", "status": "approved",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text
    iid = int(r.headers["location"].rsplit("/", 1)[-1])
    assert client.get(f"/individuals/{iid}").status_code == 200
    assert client.get(f"/individuals/{iid}/edit").status_code == 200
    assert client.get("/individuals").status_code == 200
    r = client.post(f"/individuals/{iid}/edit", data={
        "name": "规则个体测试个体", "concept_id": str(cid), "domain": "测试",
        "description": "d2", "sources_text": "", "supply_text": "{}",
        "status": "approved",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text

    payload = client.get("/api/export/json").json()
    rule = next(x for x in payload["rules"] if x["name"] == "规则个体测试规则")
    ind = next(x for x in payload["individuals"] if x["name"] == "规则个体测试个体")
    assert rule["head"][0]["lhs"]["belong_concepts"] == f"oms-c-{cid}"
    assert ind["concept"] == "规则个体测试概念" and ind["concept_id"] == cid


def test_import_empty_rule_individual_and_idempotent(client):
    login(client, "admin")
    cid = _create_concept(client, "规则导入概念")
    payload = {
        "rules": [{"name": "规则导入测试", "body": None,
                   "head": [{"lhs": {"symbol": "x",
                                     "belong_concepts": f"oms-c-{cid}"}, "rhs": None}]}],
        "individuals": [{"name": "规则导入个体", "concept": "规则导入概念"}],
    }
    r1 = client.post("/api/import/json", content=json.dumps(payload, ensure_ascii=False),
                     headers={"Content-Type": "application/json"}).json()
    assert r1["rules_created"] == 1 and r1["individuals_created"] == 1
    r2 = client.post("/api/import/json", content=json.dumps(payload, ensure_ascii=False),
                     headers={"Content-Type": "application/json"}).json()
    assert r2["rules_created"] == 0 and r2["rules_unchanged"] == 1
    assert r2["individuals_created"] == 0 and r2["individuals_unchanged"] == 1


def test_ontology_validation_includes_rules_individuals(client):
    from app.annotation.service import validate_ontology
    from app.db import SessionLocal

    login(client, "admin")
    cid = _create_concept(client, "校验规则概念")
    client.post("/api/import/json", content=json.dumps({
        "rules": [{"name": "校验规则", "head": [
            {"lhs": {"symbol": "x", "belong_concepts": f"oms-c-{cid}"}, "rhs": None}]}],
        "individuals": [{"name": "校验个体", "concept": "校验规则概念"}],
    }, ensure_ascii=False), headers={"Content-Type": "application/json"})
    db = SessionLocal()
    try:
        v = validate_ontology(db)
    finally:
        db.close()
    assert v["ok"], v
    assert v["rules"] > 0 and v["individuals"] > 0
