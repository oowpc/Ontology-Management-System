"""KEOL 源 hash / 差异检查 API 与页面。"""
import json

from conftest import login


def test_keol_sync_check_and_page(client, monkeypatch, tmp_path):
    src = tmp_path / "ontology.json"
    src.write_text(json.dumps({
        "concepts": [{"id": "c1", "canonical_name": "同步测试概念",
                      "description": "d", "parents": []}],
        "operators": [], "rules": [],
    }, ensure_ascii=False), encoding="utf-8")
    from app import keol_sync
    monkeypatch.setattr(keol_sync, "source_path", lambda: src)
    login(client, "admin")
    r = client.post("/api/keol/sync/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_counts"]["concepts"] == 1
    assert body["diff"]["concepts"]["added_count"] == 1
    s = client.get("/api/keol/sync/status")
    assert s.status_code == 200 and s.json()["checked"] is True
    page = client.get("/keol/sync")
    assert page.status_code == 200
    assert "KEOL 本体同步" in page.text
