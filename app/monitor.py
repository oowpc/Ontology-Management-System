"""DataAgent Monitor 只读桥：OMS 页面读取独立监控服务的 dashboard API。"""
from __future__ import annotations

import httpx

from .config import settings


class MonitorUnavailable(RuntimeError):
    pass


def _client() -> httpx.Client:
    return httpx.Client(base_url=settings.dataagent_monitor_url.rstrip("/"),
                        timeout=settings.dataagent_monitor_timeout)


def dashboard(run_limit: int = 20, event_limit: int = 20) -> dict:
    try:
        with _client() as c:
            r = c.get("/api/dashboard", params={"run_limit": run_limit,
                                                "event_limit": event_limit})
        if r.status_code >= 400:
            raise MonitorUnavailable(f"monitor dashboard {r.status_code}")
        data = r.json()
        if not isinstance(data, dict):
            raise MonitorUnavailable("monitor dashboard returned non-object")
        return data
    except MonitorUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        raise MonitorUnavailable(str(e)[:300]) from e


def health() -> dict:
    try:
        with _client() as c:
            r = c.get("/healthz")
        if r.status_code >= 400:
            return {"ok": False, "status": r.status_code}
        return {"ok": True, "body": r.json()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
