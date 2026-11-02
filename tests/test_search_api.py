"""P2：全局搜索 API。"""
from conftest import login


def test_search_api(client):
    login(client, "admin")
    r = client.get("/api/search", params={"q": "机构"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"] >= 1
    assert d["items"]["concepts"] and d["items"]["concepts"][0]["id"]
    r = client.get("/api/search", params={"q": "机构", "types": "concepts"})
    assert set(r.json()["items"]) == {"concepts"}
    assert client.get("/api/search", params={"q": "机构", "types": "bad"}).status_code == 400
    assert client.get("/api/search").status_code == 422
