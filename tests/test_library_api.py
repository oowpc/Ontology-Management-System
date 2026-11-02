"""Agent API：全局规则/个体 CRUD 与 manifest 暴露。"""
import uuid

from conftest import login


def test_library_api_and_manifest(client):
    login(client, "admin")
    suffix = uuid.uuid4().hex[:6]
    r = client.post("/api/concepts", json={"name": f"API规则概念{suffix}", "domain": "测试"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = client.post("/api/rules", json={
        "name": f"API规则{suffix}", "domain": "测试",
        "body": None,
        "head": [{"lhs": {"symbol": "x", "belong_concepts": f"oms-c-{cid}"},
                  "rhs": None}],
        "sources": ["api-test"],
    })
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    r = client.get(f"/api/rules/{rid}")
    assert r.status_code == 200 and r.json()["name"] == f"API规则{suffix}"
    r = client.patch(f"/api/rules/{rid}", json={"description": "patched"})
    assert r.status_code == 200 and r.json()["description"] == "patched"
    assert client.get("/api/rules", params={"q": suffix}).json()["total"] == 1

    r = client.post("/api/individuals", json={
        "name": f"API个体{suffix}", "concept_id": cid, "sources": ["api-test"],
    })
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    assert client.get(f"/api/individuals/{iid}").json()["concept"] == f"API规则概念{suffix}"
    assert client.patch(f"/api/individuals/{iid}", json={"description": "patched"}).status_code == 200
    assert client.get("/api/individuals", params={"concept_id": cid}).json()["total"] == 1

    export = client.get("/api/export/json").json()
    assert any(x["name"] == f"API规则{suffix}" for x in export["rules"])
    assert any(x["name"] == f"API个体{suffix}" for x in export["individuals"])

    d = client.get("/api/agent/manifest").json()
    flat = [e for v in d["endpoints"].values() for e in v]
    assert any("/api/annotation/batches" in e for e in flat)
    assert any("/api/rules" in e for e in flat)
    assert any("/api/individuals" in e for e in flat)
    assert any(w["name"] == "标注批次全流程" for w in d["workflows"])
