"""M1 骨架验证：登录、概念 CRUD、算子 CRUD、页面渲染、角色限制。"""
from conftest import concept_id_by_name, login


def test_api_requires_login(client):
    assert client.get("/api/concepts").status_code == 401


def test_page_requires_login(client):
    r = client.get("/concepts", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]


def test_admin_login_and_seed(client):
    r = login(client, "admin")
    assert r.status_code == 303
    data = client.get("/api/concepts").json()
    assert data["total"] >= 20
    names = {i["name"] for i in data["items"]}
    assert {"机构", "行政区划", "地方政府", "领导职务"} <= names
    # 列表带使用统计
    ji = next(i for i in data["items"] if i["name"] == "机构")
    assert ji["ke_count"] == 128


def test_wrong_password_rejected(client):
    r = login(client, "admin", "wrong-password")
    assert r.status_code == 401


def test_annotator_cannot_write(client):
    login(client, "annotator")
    assert client.post("/api/concepts", json={"name": "越权概念"}).status_code == 403
    assert client.get("/api/concepts").status_code == 200  # 读不受限


def test_concept_crud_flow(client):
    login(client, "admin")
    jid = concept_id_by_name(client, "机构")
    r = client.post("/api/concepts", json={
        "name": "测试概念甲", "aliases": ["测试甲"], "definition": "验收用", "parent_id": jid,
    })
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["parent_name"] == "机构"
    assert r.json()["status"] == "approved"

    d = client.get(f"/api/concepts/{cid}").json()
    assert d["name"] == "测试概念甲" and d["parent_name"] == "机构"

    r = client.patch(f"/api/concepts/{cid}", json={"aliases": ["测试甲", "甲测试"], "definition": "v2"})
    assert r.json()["aliases"] == ["测试甲", "甲测试"]
    assert r.json()["definition"] == "v2"

    r = client.patch(f"/api/concepts/{cid}", json={"parent_id": None})
    assert r.json()["parent_id"] is None

    assert client.post(f"/api/concepts/{cid}/disable").json()["status"] == "disabled"
    assert client.post(f"/api/concepts/{cid}/restore").json()["status"] == "approved"


def test_duplicate_name_conflict(client):
    login(client, "admin")
    assert client.post("/api/concepts", json={"name": "机构"}).status_code == 409


def test_cycle_rejected(client):
    login(client, "admin")
    a = client.post("/api/concepts", json={"name": "循环甲"}).json()["id"]
    b = client.post("/api/concepts", json={"name": "循环乙", "parent_id": a}).json()["id"]
    assert client.patch(f"/api/concepts/{a}", json={"parent_id": b}).status_code == 400


def test_operator_crud_flow(client):
    login(client, "admin")
    jid = concept_id_by_name(client, "机构")
    r = client.post("/api/operators", json={
        "name": "测试算子", "subject_concept_id": jid,
        "param_types": ["行政区划"], "return_type": "String",
    })
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    assert r.json()["subject_name"] == "机构"
    assert r.json()["owner_name"] == "机构"  # 缺省所属 = 主体

    dup = client.post("/api/operators", json={
        "name": "测试算子", "subject_concept_id": jid, "return_type": "String",
    })
    assert dup.status_code == 409

    r = client.patch(f"/api/operators/{oid}", json={"return_type": "Number"})
    assert r.json()["return_type"] == "Number"

    lst = client.get("/api/operators", params={"subject_concept_id": jid, "q": "测试"}).json()
    assert lst["total"] >= 1

    assert client.post(f"/api/operators/{oid}/disable").json()["status"] == "disabled"


def test_pages_render(client):
    login(client, "admin")
    for url in ("/concepts", "/operators", "/concepts/new", "/operators/new"):
        r = client.get(url)
        assert r.status_code == 200, url
    body = client.get("/concepts").text
    assert "概念管理" in body and "使用次数" in body
    jid = concept_id_by_name(client, "机构")
    assert "绑定算子" in client.get(f"/concepts/{jid}").text
