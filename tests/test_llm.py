"""LLM 辅助验证：未配置降级 + mock 模型输出（不需要真实模型）。"""
import json

from conftest import concept_id_by_name, login


def test_llm_disabled_by_default(client):
    login(client, "admin")
    r = client.get("/api/llm/status")
    assert r.json()["enabled"] is False
    r = client.post("/api/llm/dedup", json={"name": "政府"})
    assert r.json() == {"llm": False, "items": []}
    r = client.post("/api/llm/review-suggest", json={"submission_id": 1})
    assert r.json() == {"llm": False}


def _enable_mock_llm(monkeypatch, chat_return):
    from app import llm
    monkeypatch.setattr(llm.settings, "llm_base_url", "http://mock-llm")
    monkeypatch.setattr(llm.settings, "llm_model", "mock-model")
    monkeypatch.setattr(llm, "chat", lambda messages, **kw: chat_return)


def test_llm_dedup_mocked(client, monkeypatch):
    login(client, "annotator")
    target_id = concept_id_by_name(client, "行政机关")
    payload = json.dumps({"items": [
        {"id": target_id, "verdict": "same", "reason": "均指国家行政机关"},
        {"id": 999999, "verdict": "same", "reason": "幽灵ID应被过滤"},
    ]}, ensure_ascii=False)
    _enable_mock_llm(monkeypatch, payload)

    r = client.get("/api/llm/status")
    assert r.json()["enabled"] is True
    r = client.post("/api/llm/dedup", json={"name": "政府管理部门", "aliases": []})
    d = r.json()
    assert d["llm"] is True
    assert len(d["items"]) == 1  # 幽灵 ID 已过滤
    assert d["items"][0]["name"] == "行政机关"
    assert d["items"][0]["verdict"] == "same"


# ---------- M23：重试退避 + usage 台账 ----------

class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _enable_http_mock(monkeypatch, responder):
    from app import llm
    monkeypatch.setattr(llm.settings, "llm_base_url", "http://mock-llm")
    monkeypatch.setattr(llm.settings, "llm_model", "mock-model")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)   # 退避不真睡
    monkeypatch.setattr(llm.httpx, "post", responder)


def _llm_calls(scene):
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import LlmCall
    db = SessionLocal()
    try:
        return db.scalars(select(LlmCall).where(LlmCall.scene == scene)).all()
    finally:
        db.close()


def test_llm_retry_then_success_records_usage(client, monkeypatch):
    """429 退避重试 → 成功；台账记录 attempts=2 与 token 用量。"""
    from app import llm
    state = {"n": 0}

    def responder(url, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return _Resp(429, text="rate limited")
        return _Resp(200, {"choices": [{"message": {"content": "你好"}}],
                           "usage": {"prompt_tokens": 11, "completion_tokens": 7}})

    _enable_http_mock(monkeypatch, responder)
    out = llm.chat([{"role": "user", "content": "hi"}], scene="retry_ok")
    assert out == "你好" and state["n"] == 2
    rows = _llm_calls("retry_ok")
    assert len(rows) == 1 and rows[0].ok and rows[0].attempts == 2
    assert rows[0].prompt_tokens == 11 and rows[0].completion_tokens == 7


def test_llm_persistent_failure_returns_none_and_records(client, monkeypatch):
    """持续 500 → 重试耗尽返回 None（降级）；台账 ok=False；4xx 不重试。"""
    from app import llm
    state = {"n": 0}

    def responder(url, **kw):
        state["n"] += 1
        return _Resp(500, text="server boom")

    _enable_http_mock(monkeypatch, responder)
    assert llm.chat([{"role": "user", "content": "hi"}], scene="retry_fail") is None
    from app.config import settings
    assert state["n"] == settings.llm_max_retries + 1
    rows = _llm_calls("retry_fail")
    assert len(rows) == 1 and not rows[0].ok and "500" in rows[0].error

    state["n"] = 0

    def responder_400(url, **kw):
        state["n"] += 1
        return _Resp(400, text="bad request")

    monkeypatch.setattr(llm.httpx, "post", responder_400)
    assert llm.chat([{"role": "user", "content": "hi"}], scene="no_retry_4xx") is None
    assert state["n"] == 1          # 4xx 不重试
    assert _llm_calls("no_retry_4xx")[0].attempts == 1


def test_llm_usage_api(client, monkeypatch):
    """GET /api/llm/usage 汇总台账（reviewer/admin）。"""
    from app import llm
    _enable_http_mock(monkeypatch, lambda url, **kw: _Resp(
        200, {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 3}}))
    llm.chat([{"role": "user", "content": "x"}], scene="usage_api")

    login(client, "reviewer")
    r = client.get("/api/llm/usage")
    assert r.status_code == 200
    d = r.json()
    assert d["enabled"] is True
    assert d["totals"]["calls"] >= 1 and d["totals"]["prompt_tokens"] >= 3
    assert "usage_api" in d["scenes"] and d["scenes"]["usage_api"]["ok"] == 1


def test_llm_review_suggest_mocked(client, monkeypatch):
    # 先造一条待审单
    login(client, "annotator")
    jid = concept_id_by_name(client, "机构")
    sid = client.post("/api/submissions", json={
        "name": "LLM审核概念", "suggested_parent_id": jid,
        "reason": "r", "sample_question": "q",
    }).json()["id"]

    target_id = concept_id_by_name(client, "行政机关")
    payload = json.dumps({
        "action": "merge", "comment": "与「行政机关」语义重合，建议合并",
        "merge_target": "行政机关", "rationale": "名称与别名高度重合",
    }, ensure_ascii=False)
    _enable_mock_llm(monkeypatch, payload)

    login(client, "reviewer")
    r = client.post("/api/llm/review-suggest", json={"submission_id": sid})
    d = r.json()
    assert d["llm"] is True
    assert d["action"] == "merge"
    assert d["merge_target_id"] == target_id  # 名称解析为概念 ID
    assert "行政机关" in d["comment"]


def test_llm_bad_output_degrades(client, monkeypatch):
    """模型返回非 JSON 时按不可用处理，不炸主流程。"""
    _enable_mock_llm(monkeypatch, "这是一段废话，不是 JSON")
    login(client, "annotator")
    r = client.post("/api/llm/dedup", json={"name": "政府"})
    assert r.json() == {"llm": False, "items": []}
