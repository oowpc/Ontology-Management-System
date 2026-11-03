"""DataAgent Monitor Webhook 事件收件箱与转 Issue。"""
import uuid

from conftest import login


def _payload(tag):
    return {
        "event_id": int(tag[:6], 16),
        "fingerprint": f"test-{tag}",
        "product_id": "demo-product",
        "source_id": "product-page",
        "severity": "high",
        "event": {
            "field": "price", "before": "499", "after": "599",
            "before_present": True, "after_present": True,
            "analysis_status": "analyzed",
            "analysis": {"category": "price", "summary": "价格由 499 变为 599"},
        },
        "evidence": {"official_url": "https://example.com/product"},
    }


def test_monitor_webhook_idempotent_and_issue(client):
    login(client, "admin")
    tag = uuid.uuid4().hex
    payload = _payload(tag)
    r = client.post("/api/monitor/events", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["duplicate"] is False
    eid = body["id"]
    r2 = client.post("/api/monitor/events", json=payload)
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    assert client.get("/api/monitor/events").json()["total"] >= 1
    page = client.get("/monitor")
    assert page.status_code == 200 and "demo-product" in page.text

    r = client.post(f"/api/monitor/events/{eid}/issue")
    assert r.status_code == 200, r.text
    iid = r.json()["issue_id"]
    assert iid
    ev = client.get(f"/api/monitor/events/{eid}").json()
    assert ev["status"] == "issue" and ev["issue_id"] == iid
    issue = client.get(f"/api/issues/{iid}").json()
    assert "demo-product" in issue["issue"]["title"]


def test_monitor_event_status(client):
    login(client, "admin")
    tag = uuid.uuid4().hex
    r = client.post("/api/monitor/events", json=_payload(tag))
    eid = r.json()["id"]
    r = client.post(f"/api/monitor/events/{eid}/status", json={"status": "ignored"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ignored"


def test_monitor_sync_remote_events(client, monkeypatch):
    from app.routers import monitor as router
    tag = uuid.uuid4().hex
    def fake(*a, **kw):
        return {"events": [{
            "id": int(tag[:6], 16), "product_id": "sync-product",
            "source_id": "page", "severity": "medium", "field": "text",
            "analysis_status": "not_candidate", "category": "content",
            "summary": "监控字段发生变化",
            "change": {"kind": "modified", "previous_preview": "a",
                       "current_preview": "b"},
            "checked_at": "2026-11-03T00:00:00+08:00",
        }]}
    monkeypatch.setattr(router, "fetch_dashboard", fake)
    login(client, "admin")
    r = client.post("/api/monitor/sync")
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1
