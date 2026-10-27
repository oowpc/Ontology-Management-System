"""前端选择器/分页 DAG/搜索 API 的回归。"""
from conftest import concept_id_by_name, login


def _mk(client, name, parents=None):
    payload = {"name": name}
    if parents is not None:
        payload["parents"] = parents
    r = client.post("/api/concepts", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_concept_search_api(client):
    login(client, "admin")
    r = client.get("/api/concepts/search", params={"q": "机构", "limit": 5})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items and all({"id", "name", "domain", "status"} <= set(i) for i in items)
    r = client.get("/api/concepts/search", params={"status": "approved", "limit": 5})
    assert r.status_code == 200
    assert all(i["status"] == "approved" for i in r.json()["items"])
    cid = concept_id_by_name(client, "机构")
    r = client.get("/api/concepts/search", params={"q": "机构", "exclude_id": cid})
    assert all(i["id"] != cid for i in r.json()["items"])


def test_concept_form_uses_picker(client):
    login(client, "admin")
    html = client.get("/concepts/new").text
    assert 'id="picker-primary"' in html and 'id="picker-extras"' in html
    assert '<select name="parent_id">' not in html
    assert html.count("<option") < 30
    cid = concept_id_by_name(client, "机构")
    edit = client.get(f"/concepts/{cid}/edit").text
    assert 'data-old-primary-target="#picker-extras"' in edit


def test_list_and_operator_filter_use_picker(client):
    login(client, "admin")
    html = client.get("/concepts").text
    assert 'id="filter-parent"' in html
    assert '<select name="parent_id">' not in html
    ops = client.get("/operators").text
    assert 'data-name="subject_concept_id"' in ops
    assert '<select name="subject_concept_id">' not in ops


def test_dag_layout_api_and_page(client):
    login(client, "admin")
    r = _mk(client, "DAG根")
    a = _mk(client, "DAG左", [r])
    b = _mk(client, "DAG右", [r])
    x = _mk(client, "DAG子", [a, b])
    data = client.get("/api/graph", params={"root_id": r, "layout": "dag"}).json()
    assert data["layout"] == "dag"
    assert {n["id"] for n in data["nodes"]} == {r, a, b, x}
    extra = [l for l in data["links"] if l.get("extra")]
    assert any(l["source"] == b and l["target"] == x for l in extra)
    page = client.get("/graph", params={"root_id": r, "layout": "dag"})
    assert page.status_code == 200 and "DAG 布局" in page.text
    default_page = client.get("/graph", params={"root_id": r})
    assert 'data-layout="dag"' in default_page.text
    nonroot = client.get("/graph", params={"root_id": x})
    assert f'<option value="{x}" selected>' in nonroot.text


def test_review_merge_picker(client):
    login(client, "admin")
    jid = concept_id_by_name(client, "机构")
    sub = client.post("/api/submissions", json={
        "name": "审核选择器概念", "suggested_parent_id": jid,
        "reason": "r", "sample_question": "q"}).json()
    html = client.get("/review", params={"status": "pending"}).text
    assert 'data-name="merge_target_id"' in html
    assert "<select name=\"merge_target_id\">" not in html
