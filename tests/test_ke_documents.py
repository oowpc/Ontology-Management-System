"""统一 KE 文档：GOLD 创建/修订/状态与 API。"""
import uuid

from conftest import login


def test_gold_creates_unified_document_and_versions(client):
    login(client, "admin")
    tag = uuid.uuid4().hex[:6]
    r = client.post("/api/gold/items", json={
        "article_code": f"DOC-{tag}", "ke_id": f"KE-{tag}",
        "group_name": "测试", "ke_text": "output = 1;",
    })
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    items = client.get("/api/ke/documents", params={"source_type": "gold"}).json()["items"]
    doc = next(x for x in items if x["gold_item_id"] == gid)
    assert doc["ke_text"] == "output = 1;" and doc["status"] == "candidate"
    detail = client.get(f"/api/ke/documents/{doc['id']}").json()
    assert detail["versions"][0]["version"] == 1
    r = client.patch(f"/api/gold/items/{gid}", json={"ke_text": "output = 2;"})
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/ke/documents/{doc['id']}").json()
    assert len(detail["versions"]) == 2 and detail["ke_text"] == "output = 2;"
    r = client.post(f"/api/gold/items/{gid}/status", json={"status": "gold"})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/ke/documents/{doc['id']}").json()["status"] == "gold"
    page = client.get("/ke/documents")
    assert page.status_code == 200
