"""M21 · 抽检闭环：分层等距抽样 / 双人盲评 / 仲裁 / 关闭 / Wilson CI。"""
from conftest import login

KE_OK = "output={生命周期专用算子(a)|var a: String}"


def _mk_items(client, n=6, article="篇S", group="一组"):
    ids = []
    for i in range(n):
        r = client.post("/api/gold/items", json={
            "article_code": article, "ke_id": f"{article}-{i}", "group_name": group,
            "ke_text": KE_OK})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


def _auto_sample(client, **kw):
    payload = {"group_name": "一组", "article_code": "篇S", "size": 4, "seed": 42}
    payload.update(kw)
    r = client.post("/api/gold/samples/auto", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _review(client, sid, gid, verdict):
    return client.post(f"/api/gold/samples/{sid}/reviews",
                       json={"gold_item_id": gid, "verdict": verdict})


def test_auto_sample_create_deterministic(client):
    """同 seed 同总体 → 样本清单完全一致（可复现）；参数落库。"""
    login(client, "admin")
    ids = set(_mk_items(client, 6))
    sid1 = _auto_sample(client)
    sid2 = _auto_sample(client)
    d1 = client.get(f"/api/gold/samples/{sid1}").json()
    d2 = client.get(f"/api/gold/samples/{sid2}").json()
    gids1 = sorted(i["gold_item_id"] for i in d1["items"])
    gids2 = sorted(i["gold_item_id"] for i in d2["items"])
    assert gids1 == gids2 and len(gids1) == 4
    assert set(gids1) <= ids
    assert d1["sample"]["params"]["seed"] == 42
    assert d1["sample"]["params"]["population"] >= 6
    assert d1["stats"]["total"] == 4 and d1["stats"]["finalized"] == 0


def test_blind_dual_review_and_agreement(client):
    """双人一致自动落定；他人结论对未评者盲化；重复打标 409。"""
    login(client, "admin")
    _mk_items(client, 6, article="篇B")
    sid = _auto_sample(client, article_code="篇B")
    gid = client.get(f"/api/gold/samples/{sid}").json()["items"][0]["gold_item_id"]

    login(client, "reviewer")
    r = _review(client, sid, gid, "通过")
    assert r.status_code == 201
    assert _review(client, sid, gid, "通过").status_code == 409   # 幂等：重复打标 409

    # admin 未评该条 → 盲化：看不到 reviewer 的结论
    login(client, "admin")
    item = [i for i in client.get(f"/api/gold/samples/{sid}").json()["items"]
            if i["gold_item_id"] == gid][0]
    assert item["blind"] is True and item["reviews"] == [] and item["final_verdict"] is None

    # admin 评同一结论 → 一致 → 自动定局
    assert _review(client, sid, gid, "通过").status_code == 201
    item = [i for i in client.get(f"/api/gold/samples/{sid}").json()["items"]
            if i["gold_item_id"] == gid][0]
    assert item["final_verdict"] == "通过" and item["blind"] is False
    assert len(item["reviews"]) == 2


def test_disagreement_and_arbitration(client):
    """双人分歧 → 待仲裁；无分歧仲裁被拒；仲裁落定并留仲裁人。"""
    login(client, "admin")
    _mk_items(client, 6, article="篇A")
    sid = _auto_sample(client, article_code="篇A")
    gids = [i["gold_item_id"] for i in client.get(f"/api/gold/samples/{sid}").json()["items"]]
    gid = gids[0]

    login(client, "reviewer")
    _review(client, sid, gid, "通过")
    # 单人不构成分歧 → 仲裁 400
    r = client.post(f"/api/gold/samples/{sid}/items/{gid}/arbitrate", json={"verdict": "不通过"})
    assert r.status_code == 400
    login(client, "admin")
    _review(client, sid, gid, "不通过")
    item = [i for i in client.get(f"/api/gold/samples/{sid}").json()["items"]
            if i["gold_item_id"] == gid][0]
    assert item["final_verdict"] is None
    assert client.get(f"/api/gold/samples/{sid}").json()["stats"]["disagreement_count"] == 1

    r = client.post(f"/api/gold/samples/{sid}/items/{gid}/arbitrate",
                    json={"verdict": "不通过", "note": "KE 内容不完整"})
    assert r.status_code == 200 and r.json()["final_verdict"] == "不通过"


def test_close_requires_all_final(client):
    login(client, "admin")
    _mk_items(client, 6, article="篇C")
    sid = _auto_sample(client, article_code="篇C", size=2)
    # 未评完 → 关闭 409
    assert client.post(f"/api/gold/samples/{sid}/close").status_code == 409

    items = client.get(f"/api/gold/samples/{sid}").json()["items"]
    for i in items:
        login(client, "reviewer")
        _review(client, sid, i["gold_item_id"], "通过")
        login(client, "admin")
        _review(client, sid, i["gold_item_id"], "通过")
    r = client.post(f"/api/gold/samples/{sid}/close")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "closed" and d["passed"] == d["total"] == 2
    # 关闭后拒写
    assert _review(client, sid, items[0]["gold_item_id"], "不通过").status_code == 409


def test_wilson_and_stats_api(client):
    """通过率带 Wilson CI；一致率口径 = 双评一致 / 双评完成。"""
    login(client, "admin")
    _mk_items(client, 6, article="篇W")
    sid = _auto_sample(client, article_code="篇W", size=2)
    items = client.get(f"/api/gold/samples/{sid}").json()["items"]
    # 条目1：双人一致通过；条目2：reviewer 通过 + admin 不通过 → 分歧后仲裁不通过
    login(client, "reviewer")
    _review(client, sid, items[0]["gold_item_id"], "通过")
    _review(client, sid, items[1]["gold_item_id"], "通过")
    login(client, "admin")
    _review(client, sid, items[0]["gold_item_id"], "通过")
    _review(client, sid, items[1]["gold_item_id"], "不通过")
    client.post(f"/api/gold/samples/{sid}/items/{items[1]['gold_item_id']}/arbitrate",
                json={"verdict": "不通过"})
    d = client.get(f"/api/gold/samples/{sid}").json()["stats"]
    assert d["finalized"] == 2 and d["passed"] == 1
    assert d["pass_rate"] == 0.5
    lo, hi = d["wilson_lo"], d["wilson_hi"]
    assert lo is not None and lo < 0.5 < hi and hi - lo > 0.5   # n=2 区间很宽
    assert d["paired"] == 2 and d["agree"] == 1 and d["agreement_rate"] == 0.5


def test_manual_sample_still_works(client):
    """旧手工登记兼容：kind=manual，无样本清单（进详情 400）。"""
    login(client, "reviewer")
    r = client.post("/api/gold/samples", json={
        "sample_date": "2026-10-26", "scope": "育儿·一组", "total": 10, "passed": 9})
    assert r.status_code == 201
    sid = r.json()["id"]
    items = client.get("/api/gold/samples").json()["items"]
    row = [s for s in items if s["id"] == sid][0]
    assert row["kind"] == "manual"
    assert client.get(f"/api/gold/samples/{sid}").status_code == 400


def test_sampler_role_guard(client):
    """标注员（被抽方）不能抽样/打标（回避）。"""
    login(client, "annotator")
    r = client.post("/api/gold/samples/auto", json={"size": 1})
    assert r.status_code == 403

    login(client, "admin")
    _mk_items(client, 3, article="篇G")
    sid = _auto_sample(client, article_code="篇G", size=2)
    gid = client.get(f"/api/gold/samples/{sid}").json()["items"][0]["gold_item_id"]
    login(client, "annotator")
    assert _review(client, sid, gid, "通过").status_code == 403
