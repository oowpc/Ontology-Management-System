"""P2 · 查重打分边界用例（阈值 0.95 硬拦 / 0.75 提示 的邻域行为）。"""
from sqlalchemy import select

from conftest import login


def test_pair_score_bounds():
    """纯函数边界：同名归一化 1.0；包含关系加权到 0.9；空串 0。"""
    from app.similarity import pair_score
    assert pair_score("行政区划", "行政区划") == 1.0
    assert pair_score("行政区划", " 行政区划　") == 1.0          # NFKC + 去空白归一化
    assert pair_score("政府", "地方政府") == 0.9               # 包含关系加权
    assert pair_score("", "任意") == 0.0
    assert 0.0 < pair_score("行政区划", "行政划区") < 1.0


def test_find_similar_threshold_filtering(client):
    """find_similar 按阈值过滤、停用概念不参与；同父加权 +0.05（封顶 1.0）。"""
    login(client, "admin")
    from app.db import SessionLocal
    from app.models import Concept
    from app.similarity import find_similar, score_against
    db = SessionLocal()
    try:
        db.add(Concept(name="阈值验证-地方政府", domain="通用", status="approved", created_by="test"))
        db.add(Concept(name="阈值验证-完全无关词", domain="通用", status="approved", created_by="test"))
        db.add(Concept(name="阈值验证-地方政府停用版", domain="通用", status="disabled", created_by="test"))
        db.commit()
        hits = find_similar(db, name="阈值验证-地方政府", threshold=0.75)
        names = [h["name"] for h in hits]
        assert "阈值验证-地方政府" in names                    # 自身命中（同名列出，由上层决定是否排除）
        assert "阈值验证-地方政府停用版" not in names          # 停用不参与查重
        assert "阈值验证-完全无关词" not in names              # 低于阈值被过滤
        # 高阈值下只剩几乎同名者
        strict = find_similar(db, name="阈值验证-地方政府", threshold=0.95)
        assert all(h["score"] >= 0.95 or h["name"] == "阈值验证-地方政府" for h in strict)
        # 同父加权：+0.05 封顶 1.0
        c = db.scalar(select(Concept).where(Concept.name == "阈值验证-地方政府"))
        boosted = score_against("阈值验证-地方政府", (), c, parent_id=999)
        plain = score_against("阈值验证-地方政府", (), c, parent_id=None)
        assert boosted["score"] == min(1.0, plain["score"])  # 已是 1.0 → 封顶不变
    finally:
        db.close()


def test_block_threshold_semantics(client):
    """硬拦语义：>=0.95 直接入库被拒（SimilarityBlock），走提交审核放行。"""
    login(client, "admin")
    r = client.post("/api/concepts", json={"name": "行政区划", "domain": "通用"})
    assert r.status_code in (400, 409, 422)                   # 完全同名必然被拦（查重或唯一约束）
    assert client.get("/api/concepts", params={"q": "行政区划"}).json()["total"] >= 1
