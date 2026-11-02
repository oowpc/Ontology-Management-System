"""服务间登录校验：KEOL 控制台复用 OMS 账号。"""
from conftest import login


def test_api_login_and_failure(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"
    r = client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
    assert r.status_code == 401
    # API 登录不创建浏览器 session
    assert "/logout" not in client.get("/").text
