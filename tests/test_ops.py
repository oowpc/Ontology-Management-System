"""P2-B：运行状态摘要。"""
from conftest import login


def test_ops_summary_and_page(client):
    login(client, "admin")
    r = client.get("/api/ops/summary")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["counts"]["concepts"] >= 1
    assert "backup" in d and "certificate" in d
    assert "services" in d
    assert client.get("/ops").status_code == 200
    login(client, "annotator")
    assert client.get("/api/ops/summary").status_code == 403
