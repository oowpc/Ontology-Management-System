"""题目级视图验证：回填幂等、ext_id 消歧、列表页/筛选/CSV、空库态。

app.* 一律测试函数内延迟导入（顶层导入会抢在 DB_URL 设置前建引擎，打穿开发库）。
"""
from conftest import concept_id_by_name, login


def _seed_ke_tasks(rows):
    from app.db import SessionLocal
    from app.models import KeTask
    db = SessionLocal()
    try:
        for r in rows:
            db.add(KeTask(
                group_name=r.get("group", "试验组甲"), ke_id=r["id"],
                domain=r.get("domain", "试验"), summary=r.get("summary", ""),
                status=r.get("status", "待标注"), person=r.get("person", "王芳"),
                tags=r.get("tags", [])))
        db.commit()
    finally:
        db.close()


def _counts():
    from app.db import SessionLocal
    from app.models import KeTask, Question
    from sqlalchemy import func, select
    db = SessionLocal()
    try:
        return (db.scalar(select(func.count()).select_from(KeTask)) or 0,
                db.scalar(select(func.count()).select_from(Question)) or 0)
    finally:
        db.close()


def _sync(client):
    login(client, "admin")
    r = client.post("/api/questions/sync")
    assert r.status_code == 200
    return r.json()


def test_backfill_and_idempotent(client):
    """验收1：回填后题目数 == ke_tasks 行数；重复运行数量不变。"""
    _seed_ke_tasks([
        {"id": "T-001", "summary": "试验题一：1+1=?", "status": "已完成", "person": "王芳"},
        {"id": "T-002", "summary": "试验题二：水的化学式", "status": "标注中"},
        {"id": "T-003", "summary": "试验题三：光的折射", "status": "待复核"},
    ])
    tasks_before, _ = _counts()
    stats = _sync(client)
    tasks_after, questions_after = _counts()
    assert tasks_after == tasks_before
    assert questions_after == tasks_after  # 一题一行
    assert stats["created"] >= 3
    # 状态映射正确
    items = client.get("/api/questions", params={"q": "T-00"}).json()["items"]
    by_ext = {i["ext_id"]: i for i in items}
    assert by_ext["T-001"]["status"] == "approved"
    assert by_ext["T-002"]["status"] == "drafting"
    assert by_ext["T-003"]["status"] == "pending_review"
    assert by_ext["T-001"]["assignee"] == "王芳"
    # 重复运行：零新建
    stats2 = _sync(client)
    assert stats2["created"] == 0
    _, questions_final = _counts()
    assert questions_final == tasks_after


def test_ext_id_collision(client):
    """跨组重号 → 消歧为 组/题号，两行都成题。"""
    _seed_ke_tasks([
        {"id": "Q-撞", "group": "试验组甲", "summary": "同一题号甲组版"},
        {"id": "Q-撞", "group": "试验组乙", "summary": "同一题号乙组版"},
    ])
    stats = _sync(client)
    assert stats["collisions"] >= 2
    items = client.get("/api/questions", params={"q": "Q-撞"}).json()["items"]
    exts = {i["ext_id"] for i in items}
    assert "试验组甲/Q-撞" in exts and "试验组乙/Q-撞" in exts
    # 再次同步仍命中既有行（按消歧名）
    stats2 = _sync(client)
    items2 = client.get("/api/questions", params={"q": "Q-撞"}).json()["items"]
    assert len(items2) == len(items)


def test_list_page_filters_and_csv(client):
    _sync(client)
    login(client, "annotator")
    body = client.get("/questions").text
    assert "题目（" in body and "导出 CSV" in body
    # 领域/组/状态/关键字筛选
    r = client.get("/api/questions", params={"status": "approved"})
    assert all(i["status"] == "approved" for i in r.json()["items"])
    r = client.get("/api/questions", params={"q": "T-001"})
    assert r.json()["total"] == 1
    # 未解决 issue 筛选（此时全库无 open issue → 空）
    r = client.get("/api/questions", params={"has_open_issue": "true"})
    assert r.json()["total"] == 0
    # 空结果态（验收8）
    body = client.get("/questions", params={"domain": "不存在领域"}).text
    assert "暂无题目" in body
    # CSV 导出（当前筛选）
    r = client.get("/questions/export.csv", params={"status": "approved"})
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    assert "题号" in r.text and "T-001" in r.text


# ================= 第②步：详情页 / 写操作 / 反向入口 / 审计 =================

def _qid(client, ext):
    items = client.get("/api/questions", params={"q": ext}).json()["items"]
    match = [i for i in items if i["ext_id"] == ext]
    assert match, f"题目不存在：{ext}"
    return match[0]["id"]


def test_detail_sections(client):
    """验收2：五块内容都在。"""
    login(client, "annotator")
    qid = _qid(client, "T-001")
    body = client.get(f"/questions/{qid}").text
    for block in ("题目原文", "涉及概念与算子（CO）", "KE（", "issue（", "审计尾"):
        assert block in body, block


def test_add_link_and_reverse_entry(client):
    """验收5：CO 面板挂概念；概念详情页出现「使用此概念的题目」并可跳转。"""
    login(client, "annotator")
    qid = _qid(client, "T-001")
    cid = concept_id_by_name(client, "机构")
    r = client.post(f"/api/questions/{qid}/concepts", json={"concept_id": cid, "note": "主体"})
    assert r.status_code == 201
    assert client.post(f"/api/questions/{qid}/concepts",
                       json={"concept_id": cid}).status_code == 409  # 重复关联
    # 二选一约束
    assert client.post(f"/api/questions/{qid}/concepts", json={}).status_code == 400
    # CO 面板显示「被多少题使用」
    d = client.get(f"/api/questions/{qid}").json()
    assert any(c["name"] == "机构" and c["used_by"] >= 1 for c in d["concepts"])
    # 概念详情反向入口
    body = client.get(f"/concepts/{cid}").text
    assert "使用此概念的题目" in body and "T-001" in body and f"/questions/{qid}" in body


def test_ke_versions_and_diff(client):
    """验收3：两版 KE 逐行 diff。"""
    login(client, "annotator")
    qid = _qid(client, "T-002")
    v1 = client.post(f"/api/questions/{qid}/ke-versions", json={
        "content": "机构全称(__甲) = \"甲\"\n上级机构(__甲) = {__乙}"})
    assert v1.status_code == 201 and v1.json()["version"] == 1 and v1.json()["is_current"]
    v2 = client.post(f"/api/questions/{qid}/ke-versions", json={
        "content": "机构全称(__甲) = \"甲改\"\n上级机构(__甲) = {__乙}\n机构简称(__甲) = \"甲\""})
    assert v2.json()["version"] == 2
    # 当前版切换
    d = client.get(f"/api/questions/{qid}").json()
    assert d["current_version"]["version"] == 2
    assert "上级机构(__甲) = {__乙}" in d["current_version"]["content"]  # 换行保留
    # 默认 diff = 上一版 vs 当前版
    diff = client.get(f"/api/questions/{qid}/ke-versions/diff").json()
    kinds = [l["kind"] for l in diff["lines"]]
    assert "add" in kinds and "del" in kinds
    assert any(l["text"] == '机构全称(__甲) = "甲"' and l["kind"] == "del" for l in diff["lines"])
    assert any('机构简称' in l["text"] and l["kind"] == "add" for l in diff["lines"])
    # 指定版本 + 页面 diff 表格
    body = client.get(f"/questions/{qid}", params={"v1": 1, "v2": 2}).text
    assert "diff-add" in body and "diff-del" in body


def test_ke_version_role(client):
    """另存新版本仅标注员/管理员（规格原文）。"""
    login(client, "reviewer")
    qid = _qid(client, "T-001")
    assert client.post(f"/api/questions/{qid}/ke-versions",
                       json={"content": "x"}).status_code == 403
    login(client, "annotator")
    assert client.post(f"/api/questions/{qid}/ke-versions",
                       json={"content": "output = 机构全称(__甲);"}).status_code == 201


def test_issue_count_flow(client):
    """验收4：新建 issue → 列表未解决数 +1；解决后归 0。"""
    login(client, "annotator")
    qid = _qid(client, "T-003")
    r = client.post(f"/api/questions/{qid}/issues", json={
        "title": "粒度分歧：是否拆概念", "tags": ["粒度"], "body": "思考过程记录"})
    assert r.status_code == 201
    iid = r.json()["id"]
    items = client.get("/api/questions", params={"q": "T-003"}).json()["items"]
    assert items[0]["open_issues"] == 1
    # 列表页「只看有未解决 issue」能筛到
    assert client.get("/api/questions", params={"has_open_issue": "true"}).json()["total"] == 1
    r = client.post(f"/api/questions/{qid}/issues/{iid}/resolve")
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    items = client.get("/api/questions", params={"q": "T-003"}).json()["items"]
    assert items[0]["open_issues"] == 0


def test_status_role_rules(client):
    login(client, "annotator")
    qid = _qid(client, "T-003")
    assert client.post(f"/api/questions/{qid}/status",
                       json={"status": "approved"}).status_code == 403  # 标注员不能过审
    assert client.post(f"/api/questions/{qid}/status",
                       json={"status": "drafting"}).status_code == 200
    login(client, "reviewer")
    r = client.post(f"/api/questions/{qid}/status", json={"status": "approved"})
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_audit_tail(client):
    """验收6：写操作后审计尾出现对应记录。"""
    login(client, "reviewer")
    qid = _qid(client, "T-003")
    d = client.get(f"/api/questions/{qid}").json()
    actions = [h["action"] for h in d["audit_tail"]]
    assert "status" in actions and "create_issue" in actions and "resolve_issue" in actions
    body = client.get(f"/questions/{qid}").text
    assert "审计尾" in body and "create_issue" in body
    # before/after 含 status 字段（规格要求）
    st = next(h for h in d["audit_tail"] if h["action"] == "status")
    assert any(f["field"] == "status" for f in st["diff"])
