"""Agent 可操控性验证：活清单 manifest + MCP 工具层（agent 全开放）。"""
import sys
from pathlib import Path

import pytest

from conftest import concept_id_by_name, login

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def _mint(client, name="agent-test"):
    login(client, "admin")
    r = client.post("/api/tokens", json={"name": name})
    assert r.status_code == 201
    return r.json()["token"]


def test_token_auth_flow(client, app):
    token = _mint(client)
    # Bearer 可用
    r = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["username"] == "admin"
    # 吊销后失效（用无 cookie 的新客户端，排除 session 干扰）
    tid = client.get("/api/tokens").json()["items"][0]["id"]
    client.delete(f"/api/tokens/{tid}")
    from fastapi.testclient import TestClient
    with TestClient(app) as bare:
        assert bare.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_manifest(client):
    token = _mint(client)
    r = client.get("/api/agent/manifest", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    d = r.json()
    # 活路由表覆盖关键端点
    flat = [e for v in d["endpoints"].values() for e in v]
    assert any("/api/concepts" in e for e in flat)
    assert any("/api/submissions" in e for e in flat)
    assert any("/api/gold/articles" in e for e in flat)
    assert d["endpoint_count"] == len(flat)
    # 工作流与不变量
    assert len(d["workflows"]) >= 4
    assert "Bearer" in d["invariants"]["auth"]
    # 活词表（通用性）：阶段/组别/目标/KE 词表都在
    assert d["vocab"]["gold_stages"]
    assert d["vocab"]["gold_groups"]
    assert d["vocab"]["gold_targets"]["target_new"] == 400
    assert "done" in d["vocab"]["ke_status_map"]


@pytest.fixture()
def mcp(client):
    import mcp_server as ms
    token = _mint(client, "mcp-test")
    client.headers["Authorization"] = f"Bearer {token}"
    ms._client = client  # 指向测试内应用（TestClient 与 httpx.Client 接口兼容）
    yield ms
    ms._client = None


def test_mcp_tools_registered():
    import mcp_server as ms
    tools = ms.mcp._tool_manager.list_tools()
    names = {t.name for t in tools}
    assert len(tools) >= 25
    assert {"whoami", "get_manifest", "list_concepts", "submit_concept",
            "review_submission", "backfill_text", "gold_stats"} <= names


def test_mcp_core_flow(mcp):
    """agent 经 MCP 工具完成：自检 → 查概念 → 提交 → 审核 → TMP 回填。"""
    assert mcp.whoami()["username"] == "admin"
    assert mcp.get_manifest()["endpoint_count"] > 30
    assert mcp.list_concepts()["total"] >= 20

    jid = concept_id_by_name(mcp._client, "机构")
    sim = mcp.check_similarity(name="MCP流程概念", parent_id=jid)
    assert "block_threshold" in sim
    sub = mcp.submit_concept(name="MCP流程概念", suggested_parent_id=jid,
                             reason="agent 流程验证", sample_question="agent 题目")
    assert sub["status"] == "pending" and sub["tmp_id"].startswith("TMP-")

    done = mcp.review_submission(sub["id"], "approve", "agent 审核通过")
    assert done["status"] == "approved" and done["tmp_status"] == "resolved"

    bf = mcp.backfill_text(f"引用 {sub['tmp_id']} 完成回填", source_label="mcp")
    assert bf["replaced"][0]["replacement"] == "MCP流程概念"

    hist = mcp.list_history(entity_type="concept")
    assert hist["items"], "写操作应留痕"


def test_mcp_gold_and_health(mcp):
    assert mcp.gold_stats()["target_new"] == 400
    articles = mcp.list_gold_articles(group_name="三组")
    assert articles["total"] >= 1
    aid = articles["items"][0]["id"]
    assert mcp.update_gold_ke_done(aid, 15)["ke_done"] == 15
    cell = mcp.upsert_schedule_cell(day_index=30, slot="下午", group_name="一组",
                                    task="agent 写入的任务", status="done")
    assert cell["status"] == "done"
    issue = mcp.create_issue("agent 登记的分歧", content="思考过程", cluster="agent")
    assert issue["id"]
    assert mcp.comment_issue(issue["id"], "agent 评论")["comments"]
    assert mcp.set_issue_status(issue["id"], "resolved", "已结论")["status"] == "resolved"
    assert mcp.run_health()["summary"]
    assert mcp.get_health_latest()["report"]
