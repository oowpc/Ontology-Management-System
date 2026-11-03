"""统一校验：rule / operator / GOLD 留痕、API、阻断开关。"""
import json
import uuid

from conftest import login


def _concept(client, name):
    r = client.post("/api/concepts", json={"name": name, "domain": "测试"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_rule_validation_is_recorded(client):
    login(client, "admin")
    tag = uuid.uuid4().hex[:6]
    cid = _concept(client, f"校验规则概念{tag}")
    r = client.post("/api/rules", json={
        "name": f"校验规则{tag}", "domain": "测试", "status": "approved",
        "head": [{"lhs": {"symbol": "x", "belong_concepts": f"oms-c-{cid}"},
                  "rhs": None}],
    })
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    rows = client.get("/api/validation/results",
                      params={"object_type": "rule", "object_id": rid}).json()
    assert rows["total"] >= 1
    item = rows["items"][0]
    assert item["stage"] == "create" and item["ok"] is True
    full = client.get(f"/api/validation/results/{item['id']}").json()
    assert full["result"]["object_type"] == "rule"
    assert full["result"]["errors"] == []


def test_bad_rule_reference_is_warning_by_default(client):
    login(client, "admin")
    tag = uuid.uuid4().hex[:6]
    r = client.post("/api/rules", json={
        "name": f"悬空规则{tag}", "domain": "测试", "status": "approved",
        "head": [{"lhs": {"symbol": "x", "belong_concepts": "oms-c-999999"},
                  "rhs": None}],
    })
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    row = client.get("/api/validation/results", params={
        "object_type": "rule", "object_id": rid}).json()["items"][0]
    assert row["ok"] is False
    full = client.get(f"/api/validation/results/{row['id']}").json()
    assert any(x["kind"] == "unknown_concept" for x in full["result"]["errors"])


def test_validation_enforce_blocks_bad_rule(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "validation_enforce", True)
    login(client, "admin")
    tag = uuid.uuid4().hex[:6]
    r = client.post("/api/rules", json={
        "name": f"阻断规则{tag}", "domain": "测试", "status": "approved",
        "head": [{"lhs": {"symbol": "x", "belong_concepts": "oms-c-999999"},
                  "rhs": None}],
    })
    assert r.status_code == 422, r.text
    assert "validation" in r.json()["detail"]


def test_operator_validation_records_type_warning(client):
    login(client, "admin")
    tag = uuid.uuid4().hex[:6]
    cid = _concept(client, f"校验算子主体{tag}")
    r = client.post("/api/operators", json={
        "name": f"校验算子{tag}", "subject_concept_id": cid,
        "return_type": f"未入库类型{tag}",
    })
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    row = client.get("/api/validation/results", params={
        "object_type": "operator", "object_id": oid}).json()["items"][0]
    assert row["ok"] is True
    full = client.get(f"/api/validation/results/{row['id']}").json()
    assert any(x["kind"] == "unknown_type" for x in full["result"]["warnings"])


def test_import_records_operator_and_rule(client):
    login(client, "admin")
    tag = uuid.uuid4().hex[:6]
    cid = _concept(client, f"导入校验概念{tag}")
    payload = {
        "operators": [{"name": f"导入算子{tag}", "subject": f"导入校验概念{tag}",
                       "return_type": "Number"}],
        "rules": [{"name": f"导入规则{tag}", "status": "approved",
                   "head": [{"lhs": {"symbol": "x",
                                     "belong_concepts": f"oms-c-{cid}"},
                             "rhs": None}]}],
    }
    r = client.post("/api/import/json", content=json.dumps(payload, ensure_ascii=False),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    rows = client.get("/api/validation/results", params={"object_type": "rule"}).json()
    assert any(x["stage"] == "import" for x in rows["items"])


def test_gold_accept_and_finalize_validation(client):
    login(client, "admin")
    tag = uuid.uuid4().hex[:6]
    r = client.post("/api/gold/items", json={
        "article_code": "VAL", "ke_id": tag, "group_name": "测试",
        "ke_text": "这是无效 KE",
    })
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    row = client.get("/api/validation/results", params={
        "object_type": "gold_item", "object_id": iid}).json()["items"][0]
    assert row["ok"] is False and row["stage"] == "create"
    r = client.post(f"/api/gold/items/{iid}/status", json={"status": "gold"})
    assert r.status_code == 422, r.text


def test_validation_summary_and_revalidate(client):
    login(client, "admin")
    s = client.get("/api/validation/summary")
    assert s.status_code == 200
    assert isinstance(s.json(), dict)
    r = client.post("/api/validation/revalidate",
                    json={"object_type": "operator", "limit": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checked"] >= 1
    assert all(x["object_type"] == "operator" for x in body["items"])


def test_import_enforce_rolls_back_invalid_rule(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "validation_enforce", True)
    login(client, "admin")
    tag = uuid.uuid4().hex[:6]
    name = f"导入阻断规则{tag}"
    r = client.post("/api/import/json", json={
        "rules": [{"name": name, "status": "approved",
                   "head": [{"lhs": {"symbol": "x", "belong_concepts": "oms-c-999999"},
                             "rhs": None}]}],
    })
    assert r.status_code == 422, r.text
    assert client.get("/api/rules", params={"q": name}).json()["total"] == 0


def test_validation_ops_page(client):
    login(client, "admin")
    page = client.get("/validation")
    assert page.status_code == 200
    assert "校验运营" in page.text
    r = client.post("/validation/revalidate", data={"object_type": "operator"},
                   follow_redirects=False)
    assert r.status_code == 303
