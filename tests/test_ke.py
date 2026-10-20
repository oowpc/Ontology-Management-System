"""M7 · KE 标注进度：上传 → 全量替换 → 看板 的端到端用例。"""
from conftest import login

CSV_OK = (
    "题目ID,领域,题目摘要,标注状态,标注人,创建时间,开始时间,完成时间,复核结论,问题标签\n"
    "KE-T01,金融,测试题一,已完成,王芳,2026-10-01,2026-10-02 09:00,2026-10-02 12:00,一致,\n"
    "KE-T02,金融,测试题二,标注中,李强,2026-10-03,2026-10-04 09:00,,,题干歧义\n"
)
CSV_EMPTY = "题目ID,领域,题目摘要,标注状态,标注人,创建时间,开始时间,完成时间,复核结论,问题标签\n"
CSV_BAD = "题目ID,领域\nKE-X01,金融\n"
CSV_WITH_ART = (
    "题目ID,领域,题目摘要,标注状态,标注人,创建时间,开始时间,完成时间,复核结论,问题标签,篇编号\n"
    "KE-T10,金融,测试题十,已完成,王芳,2026-10-01,2026-10-02 09:00,2026-10-02 12:00,一致,,篇12\n"
    "KE-T11,金融,测试题十一,标注中,李强,2026-10-03,2026-10-04 09:00,,,,篇12\n"
)


def test_upload_page_shows_group(client):
    login(client, "annotator")
    r = client.get("/ke/upload")
    assert r.status_code == 200
    assert "一组" in r.text
    assert "今日上传状态" in r.text


def test_upload_and_dashboard(client):
    login(client, "annotator")
    r = client.post("/ke/upload", files={"file": ("t.csv", CSV_OK, "text/csv")},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "ok=" in r.headers["location"]

    r = client.get("/ke/dashboard", params={"group": "一组"})
    assert r.status_code == 200
    assert "KE-T02" in r.text            # 未关闭问题进入看板数据
    assert "今日已上传" in r.text        # 上传状态横幅

    # 幂等：同文件再传一次，仍是 2 行（全量替换而非追加）
    client.post("/ke/upload", files={"file": ("t.csv", CSV_OK, "text/csv")})
    groups = client.get("/api/ke/groups").json()["groups"]
    g1 = next(g for g in groups if g["name"] == "一组")
    assert g1["row_count"] == 2 and g1["done_count"] == 1

    # 全部组汇总视图（全员可见）
    r = client.get("/ke/dashboard")
    assert r.status_code == 200 and "KE-T02" in r.text


def test_upload_other_group_forbidden(client):
    login(client, "annotator")
    r = client.post("/api/ke/upload", data={"group": "二组"},
                    files={"file": ("t.csv", CSV_OK, "text/csv")})
    assert r.status_code == 403


def test_admin_can_upload_any_group(client):
    login(client, "admin")
    r = client.post("/api/ke/upload", data={"group": "二组"},
                    files={"file": ("t.csv", CSV_OK, "text/csv")})
    assert r.status_code == 201
    assert r.json()["row_count"] == 2


def test_upload_bad_csv_rejected(client):
    login(client, "annotator")
    r = client.post("/api/ke/upload",
                    files={"file": ("t.csv", CSV_BAD, "text/csv")})
    assert r.status_code == 400


def test_article_code_auto_rollup(client):
    """篇编号列：上传后入库，GOLD 统计按篇自动汇总（只计已完成）。"""
    login(client, "annotator")
    r = client.post("/api/ke/upload",
                    files={"file": ("t.csv", CSV_WITH_ART, "text/csv")})
    assert r.status_code == 201 and r.json()["row_count"] == 2
    from app.db import SessionLocal
    from app.gold.service import dashboard_stats
    db = SessionLocal()
    try:
        stats = dashboard_stats(db)
    finally:
        db.close()
    assert stats["article_auto"]["篇12"] == 1   # 只有 KE-T10 已完成


def test_gold_groups_follow_users(client):
    """组名口径统一：M8 组清单来自 users.group_name（一组/二组/三组），不再有 A组。"""
    login(client, "admin")
    from app.db import SessionLocal
    from app.gold.service import dashboard_stats, list_groups
    db = SessionLocal()
    try:
        groups = list_groups(db)
        stats = dashboard_stats(db)
    finally:
        db.close()
    assert groups[:3] == ["一组", "二组", "三组"]
    assert "A组" not in groups
    assert {"一组", "二组", "三组"} <= set(stats["by_group"])


def test_empty_csv_no_crash(client):
    login(client, "annotator")
    r = client.post("/api/ke/upload",
                    files={"file": ("t.csv", CSV_EMPTY, "text/csv")})
    assert r.status_code == 201 and r.json()["row_count"] == 0
    r = client.get("/ke/dashboard", params={"group": "一组"})
    assert r.status_code == 200 and "暂无数据" in r.text


def test_dashboard_requires_login(client):
    r = client.get("/ke/dashboard", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]
