"""M3 验证：TMP 映射、批量回填、未决处理、回填记录（验收2）。"""
from conftest import concept_id_by_name, login


def _submit_and(client, name, approve=True):
    login(client, "annotator")
    jid = concept_id_by_name(client, "机构")
    sub = client.post("/api/submissions", json={
        "name": name, "suggested_parent_id": jid,
        "reason": "回填测试", "sample_question": "回填题目",
    }).json()
    if approve:
        login(client, "reviewer")
        client.post(f"/api/submissions/{sub['id']}/approve", json={"comment": "同意"})
        sub = client.get(f"/api/submissions/{sub['id']}").json()
    return sub


def test_tmp_mappings_list(client):
    sub = _submit_and(client, "回填映射概念")
    login(client, "annotator")
    r = client.get("/api/tmp-mappings")
    assert r.status_code == 200
    m = next(i for i in r.json()["items"] if i["tmp_id"] == sub["tmp_id"])
    assert m["status"] == "resolved"
    assert m["concept_name"] == "回填映射概念"
    # 状态过滤
    r = client.get("/api/tmp-mappings", params={"status": "open"})
    assert all(i["status"] == "open" for i in r.json()["items"])


def test_backfill_resolved_and_records(client):
    """验收2：TMP-xxx 批量替换为正式名，且回填记录可查。"""
    sub = _submit_and(client, "回填目标概念")
    tmp_id = sub["tmp_id"]
    text = f"上级机构({tmp_id}) = {{政府(__广东省)}}\n-- {tmp_id} 的定义见标注规范"
    login(client, "annotator")
    r = client.post("/api/tmp/backfill", json={"text": text, "source_label": "验收批次"})
    assert r.status_code == 200
    d = r.json()
    assert d["unresolved"] == []
    assert d["replaced"][0]["tmp_id"] == tmp_id
    assert d["replaced"][0]["replacement"] == "回填目标概念"
    assert d["replaced"][0]["count"] == 2
    assert "回填目标概念" in d["text"] and tmp_id not in d["text"]
    # 回填记录可查
    login(client, "reviewer")
    records = client.get("/api/tmp/backfill/records").json()["items"]
    rec = next(i for i in records if i["tmp_id"] == tmp_id)
    assert rec["replaced_count"] == 2
    assert rec["replacement"] == "回填目标概念"
    assert rec["source_label"] == "验收批次"
    assert rec["actor"] == "annotator"


def test_backfill_unresolved_kept(client):
    login(client, "annotator")
    jid = concept_id_by_name(client, "机构")
    sub = client.post("/api/submissions", json={
        "name": "未决回填概念", "suggested_parent_id": jid,
        "reason": "r", "sample_question": "q",
    }).json()
    text = f"引用 {sub['tmp_id']} 和 TMP-9999"
    r = client.post("/api/tmp/backfill", json={"text": text})
    d = r.json()
    assert d["replaced"] == []
    reasons = {u["tmp_id"]: u["reason"] for u in d["unresolved"]}
    assert reasons[sub["tmp_id"]] == "审核未决或已驳回"
    assert reasons["TMP-9999"] == "未登记"
    assert d["text"] == text  # 原文不动


def test_backfill_merged_points_to_target(client):
    login(client, "annotator")
    jid = concept_id_by_name(client, "机构")
    sub = client.post("/api/submissions", json={
        "name": "合并回填概念", "suggested_parent_id": jid,
        "reason": "r", "sample_question": "q",
    }).json()
    login(client, "reviewer")
    target = concept_id_by_name(client, "行政机关")
    client.post(f"/api/submissions/{sub['id']}/merge",
                json={"comment": "同义", "merge_target_id": target})
    r = client.post("/api/tmp/backfill", json={"text": f"主体是 {sub['tmp_id']}"})
    d = r.json()
    assert d["replaced"][0]["replacement"] == "行政机关"
    assert d["replaced"][0]["concept_id"] == target


def test_tmp_pages(client):
    sub = _submit_and(client, "页面回填概念")
    login(client, "annotator")
    assert client.get("/tmp").status_code == 200
    r = client.post("/tmp/backfill", data={
        "text": f"用到 {sub['tmp_id']} 一次", "source_label": "页面批次",
    })
    assert r.status_code == 200
    assert "页面回填概念" in r.text and "回填结果" in r.text
    body = client.get("/tmp").text
    assert "页面批次" in body and sub["tmp_id"] in body
