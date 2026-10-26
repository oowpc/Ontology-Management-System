"""M9 验证：成品入库（CSV 钩子 + 手工补录）、权限与定稿锁定、导出、留痕。"""
from conftest import login

CSV_CONTENT = (
    "题目ID,领域,题目摘要,标注状态,标注人,创建时间,开始时间,完成时间,复核结论,问题标签,篇编号,KE内容\n"
    "KE-T20,金融,测试题二十,已完成,王芳,2026-10-01,2026-10-02 09:00,2026-10-02 12:00,一致,,篇12,利率=本金×费率；引用：利率\n"
    "KE-T21,金融,测试题廿一,标注中,李强,2026-10-03,,,,,篇12,逾期即罚息；引用：逾期\n"
)
CSV_PROTECT = (
    "题目ID,领域,题目摘要,标注状态,标注人,创建时间,开始时间,完成时间,复核结论,问题标签,篇编号,KE内容\n"
    "KE-M01,化学,成品改,已完成,王芳,2026-10-01,2026-10-02 09:00,2026-10-02 12:00,一致,,篇13,新内容不应覆盖已定稿条目\n"
)


def test_ingest_from_upload(client):
    """上传 CSV 带「篇编号 + KE内容」→ 自动入成品库草稿。"""
    login(client, "annotator")
    r = client.post("/api/ke/upload", files={"file": ("t.csv", CSV_CONTENT, "text/csv")})
    assert r.status_code == 201 and r.json()["items"]["created"] == 2
    items = client.get("/api/gold/items", params={"article_code": "篇12"}).json()["items"]
    mine = [i for i in items if i["ke_id"] in ("KE-T20", "KE-T21")]
    assert len(mine) == 2 and all(i["status"] == "draft" for i in mine)


def test_item_lifecycle_and_lock(client):
    """补录 → 409 幂等 → 本组标注员改草稿 → 标注员定稿被拒 → 审核员定稿 → 锁定与上传保护 → 留痕。"""
    login(client, "reviewer")
    r = client.post("/api/gold/items", json={
        "article_code": "篇13", "ke_id": "KE-M01", "group_name": "一组",
        "ke_text": "output={生命周期专用算子(a)|var a: String}", "concepts": ["利率"]})
    assert r.status_code == 201
    iid = r.json()["id"]
    # 幂等键冲突
    assert client.post("/api/gold/items", json={"article_code": "篇13", "ke_id": "KE-M01"}).status_code == 409

    login(client, "annotator")   # 一组标注员
    r = client.patch(f"/api/gold/items/{iid}", json={"ke_text": "output={生命周期专用算子(b)|var b: String} -- 标注员修订"})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert client.post(f"/api/gold/items/{iid}/status", json={"status": "gold"}).status_code == 403

    login(client, "reviewer")
    r = client.post(f"/api/gold/items/{iid}/status", json={"status": "gold"})
    assert r.status_code == 200 and r.json()["status"] == "gold"

    login(client, "annotator")
    assert client.patch(f"/api/gold/items/{iid}", json={"ke_text": "x"}).status_code == 403
    r = client.post("/api/ke/upload", files={"file": ("t.csv", CSV_PROTECT, "text/csv")})
    assert r.json()["items"]["protected"] == 1            # 定稿条目不被上传覆盖
    assert client.get(f"/api/gold/items/{iid}").json()["ke_text"].startswith("output={生命周期专用算子(b)")

    # 写操作全留痕（create + update + status ≥ 3 条）
    from sqlalchemy import func, select
    from app.db import SessionLocal
    from app.models import AuditLog
    db = SessionLocal()
    try:
        n = db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.entity_type == "gold_item", AuditLog.entity_id == iid)) or 0
    finally:
        db.close()
    assert n >= 3


def test_export_and_pages(client):
    login(client, "reviewer")
    r = client.get("/api/gold/items/export.csv", params={"article_code": "篇13"})
    assert r.status_code == 200 and "KE-M01" in r.text
    r = client.get("/api/gold/items/export.json", params={"article_code": "篇13"})
    assert r.status_code == 200 and r.json()["items"][0]["ke_id"] == "KE-M01"
    assert client.get("/gold/items").status_code == 200
    assert client.get(f"/gold/items/new").status_code == 200
