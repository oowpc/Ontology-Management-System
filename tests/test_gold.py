"""M8 验证：GOLDKE 篇级任务、泳道、issue、抽查、统计（《数据标注执行方案》口径）。"""
from conftest import login


def test_seed_articles_and_stats(client):
    login(client, "annotator")
    r = client.get("/api/gold/articles")
    assert r.status_code == 200
    assert r.json()["total"] == 13  # 方案预置 13 篇
    stats = client.get("/api/gold/stats").json()
    assert stats["target_new"] == 400 and stats["target_total"] == 800
    assert stats["by_domain"]["育儿"]["articles"] == 10
    assert stats["by_group"]["三组"]["articles"] == 5  # 篇3,6,11,12,13


def test_article_crud_and_stage_flow(client):
    login(client, "annotator")
    r = client.post("/api/gold/articles", json={
        "code": "篇14", "title": "化学领域·篇14", "domain": "化学", "group_name": "二组",
    })
    assert r.status_code == 201
    aid = r.json()["id"]
    assert r.json()["stage"] == "co_annotation"
    assert client.post("/api/gold/articles", json={
        "code": "篇14", "title": "重复", "group_name": "二组"}).status_code == 409
    # 阶段推进：co_annotation → co_merge → ke_annotation → done
    for expect in ("co_merge", "ke_annotation", "done"):
        r = client.post(f"/api/gold/articles/{aid}/advance")
        assert r.json()["stage"] == expect
    assert client.post(f"/api/gold/articles/{aid}/advance").status_code == 400
    # 更新完成条数
    r = client.patch(f"/api/gold/articles/{aid}", json={"ke_done": 27})
    assert r.json()["ke_done"] == 27


def test_schedule_grid_and_cell_update(client):
    login(client, "annotator")
    r = client.get("/api/gold/schedule")
    grid = r.json()["grid"]
    # 方案预置：day1 上午三组都有任务；day2 全空
    assert grid["1"]["上午"]["一组"]["task"].startswith("CO 标注与对齐")
    assert grid["1"]["上午"]["三组"]["article_code"] == "篇3"
    assert grid["2"]["上午"]["一组"] is None
    # 更新单元格状态
    r = client.post("/api/gold/schedule/cell", json={
        "day_index": 1, "slot": "上午", "group_name": "一组",
        "task": "CO 标注与对齐（篇1-育儿）", "article_code": "篇1", "status": "done",
    })
    assert r.json()["status"] == "done"
    stats = client.get("/api/gold/stats").json()
    assert stats["cell_status"]["done"] >= 1
    # 非法组别
    assert client.post("/api/gold/schedule/cell", json={
        "day_index": 1, "slot": "上午", "group_name": "D组"}).status_code == 400


def test_issue_flow(client):
    login(client, "annotator")
    r = client.post("/api/gold/issues", json={
        "title": "粒度分歧：部门派出机构是否单独建概念", "cluster": "粒度分歧",
        "related_type": "article", "related_ref": "篇1",
        "content": "我的思考：参照方法论原则5…",
    })
    assert r.status_code == 201
    iid = r.json()["id"]
    # 话题+评论
    login(client, "reviewer")
    r = client.post(f"/api/gold/issues/{iid}/comments", json={"content": "同意按原则5处理"})
    assert r.status_code == 201 and len(r.json()["comments"]) == 1
    # 解决
    r = client.post(f"/api/gold/issues/{iid}/status",
                    json={"status": "resolved", "resolution": "按原则5，不单独建概念"})
    assert r.json()["status"] == "resolved"
    assert r.json()["resolved_at"] is not None
    # 过滤
    items = client.get("/api/gold/issues", params={"status": "open"}).json()["items"]
    assert all(i["status"] == "open" for i in items)
    items = client.get("/api/gold/issues", params={"cluster": "粒度分歧"}).json()["items"]
    assert len(items) == 1


def test_samples(client):
    login(client, "reviewer")
    r = client.post("/api/gold/samples", json={
        "sample_date": "2026-10-20", "scope": "育儿·一组·篇1", "total": 10, "passed": 9,
    })
    assert r.status_code == 201 and r.json()["pass_rate"] == 0.9
    assert client.post("/api/gold/samples", json={
        "sample_date": "2026-10-20", "scope": "x", "total": 5, "passed": 9}).status_code == 400
    items = client.get("/api/gold/samples").json()["items"]
    assert items[0]["scope"] == "育儿·一组·篇1"


def test_gold_pages(client):
    login(client, "annotator")
    for url in ("/gold", "/gold/articles", "/gold/schedule", "/gold/issues", "/gold/samples"):
        assert client.get(url).status_code == 200, url
    body = client.get("/gold").text
    assert "800" in body and "育儿" in body
    # 页面推进阶段
    r = client.post("/gold/articles/1/advance", follow_redirects=False)
    assert r.status_code == 303
    # 页面建 issue
    r = client.post("/gold/issues/new", data={
        "title": "页面登记的分歧", "cluster": "写法", "content": "思考…",
    }, follow_redirects=False)
    assert r.status_code == 303
    body = client.get("/gold/issues").text
    assert "页面登记的分歧" in body
