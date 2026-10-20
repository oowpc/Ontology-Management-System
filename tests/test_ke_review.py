"""M10 验证：逐条复核 → 写回 → 留痕 → 上传重放（系统结论为准）→ 指标联动。"""
from conftest import login

CSV_REVIEW = (
    "题目ID,领域,题目摘要,标注状态,标注人,创建时间,开始时间,完成时间,复核结论,问题标签\n"
    "KE-R01,金融,复核题一,待复核,王芳,2026-10-01,2026-10-02 09:00,,,\n"
    "KE-R02,金融,复核题二,待复核,李强,2026-10-03,2026-10-04 09:00,,,\n"
    "KE-R03,金融,复核题三,已完成,王芳,2026-10-01,2026-10-02 09:00,2026-10-02 12:00,一致,\n"
)


def test_review_flow_and_replay(client):
    login(client, "annotator")
    assert client.post("/api/ke/upload",
                       files={"file": ("t.csv", CSV_REVIEW, "text/csv")}).status_code == 201

    login(client, "reviewer")
    q = client.get("/api/ke/review/queue").json()
    ids = {i["ke_id"] for i in q["items"]}
    assert {"KE-R01", "KE-R02"} <= ids and "KE-R03" not in ids   # 队列只有待复核

    r = client.post("/api/ke/review", json={
        "group_name": "一组", "ke_id": "KE-R01", "conclusion": "一致", "comment": "没问题"})
    assert r.status_code == 201 and r.json()["status"] == "已完成"
    r = client.post("/api/ke/review", json={
        "group_name": "一组", "ke_id": "KE-R02", "conclusion": "不一致", "comment": "答案错误"})
    assert r.status_code == 201
    assert r.json()["status"] == "已退回" and "复核退回" in r.json()["tags"]

    # 留痕
    hist = client.get("/api/ke/review/history", params={"group": "一组"}).json()["items"]
    assert {h["ke_id"] for h in hist} >= {"KE-R01", "KE-R02"}

    # 重放：组内再传同一份 CSV（表里仍是「待复核」）→ 系统结论覆盖，done_count 按覆盖后计
    login(client, "annotator")
    r = client.post("/api/ke/upload", files={"file": ("t.csv", CSV_REVIEW, "text/csv")})
    assert r.status_code == 201
    assert r.json()["done_count"] == 2            # KE-R03 + 系统复核一致的 KE-R01
    assert any("系统复核结论覆盖" in w for w in r.json()["warnings"])

    login(client, "reviewer")
    q = client.get("/api/ke/review/queue", params={"group": "一组"}).json()
    assert q["total"] == 0                        # 队列清空（R01 已完成、R02 已退回）


def test_review_batch_and_permission(client):
    login(client, "annotator")
    client.post("/api/ke/upload", files={"file": ("t.csv", CSV_REVIEW, "text/csv")})
    # 标注员无权复核
    r = client.post("/api/ke/review", json={
        "group_name": "一组", "ke_id": "KE-R01", "conclusion": "一致"})
    assert r.status_code == 403

    login(client, "reviewer")
    r = client.post("/api/ke/review/batch", json={
        "items": [{"group_name": "一组", "ke_id": "KE-R01"},
                  {"group_name": "一组", "ke_id": "KE-R02"},
                  {"group_name": "一组", "ke_id": "KE-NOPE"}],
        "conclusion": "一致", "comment": "抽查通过"})
    assert r.status_code == 200
    assert len(r.json()["processed"]) == 2 and len(r.json()["errors"]) == 1


CSV_PROTECT_PAGE = (
    "题目ID,领域,题目摘要,标注状态,标注人,创建时间,开始时间,完成时间,复核结论,问题标签\n"
    "KE-P01,医疗,页面题一,待复核,张敏,2026-10-05,2026-10-06 09:00,,,\n"
)


def test_review_page(client):
    login(client, "annotator")
    client.post("/api/ke/upload", files={"file": ("t.csv", CSV_PROTECT_PAGE, "text/csv")})
    assert client.get("/ke/review").status_code == 403        # 标注员不可见
    login(client, "reviewer")
    r = client.get("/ke/review")
    assert r.status_code == 200 and "KE-P01" in r.text
