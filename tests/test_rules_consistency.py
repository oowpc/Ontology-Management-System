"""规则一致性 API / 页面：依赖环、结构检查。"""
import uuid

from conftest import login


def test_rule_consistency_detects_dependency_cycle(client):
    login(client, "admin")
    tag = uuid.uuid4().hex[:6]
    c = client.post("/api/concepts", json={"name": f"规则一致概念{tag}", "domain": "测试"})
    assert c.status_code == 201, c.text
    cid = c.json()["id"]
    ops = []
    for suffix in ("A", "B"):
        r = client.post("/api/operators", json={
            "name": f"规则算子{suffix}{tag}", "subject_concept_id": cid,
            "return_type": "Bool",
        })
        assert r.status_code == 201, r.text
        ops.append(r.json()["id"])
    for i, (body_op, head_op) in enumerate((
            (ops[0], ops[1]), (ops[1], ops[0]))):
        r = client.post("/api/rules", json={
            "name": f"环规则{i}{tag}", "status": "approved",
            "body": {"lhs": {"operator": f"oms-op-{body_op}",
                             "arguments": ["x"]}, "rhs": None},
            "head": [{"lhs": {"operator": f"oms-op-{head_op}",
                              "arguments": ["x"]}, "rhs": None}],
        })
        assert r.status_code == 201, r.text
    r = client.get("/api/rules/consistency")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["cycles"]
    assert any(x["kind"] == "rule_cycle" for x in body["errors"])
    page = client.get("/rules/consistency")
    assert page.status_code == 200
    assert "规则一致性" in page.text
    assert "规则依赖环" in page.text
