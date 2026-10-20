"""Agent 接入：API Token 签发 / Bearer 使用 / 吊销 / 权限。"""
from conftest import login


def test_mint_and_use_bearer(client):
    login(client, "annotator")
    r = client.post("/api/tokens", json={"name": "ci-bot"})
    assert r.status_code == 201
    token = r.json()["token"]
    assert token.startswith("ke_")

    client.cookies.clear()  # 去掉 session，模拟纯 agent 访问
    r = client.get("/api/ke/groups", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and "groups" in r.json()
    # 错误 token → 401
    r = client.get("/api/ke/groups", headers={"Authorization": "Bearer ke_bad"})
    assert r.status_code == 401

    # 吊销后失效（先登录回 admin 页面通道查询 id）
    login(client, "annotator")
    tid = client.get("/api/tokens").json()["items"][0]["id"]
    assert client.delete(f"/api/tokens/{tid}").status_code == 200
    client.cookies.clear()
    assert client.get("/api/ke/groups",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_mint_for_others_admin_only(client):
    login(client, "annotator")
    assert client.post("/api/tokens", json={"username": "reviewer"}).status_code == 403
    login(client, "admin")
    r = client.post("/api/tokens", json={"username": "reviewer", "name": "review-bot"})
    assert r.status_code == 201 and r.json()["username"] == "reviewer"


def test_token_list_scoped(client):
    login(client, "annotator")
    mine = client.get("/api/tokens").json()["items"]
    assert all(t["username"] == "annotator" for t in mine)
