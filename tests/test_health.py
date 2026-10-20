"""M5 验证：四类体检、报告落库、CSV、使用统计扫描（验收4）。"""
from conftest import concept_id_by_name, login


def _setup(client):
    login(client, "admin")
    # 孤立节点：无父类且不在根白名单
    client.post("/api/concepts", json={"name": "孤立测试概念"})
    # 未使用概念：有父类但无使用记录
    jid = concept_id_by_name(client, "机构")
    client.post("/api/concepts", json={"name": "未使用测试概念", "parent_id": jid})


def test_health_run_and_latest(client):
    """验收4：体检正确列出孤立节点与未使用概念。"""
    _setup(client)
    login(client, "reviewer")
    r = client.post("/api/health/run")
    assert r.status_code == 200
    summary = r.json()["summary"]
    assert summary["orphan"] >= 1 and summary["unused"] >= 1

    report = client.get("/api/health/latest").json()["report"]
    orphan_names = [x["name"] for x in report["details"]["orphan"]]
    unused_names = [x["name"] for x in report["details"]["unused"]]
    assert "孤立测试概念" in orphan_names
    assert "未使用测试概念" in unused_names
    # 根白名单不算孤立
    assert "机构" not in orphan_names and "行政区划" not in orphan_names


def test_health_similar_pairs(client):
    login(client, "reviewer")
    client.post("/api/health/run")
    report = client.get("/api/health/latest").json()["report"]
    pairs = {(p["a_name"], p["b_name"]) for p in report["details"]["similar_pairs"]}
    assert any({"行政机关", "地方政府"} == {a, b} for a, b in pairs)  # 别名「政府」包含命中


def test_health_operator_counts(client):
    login(client, "reviewer")
    client.post("/api/health/run")
    report = client.get("/api/health/latest").json()["report"]
    items = {x["name"]: x for x in report["details"]["operator_count"]}
    assert "地方政府" in items and items["地方政府"]["issue"] == "过少"  # 无算子


def test_health_csv(client):
    login(client, "reviewer")
    client.post("/api/health/run")
    r = client.get("/api/health/report.csv", params={"kind": "orphan"})
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "孤立测试概念" in r.text
    # 历史
    hist = client.get("/api/health/history").json()["items"]
    assert len(hist) >= 1


def test_usage_scan_removes_from_unused(client):
    login(client, "admin")
    jid = concept_id_by_name(client, "机构")
    cid = client.post("/api/concepts", json={"name": "扫描验证概念", "parent_id": jid}).json()["id"]
    login(client, "reviewer")
    client.post("/api/health/run")
    assert cid in [x["id"] for x in client.get("/api/health/latest").json()["report"]["details"]["unused"]]
    # 扫描 KE 文本后不再算未使用
    r = client.post("/api/usage/scan", json={"text": "扫描验证概念出现了两次，扫描验证概念", "source": "测试"})
    assert any(h["concept_id"] == cid and h["count"] == 2 for h in r.json()["hits"])
    client.post("/api/health/run")
    assert cid not in [x["id"] for x in client.get("/api/health/latest").json()["report"]["details"]["unused"]]


def test_health_page(client):
    login(client, "reviewer")
    client.post("/api/health/run")
    body = client.get("/health").text
    assert "本体体检" in body and "下载本类 CSV" in body
    assert client.get("/health", params={"kind": "orphan"}).status_code == 200
    # 标注员不可见
    login(client, "annotator")
    assert client.get("/health").status_code == 403
