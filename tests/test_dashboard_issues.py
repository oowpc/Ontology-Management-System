"""P3：标注看板与 Issue Markdown 导出。"""
from conftest import login


def _issue(client, batch_id=None):
    payload = {
        "title": "「看板测试问题」应如何建模？",
        "type_tag": "模糊表达建模",
        "description": "例：A/B 冲突。",
        "cases": [{"original_text": "凡在本地居住满一年者可申领",
                   "variant_a": "A"}],
    }
    if batch_id:
        payload["annotation_batch_id"] = batch_id
    r = client.post("/api/issues", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_dashboard_and_issue_markdown(client):
    login(client, "admin")
    r = client.post("/api/annotation/batches", json={
        "batch_id": "DASH-ISO-1", "name": "看板批次",
        "source_name": "x", "source_version": "v1", "source_checksum": "x",
        "source_type": "policy", "domain": "测试", "guide_version": "v1",
    })
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    issue = _issue(client, bid)
    page = client.get("/annotation/dashboard")
    assert page.status_code == 200
    assert "标注运营看板" in page.text and "DASH-ISO-1" in page.text

    md = client.get(f"/issues/{issue['id']}/export.md")
    assert md.status_code == 200
    assert "text/markdown" in md.headers["content-type"]
    assert "看板测试问题" in md.text and "案例 1" in md.text

    bmd = client.get(f"/annotation/batches/{bid}/issues.md")
    assert bmd.status_code == 200
    assert "DASH-ISO-1" in bmd.text and issue["code"] in bmd.text
