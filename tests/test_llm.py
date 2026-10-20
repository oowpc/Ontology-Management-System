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
