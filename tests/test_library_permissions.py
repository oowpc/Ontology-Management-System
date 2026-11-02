"""库级权限：无成员不可见；owner 可授权；库角色不高于账号角色。"""
from conftest import login


def _make_user(client, username, role):
    r = client.post("/api/admin/users", json={
        "username": username, "password": "pw123456", "role": role})
    assert r.status_code == 201, r.text
    return r.json()


def test_new_user_needs_membership_and_owner_can_grant(client):
    login(client, "admin")
    _make_user(client, "lonely", "annotator")
    client.cookies.clear()
    assert client.post("/login", data={"username": "lonely", "password": "pw123456"},
                       follow_redirects=False).status_code == 303
    assert client.get("/api/libraries").json()["total"] == 0
    assert client.get("/api/concepts").status_code == 403
    assert client.get("/libraries").status_code == 200

    login(client, "admin")
    r = client.post("/api/libraries/co-baseline/members", json={
        "username": "lonely", "role": "annotator"})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    client.post("/login", data={"username": "lonely", "password": "pw123456"},
                follow_redirects=False)
    assert client.get("/api/concepts").status_code == 200
    assert client.post("/api/concepts", json={"name": "权限测试概念"}).status_code == 403


def test_library_role_downgrades_global_reviewer(client):
    login(client, "admin")
    _make_user(client, "rev_limited", "reviewer")
    r = client.post("/api/libraries/co-baseline/members", json={
        "username": "rev_limited", "role": "annotator"})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    client.post("/login", data={"username": "rev_limited", "password": "pw123456"},
                follow_redirects=False)
    assert client.get("/api/concepts").status_code == 200
    assert client.post("/api/concepts", json={"name": "审查员降级概念"}).status_code == 403
    assert client.get("/api/libraries").json()["total"] >= 1


def test_member_management_page(client):
    login(client, "admin")
    r = client.get("/libraries")
    assert r.status_code == 200 and "本体库与权限" in r.text
    r = client.get("/api/libraries/co-baseline/members")
    assert r.status_code == 200
    assert any(x["username"] == "reviewer" and x["role"] == "owner"
               for x in r.json()["items"])


def test_library_scoped_models_are_registered():
    """所有带 library_id 的模型都必须进入 db._library_classes，防止查询漏过滤。"""
    import inspect
    from app import models
    from app.db import Base, _library_classes

    expected = {
        name for name, obj in inspect.getmembers(models, inspect.isclass)
        if issubclass(obj, Base) and obj is not Base
        and hasattr(obj, "library_id")
    }
    actual = {c.__name__ for c in _library_classes()}
    assert expected - actual == set()


def test_library_scoped_service_entrypoints():
    from app.library_context import current_library_id, library_scoped

    @library_scoped
    def _probe(library_id=None):
        return current_library_id.get()

    assert _probe(library_id=4) == 4
    assert _probe() != 4
