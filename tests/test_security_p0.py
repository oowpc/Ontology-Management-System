"""P0 安全加固：登录防爆破锁定 / CSRF 同步器 token / SECRET_KEY 启动校验。

CSRF 用例通过 monkeypatch 动态打开 settings.csrf_enabled（conftest 全局关闭，
以免既有表单用例逐个带 token）。
"""
import re

import pytest

from conftest import login


# ---------------- 登录防爆破 ----------------

def test_login_lockout(client):
    from app import security
    security._LOGIN_FAILS.clear()
    try:
        for _ in range(security.LOGIN_MAX_FAILS):
            r = client.post("/login", data={"username": "admin", "password": "wrong"})
            assert r.status_code == 401
        # 达到阈值后，正确密码也被锁
        r = client.post("/login", data={"username": "admin", "password": "admin"})
        assert r.status_code == 429
        assert "锁定" in r.text
        assert security.login_lock_remaining("admin") > 0
        # 锁定按用户名隔离，别的账号不受影响
        r = client.post("/login", data={"username": "reviewer", "password": "reviewer"},
                        follow_redirects=False)
        assert r.status_code == 303
    finally:
        security._LOGIN_FAILS.clear()
    assert security.login_lock_remaining("admin") == 0


def test_login_success_clears_fails(client):
    from app import security
    security._LOGIN_FAILS.clear()
    try:
        for _ in range(security.LOGIN_MAX_FAILS - 1):
            client.post("/login", data={"username": "annotator", "password": "wrong"})
        r = client.post("/login", data={"username": "annotator", "password": "annotator"},
                        follow_redirects=False)
        assert r.status_code == 303                       # 未触锁
        assert security.login_lock_remaining("annotator") == 0  # 成功清零计数
    finally:
        security._LOGIN_FAILS.clear()


# ---------------- CSRF ----------------

def _csrf_from(html: str) -> str:
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert m, "页面未注入 csrf-token meta"
    return m.group(1)


@pytest.fixture()
def csrf_on(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "csrf_enabled", True)
    return client


def test_csrf_blocks_form_without_token(csrf_on):
    token = _csrf_from(csrf_on.get("/login").text)
    assert token
    # 无 token → 403
    r = csrf_on.post("/login", data={"username": "admin", "password": "admin"})
    assert r.status_code == 403
    assert "CSRF" in r.json()["detail"]
    # 带 token（模拟 base.html 注入的隐藏域）→ 过闸，登录成功
    r = csrf_on.post("/login", data={"username": "admin", "password": "admin",
                                     "csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303


def test_csrf_header_and_bearer_exempt(csrf_on):
    token = _csrf_from(csrf_on.get("/login").text)
    r = csrf_on.post("/login", data={"username": "admin", "password": "admin",
                                     "csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303
    # Cookie 会话调 API 写操作：无 token → 403；带 X-CSRF-Token 头 → 放行
    assert csrf_on.post("/api/tokens", json={"name": "csrf-test"}).status_code == 403
    r = csrf_on.post("/api/tokens", json={"name": "csrf-test"},
                     headers={"X-CSRF-Token": token})
    assert r.status_code == 201, r.text
    api_token = r.json()["token"]
    # Bearer 通道无 Cookie 会话，天然免疫，直接放行
    r = csrf_on.post("/api/tokens", json={"name": "csrf-test-2"},
                     headers={"Authorization": f"Bearer {api_token}"})
    assert r.status_code == 201, r.text


def test_csrf_multipart_field(csrf_on):
    """multipart 表单的 token 提取（KE 上传路径；JS 会把隐藏域追加到文件字段之后）。"""
    from app.csrf import extract_token
    boundary = "----testboundary"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.csv\"\r\n"
        f"Content-Type: text/csv\r\n\r\n题目ID,标注状态\nK1,已完成\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"csrf_token\"\r\n\r\n"
        f"tok123\r\n--{boundary}--\r\n"
    ).encode()
    headers = [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())]
    assert extract_token(headers, body) == "tok123"
    assert extract_token(headers, b"--x--\r\n") == ""               # 无字段 → 空
    urlenc = [(b"content-type", b"application/x-www-form-urlencoded")]
    assert extract_token(urlenc, b"a=1&csrf_token=tok2") == "tok2"  # urlencoded 路径


# ---------------- SECRET_KEY 启动校验 ----------------

def test_secret_key_guard():
    from app.config import DEFAULT_SECRET, ensure_secret_key
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        ensure_secret_key(DEFAULT_SECRET, dev_mode=False)   # 非调试 + 默认密钥 → 拒启动
    ensure_secret_key(DEFAULT_SECRET, dev_mode=True)        # 调试模式放行
    ensure_secret_key("real-secret", dev_mode=False)        # 已配置密钥放行
