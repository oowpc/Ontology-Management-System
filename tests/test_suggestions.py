"""KEOL/agent 本体建议箱：pending → 审批 → 全局；驳回与权限。"""
import uuid

from conftest import login


def _package(tag):
    ca, cb = f"建议概念A-{tag}", f"建议概念B-{tag}"
    op = f"建议算子-{tag}"
    rule = f"建议规则-{tag}"
    ind = f"建议个体-{tag}"
    pkg = {
        "concepts": [
            {"keol_id": "c1", "name": ca, "domain": "测试", "status": "approved"},
            {"keol_id": "c2", "name": cb, "domain": "测试", "status": "approved"},
        ],
        "operators": [
            {"keol_id": "op1", "name": op, "subject": ca, "return_type": cb,
             "param_types": [], "status": "approved"},
        ],
        "rules": [
            {"keol_id": "r1", "name": rule, "body": None,
             "head": [{"lhs": {"operator": "op1", "arguments": ["x"]}, "rhs": None}],
             "sources": ["keol-test"]},
        ],
        "individuals": [
            {"keol_id": "i1", "name": ind, "concept": ca, "sources": ["keol-test"]},
        ],
    }
    refs = {
        "concepts": {"c1": ca, "c2": cb},
        "operators": {"op1": {"name": op, "subject": ca}},
    }
    return pkg, refs, ca, cb, op, rule, ind


def test_suggestion_approve_applies_global(client):
    tag = uuid.uuid4().hex[:6]
    pkg, refs, ca, cb, op, rule, ind = _package(tag)
    login(client, "reviewer")
    r = client.post("/api/keol/suggestions", json={
        "title": f"建议包 {tag}", "origin": "keol", "package": pkg, "refs": refs,
    })
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["status"] == "pending"
    assert client.get("/api/keol/suggestions").json()["total"] >= 1
    assert client.get("/suggestions").status_code == 200
    assert client.get(f"/api/keol/suggestions/{sid}").json()["package"]["concepts"]
    assert client.get(f"/suggestions/{sid}").status_code == 200

    r = client.post(f"/api/keol/suggestions/{sid}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert client.get(f"/suggestions/{sid}").status_code == 200
    from app.db import SessionLocal
    from app.models import Concept, OntologyIndividual, OntologyRule, Operator
    from sqlalchemy import select
    db = SessionLocal()
    try:
        assert db.scalar(select(Concept).where(Concept.name == ca))
        assert db.scalar(select(Concept).where(Concept.name == cb))
        assert db.scalar(select(Operator).where(Operator.name == op))
        assert db.scalar(select(OntologyRule).where(OntologyRule.name == rule))
        assert db.scalar(select(OntologyIndividual).where(OntologyIndividual.name == ind))
    finally:
        db.close()


def test_suggestion_reject_and_permissions(client):
    tag = uuid.uuid4().hex[:6]
    pkg, refs, ca, *_ = _package(tag)
    login(client, "annotator")
    assert client.post("/api/keol/suggestions", json={
        "package": pkg, "refs": refs,
    }).status_code == 403

    login(client, "admin")
    r = client.post("/api/keol/suggestions", json={"package": pkg, "refs": refs})
    sid = r.json()["id"]
    assert client.post(f"/api/keol/suggestions/{sid}/reject",
                       json={"note": "不需要"}).status_code == 200
    assert client.get(f"/api/keol/suggestions/{sid}").json()["status"] == "rejected"
    from app.db import SessionLocal
    from app.models import Concept
    from sqlalchemy import select
    db = SessionLocal()
    try:
        assert not db.scalar(select(Concept).where(Concept.name == ca))
    finally:
        db.close()
