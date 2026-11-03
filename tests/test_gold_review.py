"""GOLD 草稿审查工作台与库隔离。"""
import uuid

from conftest import login


def test_gold_review_workbench_and_bulk(client):
    login(client, "admin")
    tag = uuid.uuid4().hex[:6]
    r = client.post("/api/gold/items", json={
        "article_code": f"REV-{tag}", "ke_id": f"KE-{tag}",
        "group_name": "测试", "ke_text": "output = 1;",
    })
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    page = client.get("/gold/review")
    assert page.status_code == 200
    assert f"KE-{tag}" in page.text
    r = client.post("/api/gold/review/bulk", json={
        "ids": [iid], "action": "reviewed"})
    assert r.status_code == 200, r.text
    assert r.json()["done"] == [iid]
    assert client.get(f"/api/gold/items/{iid}").json()["status"] == "reviewed"


def test_gold_isolation_by_library(client):
    login(client, "admin")
    tag = uuid.uuid4().hex[:6]
    r = client.post("/api/gold/items", json={
        "article_code": f"ISO-{tag}", "ke_id": f"KE-{tag}",
        "group_name": "测试", "ke_text": "output = 1;",
    })
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    r = client.get("/api/gold/items", params={"article_code": f"ISO-{tag}"})
    assert r.json()["total"] == 1
    client.post("/api/libraries", json={"key": "archer", "name": "Archer", "kind": "reference"})
    sw = client.get("/library/switch", params={"key": "archer", "next": "/gold/review"},
                    follow_redirects=False)
    assert sw.status_code == 303
    assert client.get("/api/gold/items", params={"article_code": f"ISO-{tag}"}).json()["total"] == 0
    assert client.get(f"/api/gold/items/{iid}").status_code == 404
    client.get("/library/switch", params={"key": "co-baseline", "next": "/gold/review"},
               follow_redirects=False)


def test_issue_isolation_by_library(client):
    login(client, "admin")
    tag = uuid.uuid4().hex[:6]
    r = client.post("/api/issues", json={
        "title": f"隔离 issue {tag}", "type_tag": "模糊表达建模",
        "description": "例子 → 矛盾 → 提问", "domain": "测试",
        "cases": [{"source": "测试", "original_text": "原文", "variant_a": "A"}],
    })
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    assert client.get(f"/api/issues/{iid}").status_code == 200
    client.post("/api/libraries", json={"key": "archer", "name": "Archer", "kind": "reference"})
    client.get("/library/switch", params={"key": "archer", "next": "/issues"},
               follow_redirects=False)
    assert client.get(f"/api/issues/{iid}").status_code == 404
    client.get("/library/switch", params={"key": "co-baseline", "next": "/issues"},
               follow_redirects=False)
