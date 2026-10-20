"""Issue 管理验证（第①步）：创建校验、列表筛选/排序、CSV、空库态。

app.* 一律测试函数内延迟导入（顶层导入会抢在 DB_URL 设置前建引擎）。
"""
from conftest import concept_id_by_name, login

VALID = {
    "title": "「跨省居住地与户籍地不一致」应如何建模？",
    "type_tag": "模糊表达建模",
    "description": "例：KE-12 与 KE-47 冲突。矛盾：A 用居住地 B 用户籍地。提问：以哪个为准？",
    "cases": [{"original_text": "凡在本地居住满一年者可申领",
                   "variant_a": "补贴_对象(__制度) = {__居住满一年者}"}],
}


def _create(client, **over):
    payload = {**VALID, **over}
    return client.post("/api/issues", json=payload)


def test_create_and_validation(client):
    login(client, "annotator")
    r = _create(client, domain="特困", importance="高", urgency="中", owner="王芳")
    assert r.status_code == 201, r.text
    assert r.json()["code"].startswith("IS-")
    assert r.json()["status"] == "open"
    # 第二条编号递增
    r2 = _create(client, title="「一孩二孩是否同标准」口径如何统一？")
    assert int(r2.json()["code"].split("-")[1]) == int(r.json()["code"].split("-")[1]) + 1

    # 校验1：话题式标题被拒
    bad = _create(client, title="关于补贴口径的讨论")
    assert bad.status_code == 400 and "问句" in bad.json()["detail"]
    # 校验2：无 Case 被 pydantic 拦（422）；Case 缺原文/缺写法被服务层拦（400）
    assert _create(client, cases=[]).status_code == 422
    assert _create(client, cases=[{"variant_a": "x"}]).status_code == 400
    assert _create(client, cases=[{"original_text": "x"}]).status_code == 400
    # 必填：类型标签 / 描述
    assert _create(client, type_tag="").status_code == 422
    assert _create(client, description="").status_code == 422


def test_update_and_audit(client):
    login(client, "annotator")
    iid = _create(client, title="「紧急度默认值」应如何决定？").json()["id"]
    r = client.patch(f"/api/issues/{iid}", json={"urgency": "高", "owner": "李雷"})
    assert r.status_code == 200
    # 审计：create + update 两条
    from app.db import SessionLocal
    from app.models import AuditLog
    from sqlalchemy import select
    db = SessionLocal()
    try:
        rows = db.scalars(select(AuditLog).where(
            AuditLog.entity_type == "issue", AuditLog.entity_id == iid)).all()
        assert [a.action for a in rows] == ["create", "update"]
        assert rows[1].before["urgency"] is None
        assert rows[1].after["urgency"] == "高"
    finally:
        db.close()


def test_list_filters_sort_and_csv(client):
    login(client, "annotator")
    # 排序：未结案在前（新建的 open 都在前）
    rows = client.get("/api/issues").json()["items"]
    assert rows and rows[0]["status"] not in ("resolved", "archived")
    # 筛选
    assert client.get("/api/issues", params={"type_tag": "模糊表达建模"}).json()["total"] >= 2
    assert client.get("/api/issues", params={"q": "跨省"}).json()["total"] == 1
    assert client.get("/api/issues", params={"closed": "false"}).json()["total"] >= 2
    assert client.get("/api/issues", params={"closed": "true"}).json()["total"] == 0
    # 页面与 CSV
    body = client.get("/issues").text
    assert "IS-" in body and "新建 Issue" in body
    r = client.get("/issues/export.csv")
    assert "编号" in r.text and "跨省居住地" in r.text
    # 页面新建流程（表单路径 + 预填关联）
    body = client.get("/issues/new", params={"entity_type": "question", "entity_id": 1}).text
    assert 'value="question"' in body and 'value="1"' in body


def test_empty_list(client):
    """验收8：筛选到空时页面不报错。"""
    login(client, "annotator")
    body = client.get("/issues", params={"domain": "不存在领域"}).text
    assert "暂无 issue" in body


# ================= 第②步：详情页 / 关联 / 结案回填 / 集成 =================

def _mk(client, **over):
    r = _create(client, **over)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_detail_full(client):
    """验收1：2 条 Case + 3 个待讨论问题，详情页完整显示。"""
    login(client, "annotator")
    iid = _mk(client, cases=[
        {"original_text": "原文一", "variant_a": "op(__甲) = 1", "diff_note": "差在主体"},
        {"original_text": "原文二", "variant_a": "op(__甲) = 1", "variant_b": "op(__甲, __乙) = 1"},
    ])
    for n in range(3):
        r = client.post(f"/api/issues/{iid}/questions",
                        json={"text": f"待讨论问题{n + 1}", "source": "human"})
        assert r.status_code == 201
    client.post(f"/api/issues/{iid}/viewpoints",
                json={"person": "王芳", "stance": "观点一", "body": "用户籍地"})
    client.post(f"/api/issues/{iid}/comments", json={"body": "先讨论主体"})
    d = client.get(f"/api/issues/{iid}").json()
    assert len(d["cases"]) == 2 and len(d["questions"]) == 3
    assert d["viewpoints"][0]["person"] == "王芳"
    assert d["comments"][0]["body"] == "先讨论主体"
    body = client.get(f"/issues/{iid}").text
    for section in ("问题描述", "对照案例", "待讨论问题", "各方观点", "结论与范围",
                    "关联对象", "影响与回填", "评论", "变更历史"):
        assert section in body, section
    assert "op(__甲, __乙) = 1" in body  # 写法二保留换行显示


def test_prefill_from_question(client):
    """验收2：带 entity_link 创建 → 题目页出现「相关正式 Issue」并可跳转。"""
    login(client, "admin")
    # 造一道题（test_questions 在本文件之后跑，这里自造）
    from app.db import SessionLocal
    from app.models import KeTask
    db = SessionLocal()
    db.add(KeTask(group_name="试验组甲", ke_id="IS-题-1", domain="试验",
                  summary="issue 集成用题", status="标注中", person="王芳", tags=[]))
    db.commit()
    db.close()
    client.post("/api/questions/sync")
    qid = [i for i in client.get("/api/questions", params={"q": "IS-题-1"}).json()["items"]
           if i["ext_id"] == "IS-题-1"][0]["id"]
    iid = _mk(client, title="「该题的口径」应如何统一？",
              entity_link={"entity_type": "question", "entity_id": qid})
    d = client.get(f"/api/issues/{iid}").json()
    assert d["links"][0]["entity_type"] == "question" and d["links"][0]["url"] == f"/questions/{qid}"
    body = client.get(f"/questions/{qid}").text
    assert "相关正式 Issue" in body and f"/issues/{iid}" in body


def test_close_requires_conclusion(client):
    """验收3：结案缺结论/范围/未声明无影响 → 拒绝。"""
    login(client, "annotator")
    iid = _mk(client, title="「结案校验用」应如何定？")
    r = client.post(f"/api/issues/{iid}/close", json={"conclusion": "", "scope": "全领域"})
    assert r.status_code == 400 and "结论" in r.json()["detail"]
    r = client.post(f"/api/issues/{iid}/close", json={"conclusion": "用户籍地", "scope": ""})
    assert r.status_code == 400 and "适用范围" in r.json()["detail"]
    # 无影响清单且未显式声明 → 拒
    r = client.post(f"/api/issues/{iid}/close",
                    json={"conclusion": "用户籍地", "scope": "全领域"})
    assert r.status_code == 400 and "无受影响条目" in r.json()["detail"]


def test_impact_progress(client):
    """验收4：结案带 5 条影响 → 列表「待回填 5」；勾掉 2 → 3。"""
    login(client, "annotator")
    iid = _mk(client, title="「回填计数用」应如何落地？")
    # 关联 5 个概念（种子前 5 个）
    cids = [c["id"] for c in client.get("/api/concepts").json()["items"][:5]]
    for cid in cids:
        client.post(f"/api/issues/{iid}/links", json={"entity_type": "concept", "entity_id": cid})
    r = client.post(f"/api/issues/{iid}/close", json={
        "conclusion": "统一按户籍地", "scope": "育儿+特困",
        "impacts": [{"entity_type": "concept", "entity_id": c, "action": "需修改"} for c in cids]})
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    d = client.get(f"/api/issues/{iid}").json()
    assert d["impact_progress"] == {"done": 0, "total": 5}
    row = next(x for x in client.get("/api/issues").json()["items"] if x["id"] == iid)
    assert row["undone_count"] == 5
    # 勾掉 2 条
    for im in d["impacts"][:2]:
        client.post(f"/api/issues/{iid}/impacts/{im['impact_id']}/toggle")
    d = client.get(f"/api/issues/{iid}").json()
    assert d["impact_progress"]["done"] == 2
    row = next(x for x in client.get("/api/issues").json()["items"] if x["id"] == iid)
    assert row["undone_count"] == 3
    # 显式无影响声明路径
    iid2 = _mk(client, title="「无影响声明用」应如何定？")
    r = client.post(f"/api/issues/{iid2}/close", json={
        "conclusion": "无需改动", "scope": "本领域", "impact_none": True})
    assert r.status_code == 200


def test_reopen(client):
    """验收5：重开 → discussing，历史有记录。"""
    login(client, "annotator")
    iid = _mk(client, title="「重开用」应如何定？")
    client.post(f"/api/issues/{iid}/close",
                json={"conclusion": "c", "scope": "s", "impact_none": True})
    r = client.post(f"/api/issues/{iid}/reopen")
    assert r.status_code == 200 and r.json()["status"] == "discussing"
    d = client.get(f"/api/issues/{iid}").json()
    actions = [h["action"] for h in d["audit_tail"]]
    assert "close" in actions and "reopen" in actions


def test_concept_related_issues(client):
    """验收6：概念详情页能看到相关 issue 并跳转。"""
    login(client, "annotator")
    cid = concept_id_by_name(client, "机构")
    iid = _mk(client, title="「机构粒度」应如何划分？",
              entity_link={"entity_type": "concept", "entity_id": cid})
    body = client.get(f"/concepts/{cid}").text
    assert "相关 Issue" in body and f"/issues/{iid}" in body and "机构粒度" in body
    # 算子详情页同理（含新详情页本身）
    oid = client.get("/api/operators").json()["items"][0]["id"]
    body = client.get(f"/operators/{oid}").text
    assert "相关 Issue" in body and "entity_type=operator" in body


def test_mcp_issue_tools(client):
    """MCP 工具：创建/评论/结案/查影响。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import mcp_server as ms
    login(client, "admin")
    token = client.post("/api/tokens", json={"name": "mcp-issue"}).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    ms._client = client
    try:
        r = ms.issue_create("「MCP 建单」应如何验？",
                            "口径不一致", "例…矛盾…问…",
                            [{"original_text": "原文", "variant_a": "写法一"}])
        iid = r["id"]
        assert ms.issue_comment(iid, "MCP 评论")["id"]
        assert ms.issue_close(iid, "结论", "范围", impact_none=True)["status"] == "resolved"
        assert ms.issue_reopen(iid)["status"] == "discussing"
        assert ms.issue_list_impacts(iid)["progress"] == {"done": 0, "total": 0}
        assert ms.list_issues(status="discussing")["total"] >= 1
    finally:
        ms._client = None
