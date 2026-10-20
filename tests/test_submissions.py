"""M2 验证：查重拦截（验收1）、提交审核流、TMP 发放、批量处理、合并。"""
from conftest import concept_id_by_name, login


def _submit(client, name, **kw):
    payload = {
        "name": name,
        "reason": kw.pop("reason", "现有概念无法表达该场景"),
        "sample_question": kw.pop("sample_question", "某业务系统要求按此口径标注"),
    }
    payload.update(kw)
    return client.post("/api/submissions", json=payload)


# ---------- 验收 1：近义查重 + 阻止直接入库 ----------

def test_similarity_check_endpoint(client):
    login(client, "annotator")
    r = client.get("/api/similarity/check", params={"name": "人民政府"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "应命中相似概念"
    top = items[0]
    assert top["name"] == "地方政府"          # 别名「人民政府」完全命中
    assert top["score"] >= 0.95
    assert top["parent_name"] == "行政机关"
    assert r.json()["block_threshold"] > 0


def test_similar_blocks_direct_create(client):
    """验收1：与已有概念近义 → 给出相似列表并阻止直接入库。"""
    login(client, "admin")
    r = client.post("/api/concepts", json={"name": "政府"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert isinstance(detail, dict) and detail["similar"], "409 必须携带相似列表"
    assert "行政机关" in [s["name"] for s in detail["similar"]]  # 别名「政府」命中
    # 低相似概念不受影响
    ok = client.post("/api/concepts", json={"name": "完全无关新概念XYZ"})
    assert ok.status_code == 201


def test_high_similar_submission_still_accepted(client):
    """高相似不拦提交（拦的是直接入库）：提交单带查重快照进待审。"""
    login(client, "annotator")
    jid = concept_id_by_name(client, "国家机构")
    r = _submit(client, "政府机关", suggested_parent_id=jid)
    assert r.status_code == 201
    d = r.json()
    assert d["status"] == "pending"
    names = [h["name"] for h in d["similar_snapshot"]]
    assert "行政机关" in names  # 「政府」包含命中 ≥0.9 进快照


# ---------- 提交与审核 ----------

def test_submission_required_fields(client):
    login(client, "annotator")
    assert client.post("/api/submissions", json={
        "name": "缺字段概念", "suggested_parent_id": 1, "reason": "", "sample_question": "题",
    }).status_code == 422
    jid = concept_id_by_name(client, "机构")
    r = client.post("/api/submissions", json={
        "name": "缺父类概念", "reason": "r", "sample_question": "q",
    })
    assert r.status_code == 422  # 建议父类必填


def test_submission_flow_approve_and_tmp(client):
    """验收2 前半：提交发 TMP；通过后概念入库、TMP resolved。"""
    login(client, "annotator")
    jid = concept_id_by_name(client, "机构")
    r = _submit(client, "部门办公室", suggested_parent_id=jid, aliases=["办公室"])
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "pending" and d["concept_status"] == "pending"
    assert d["tmp_id"].startswith("TMP-") and d["tmp_status"] == "open"
    assert d["suggested_parent_name"] == "机构"

    login(client, "reviewer")
    pending = client.get("/api/submissions", params={"status": "pending"}).json()
    assert any(i["id"] == d["id"] for i in pending["items"])

    r = client.post(f"/api/submissions/{d['id']}/approve", json={"comment": "同意入库"})
    assert r.status_code == 200
    d2 = r.json()
    assert d2["status"] == "approved" and d2["concept_status"] == "approved"
    assert d2["tmp_status"] == "resolved" and d2["tmp_concept_id"] == d2["concept_id"]
    assert d2["reviewed_by"] == "reviewer"

    c = client.get(f"/api/concepts/{d2['concept_id']}").json()
    assert c["status"] == "approved" and c["reviewed_by"] == "reviewer"


def test_review_comment_required(client):
    login(client, "annotator")
    jid = concept_id_by_name(client, "机构")
    sid = _submit(client, "无意见概念", suggested_parent_id=jid).json()["id"]
    login(client, "reviewer")
    assert client.post(f"/api/submissions/{sid}/approve", json={"comment": ""}).status_code == 422


def test_annotator_cannot_review(client):
    login(client, "annotator")
    jid = concept_id_by_name(client, "机构")
    sid = _submit(client, "越权审核概念", suggested_parent_id=jid).json()["id"]
    r = client.post(f"/api/submissions/{sid}/approve", json={"comment": "x"})
    assert r.status_code == 403


def test_reject_flow(client):
    login(client, "annotator")
    jid = concept_id_by_name(client, "机构")
    sid = _submit(client, "驳回用概念", suggested_parent_id=jid).json()["id"]
    login(client, "reviewer")
    r = client.post(f"/api/submissions/{sid}/reject", json={"comment": "定义不清"})
    d = r.json()
    assert d["status"] == "rejected" and d["concept_status"] == "draft"
    assert d["tmp_status"] == "open"  # 驳回后 TMP 未决
    # 已处理单不可重复处理
    assert client.post(f"/api/submissions/{sid}/approve", json={"comment": "x"}).status_code == 400


def test_merge_flow(client):
    login(client, "annotator")
    jid = concept_id_by_name(client, "机构")
    sid = _submit(client, "政府管理部门", suggested_parent_id=jid).json()["id"]
    login(client, "reviewer")
    target = concept_id_by_name(client, "行政机关")
    r = client.post(f"/api/submissions/{sid}/merge",
                    json={"comment": "与行政机关同义", "merge_target_id": target})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "merged" and d["merge_target_name"] == "行政机关"
    assert d["concept_status"] == "disabled"
    assert d["tmp_status"] == "merged" and d["tmp_concept_id"] == target  # TMP 指向合并目标
    c = client.get(f"/api/concepts/{d['concept_id']}").json()
    assert c["merged_into_id"] == target


def test_batch_approve(client):
    login(client, "annotator")
    jid = concept_id_by_name(client, "机构")
    s1 = _submit(client, "批量概念甲", suggested_parent_id=jid).json()["id"]
    s2 = _submit(client, "批量概念乙", suggested_parent_id=jid).json()["id"]
    login(client, "reviewer")
    r = client.post("/api/submissions/batch",
                    json={"ids": [s1, s2], "action": "approve", "comment": "批量同意"})
    assert r.status_code == 200
    assert sorted(r.json()["processed"]) == sorted([s1, s2])
    assert r.json()["skipped"] == []
    for sid in (s1, s2):
        d = client.get(f"/api/submissions/{sid}").json()
        assert d["status"] == "approved" and d["tmp_status"] == "resolved"


# ---------- 页面 ----------

def test_submit_and_review_pages(client):
    login(client, "annotator")
    assert client.get("/submit").status_code == 200
    assert client.get("/review").status_code == 403  # 标注员进不了审核页

    jid = concept_id_by_name(client, "机构")
    r = client.post("/submit", data={
        "name": "页面提交概念", "suggested_parent_id": str(jid),
        "reason": "页面流程", "sample_question": "页面题目",
    }, follow_redirects=False)
    assert r.status_code == 303 and "tmp=" in r.headers["location"]

    login(client, "reviewer")
    body = client.get("/review").text
    assert "页面提交概念" in body and "批量通过选中" in body


def test_review_page_form_actions(client):
    """页面表单路径：/review/{sid} 单条动作 + /review/batch 批量（checkbox getlist）。"""
    login(client, "annotator")
    jid = concept_id_by_name(client, "机构")
    s1 = _submit(client, "表单概念甲", suggested_parent_id=jid).json()["id"]
    s2 = _submit(client, "表单概念乙", suggested_parent_id=jid).json()["id"]

    login(client, "reviewer")
    # 单条驳回（页面表单，action=reject）
    r = client.post(f"/review/{s1}", data={"action": "reject", "comment": "页面驳回"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert client.get(f"/api/submissions/{s1}").json()["status"] == "rejected"

    # 批量通过（页面表单，ids 为重复字段；httpx 用 dict+list 编码重复键）
    r = client.post("/review/batch",
                    data={"ids": [str(s2)], "action": "approve", "comment": "页面批量"},
                    follow_redirects=False)
    assert r.status_code == 303
    d = client.get(f"/api/submissions/{s2}").json()
    assert d["status"] == "approved" and d["tmp_status"] == "resolved"

    # 页面合并动作
    login(client, "annotator")
    s3 = _submit(client, "表单合并概念", suggested_parent_id=jid).json()["id"]
    login(client, "reviewer")
    target = concept_id_by_name(client, "行政机关")
    r = client.post(f"/review/{s3}",
                    data={"action": "merge", "comment": "页面合并", "merge_target_id": str(target)},
                    follow_redirects=False)
    assert r.status_code == 303
    assert client.get(f"/api/submissions/{s3}").json()["status"] == "merged"
