"""M4 验证：变更历史留痕、before/after、回滚（验收3）。"""
from conftest import concept_id_by_name, login


def test_parent_change_history_and_rollback(client):
    """验收3：修改父类 → 历史见 before/after → 回滚还原。"""
    login(client, "admin")
    jid = concept_id_by_name(client, "机构")
    cid = client.post("/api/concepts", json={"name": "历史验证概念", "parent_id": jid}).json()["id"]

    # 修改父类（机构 → 行政区划）
    xid = concept_id_by_name(client, "行政区划")
    r = client.patch(f"/api/concepts/{cid}", json={"parent_id": xid})
    assert r.status_code == 200 and r.json()["parent_id"] == xid

    # 历史应有 create + update 两条，update 带 before/after
    hist = client.get(f"/api/concepts/{cid}/history").json()["items"]
    actions = [h["action"] for h in hist]
    assert actions[:2] == ["update", "create"]  # 倒序
    upd = hist[0]
    assert upd["before"]["parent_id"] == jid
    assert upd["after"]["parent_id"] == xid
    assert upd["actor"] == "admin"

    # 回滚到修改前
    r = client.post(f"/api/concepts/{cid}/rollback/{upd['id']}")
    assert r.status_code == 200
    assert r.json()["parent_id"] == jid

    # 回滚本身也留痕
    hist2 = client.get(f"/api/concepts/{cid}/history").json()["items"]
    assert hist2[0]["action"] == "rollback"
    assert hist2[0]["before"]["parent_id"] == xid
    assert hist2[0]["after"]["parent_id"] == jid


def test_rollback_requires_admin(client):
    login(client, "admin")
    cid = client.post("/api/concepts", json={"name": "回滚权限概念"}).json()["id"]
    client.patch(f"/api/concepts/{cid}", json={"definition": "v2"})
    upd = client.get(f"/api/concepts/{cid}/history").json()["items"][0]
    login(client, "reviewer")
    assert client.post(f"/api/concepts/{cid}/rollback/{upd['id']}").status_code == 403


def test_status_change_logged(client):
    login(client, "admin")
    cid = client.post("/api/concepts", json={"name": "停用留痕概念"}).json()["id"]
    client.post(f"/api/concepts/{cid}/disable")
    hist = client.get(f"/api/concepts/{cid}/history").json()["items"]
    assert hist[0]["action"] == "status"
    assert hist[0]["before"]["status"] == "approved"
    assert hist[0]["after"]["status"] == "disabled"


def test_create_rollback_refused(client):
    login(client, "admin")
    cid = client.post("/api/concepts", json={"name": "创建回滚概念"}).json()["id"]
    create_entry = next(h for h in client.get(f"/api/concepts/{cid}/history").json()["items"]
                        if h["action"] == "create")
    r = client.post(f"/api/concepts/{cid}/rollback/{create_entry['id']}")
    assert r.status_code == 400


def test_operator_update_logged(client):
    login(client, "admin")
    jid = concept_id_by_name(client, "机构")
    oid = client.post("/api/operators", json={
        "name": "留痕算子", "subject_concept_id": jid, "return_type": "String",
    }).json()["id"]
    client.patch(f"/api/operators/{oid}", json={"return_type": "Number"})
    r = client.get("/api/history", params={"entity_type": "operator"}).json()
    item = next(i for i in r["items"] if i["entity_id"] == oid and i["action"] == "update")
    assert item["before"]["return_type"] == "String"
    assert item["after"]["return_type"] == "Number"


def test_history_pages(client):
    login(client, "admin")
    cid = client.post("/api/concepts", json={"name": "页面历史概念"}).json()["id"]
    client.patch(f"/api/concepts/{cid}", json={"definition": "加一句定义"})
    body = client.get(f"/concepts/{cid}").text
    assert "变更历史" in body and "definition" in body and "加一句定义" in body
    assert client.get("/history").status_code == 200
    # 页面回滚（admin）
    upd = client.get(f"/api/concepts/{cid}/history").json()["items"][0]
    r = client.post(f"/concepts/{cid}/rollback/{upd['id']}", follow_redirects=False)
    assert r.status_code == 303
    assert client.get(f"/api/concepts/{cid}").json()["definition"] == ""
