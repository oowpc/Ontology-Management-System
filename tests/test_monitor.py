"""DataAgent Monitor 只读接入：页面与 API 代理。"""
from conftest import login


def _fake():
    return {
        "generated_at": "2026-11-03T00:00:00+08:00",
        "overview": {"total_runs": 1, "completed_runs": 1, "failed_runs": 0,
                     "total_events": 1, "healthy_sources": 1, "failed_sources": 0,
                     "pending_deliveries": 0, "dead_deliveries": 0},
        "latest_run": {"id": "run-1", "status": "completed",
                       "finished_at": "2026-11-03T00:00:01+08:00"},
        "sources": [{"product_id": "demo", "source_id": "page",
                     "status": "success", "changed": False,
                     "facts_preview": {"fields": ["text"], "count": 1},
                     "checked_at": "2026-11-03T00:00:00+08:00"}],
        "events": [{"id": 1, "product_id": "demo", "source_id": "page",
                    "severity": "medium", "field": "text",
                    "analysis_status": "analyzed", "category": "feature",
                    "change": {"kind": "modified", "current_preview": "new"},
                    "checked_at": "2026-11-03T00:00:00+08:00"}],
        "runs": [{"id": "run-1", "status": "completed",
                  "started_at": "2026-11-03T00:00:00+08:00",
                  "finished_at": "2026-11-03T00:00:01+08:00",
                  "summary": {"total_sources": 1, "successful_sources": 1,
                              "failed_sources": 0, "skipped_sources": 0,
                              "created_events": 1}}],
    }


def test_monitor_page_and_api(client, monkeypatch):
    from app.routers import monitor as router
    monkeypatch.setattr(router, "fetch_dashboard", lambda *a, **kw: _fake())
    login(client, "admin")
    page = client.get("/monitor")
    assert page.status_code == 200
    assert "外部产品监控" in page.text
    assert "demo" in page.text
    api = client.get("/api/monitor/dashboard")
    assert api.status_code == 200
    assert api.json()["overview"]["total_events"] == 1


def test_monitor_unavailable_is_graceful(client, monkeypatch):
    from app.routers import monitor as router
    from app.monitor import MonitorUnavailable
    def fail(*a, **kw):
        raise MonitorUnavailable("down")
    monkeypatch.setattr(router, "fetch_dashboard", fail)
    login(client, "admin")
    page = client.get("/monitor")
    assert page.status_code == 200
    assert "监控服务不可用" in page.text
    assert client.get("/api/monitor/dashboard").status_code == 503
