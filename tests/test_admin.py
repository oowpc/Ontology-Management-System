"""M11 验证：用户与组管理（admin）、组清单动态闭环、词表/列映射配置化。"""
from conftest import login


def test_user_admin_flow(client):
    login(client, "annotator")
    assert client.get("/admin/users").status_code == 403
    assert client.get("/api/admin/users").status_code == 403

    login(client, "admin")
    r = client.post("/api/admin/users", json={
        "username": "bob", "password": "bob123", "role": "annotator", "group_name": "四组"})
    assert r.status_code == 201
    uid = r.json()["id"]
    # 重复用户名 → 409（幂等）
    assert client.post("/api/admin/users", json={
        "username": "bob", "password": "x", "role": "annotator"}).status_code == 409

    # 新用户可登录，组生效
    assert client.post("/login", data={"username": "bob", "password": "bob123"},
                       follow_redirects=False).status_code == 303
    me = client.get("/api/me").json()
    assert me["role"] == "annotator" and me["group"] == "四组"

    # 改角色 + 重置密码
    login(client, "admin")
    r = client.patch(f"/api/admin/users/{uid}", json={"role": "reviewer"})
    assert r.status_code == 200 and r.json()["role"] == "reviewer"
    assert client.post(f"/api/admin/users/{uid}/password", json={"password": "newpw"}).status_code == 200
    client.cookies.clear()
    assert client.post("/login", data={"username": "bob", "password": "newpw"},
                       follow_redirects=False).status_code == 303


def test_groups_dynamic_after_user_created(client):
    """新组「四组」自动出现在组清单（上传/看板/泳道同口径）。"""
    login(client, "admin")
    client.post("/api/admin/users", json={
        "username": "carol", "password": "carol123", "role": "annotator", "group_name": "四组"})
    groups = client.get("/api/ke/groups").json()["groups"]
    assert "四组" in {g["name"] for g in groups}


def test_users_page(client):
    login(client, "admin")
    r = client.get("/admin/users")
    assert r.status_code == 200 and "用户与组管理" in r.text
