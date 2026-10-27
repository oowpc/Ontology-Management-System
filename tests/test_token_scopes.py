"""P1：API Token 最小权限（scopes）与 last_used_at 写入节流。"""
from conftest import login


def _mint(client, scopes=None, name="p1-test"):
    payload = {"name": name}
    if scopes is not None:
        payload["scopes"] = scopes
    r = client.post("/api/tokens", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _last_used(tok_plain):
    import hashlib

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import ApiToken
    h = hashlib.sha256(tok_plain.encode()).hexdigest()
    db = SessionLocal()
    try:
        return db.scalar(select(ApiToken.last_used_at).where(ApiToken.token_hash == h))
    finally:
        db.close()


def test_read_only_token_cannot_write(client):
    login(client, "admin")
    tok = _mint(client, ["read"])["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/concepts", headers=h).status_code == 200       # 只读放行
    r = client.post("/api/tokens", json={"name": "x"}, headers=h)          # 写操作拦截
    assert r.status_code == 403 and "scope" in r.json()["detail"]
    r = client.delete("/api/tokens/1", headers=h)
    assert r.status_code == 403


def test_full_token_writes(client):
    login(client, "admin")
    tok = _mint(client)["token"]          # 缺省 = 读写全权限（兼容旧行为）
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/concepts", headers=h).status_code == 200
    assert client.post("/api/tokens", json={"name": "via-full"}, headers=h).status_code == 201


def test_invalid_scopes_rejected(client):
    login(client, "admin")
    r = client.post("/api/tokens", json={"name": "bad", "scopes": ["root"]})
    assert r.status_code == 400
    r = client.post("/api/tokens", json={"name": "bad", "scopes": []})
    assert r.status_code == 400


def test_last_used_throttled(client, monkeypatch):
    login(client, "admin")
    tok = _mint(client, name="throttle-test")["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/me", headers=h).status_code == 200
    t1 = _last_used(tok)
    assert t1 is not None                  # 首次调用写库
    assert client.get("/api/me", headers=h).status_code == 200
    assert _last_used(tok) == t1           # 阈值内第二次：不写（节流生效）

    from app import security
    monkeypatch.setattr(security, "TOKEN_LAST_USED_THROTTLE", 0)
    assert client.get("/api/me", headers=h).status_code == 200
    assert _last_used(tok) > t1            # 阈值置 0：每次调用都写
