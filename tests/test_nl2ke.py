"""NL2KE BYOS 桥：子本体构建、OMS 页面/运行记录（不调用真实 LLM）。"""
import json

import pytest

from conftest import login


@pytest.fixture()
def archer_src(tmp_path, monkeypatch):
    from app import nl2ke
    (tmp_path / "super_ontology.json").write_text(json.dumps({
        "concepts": {
            "Singer": {"sources": {"concert_singer": {
                "database": "concert_singer", "table": "singer",
                "slots": {"SingerId": "Singer_ID", "Name": "Name"},
                "pk": ["Singer_ID"]}}}
        },
        "operators": {
            "SingerName": {"type": "PROPERTY", "concept": "Singer",
                           "slot": "Name", "databases": ["concert_singer"]}
        },
    }), encoding="utf-8")
    (tmp_path / "tables.json").write_text(json.dumps([{
        "db_id": "concert_singer",
        "table_names_original": ["singer"], "table_names": ["singer"],
        "column_names_original": [[-1, "*"], [0, "Singer_ID"], [0, "Name"]],
        "column_names": [[-1, "*"], [0, "Singer_ID"], [0, "Name"]],
        "column_types": ["integer", "text"], "primary_keys": [1],
        "foreign_keys": [],
    }]), encoding="utf-8")
    (tmp_path / "en_dev.json").write_text(json.dumps([{
        "db_id": "concert_singer", "question": "How many singers?",
        "query": "SELECT COUNT(*) FROM singer",
    }]), encoding="utf-8")
    monkeypatch.setattr(nl2ke, "ARCHER_SRC", tmp_path)
    monkeypatch.setattr(nl2ke, "QUESTION_DIRS", (tmp_path,))
    return tmp_path


def test_archer_sub_ontology(archer_src):
    from app import nl2ke
    assert "concert_singer" in nl2ke.databases()
    table, ontology = nl2ke.sub_ontology("concert_singer")
    assert table["db_id"] == "concert_singer"
    assert len(ontology["concepts"]) >= 1
    assert all("concert_singer" in c["sources"] for c in ontology["concepts"].values())
    assert len(ontology["operators"]) >= 1


def test_nl2ke_page_and_mocked_run(client, monkeypatch, archer_src):
    login(client, "admin")
    r = client.post("/api/libraries", json={
        "key": "archer", "name": "Archer Benchmark", "kind": "reference"})
    assert r.status_code in (201, 409), r.text
    r = client.get("/library/switch", params={"key": "archer", "next": "/nl2ke"},
                   follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/nl2ke")
    assert page.status_code == 200
    assert "KE 等价校验" in page.text
    assert len(client.get("/api/nl2ke/databases").json()["items"]) >= 1

    from app.routers import nl2ke as nl2ke_router

    def fake_run(db, db_id, question, **kw):
        return {"db_id": db_id, "ke": "output = Count({x | x: Singer});",
                "latency_ms": 12, "verify_passed": True, "attempts": 1,
                "request_id": "req-test", "usage": {"in": 1, "out": 2},
                "timings": {"generate": 12},
                "trace": {"mode": "verified", "schema": {"table_count": 1},
                          "verify_loop": {"attempts": 1, "history": []}}}
    monkeypatch.setattr(nl2ke_router.bridge, "run", fake_run)
    r = client.post("/api/nl2ke/run", json={
        "db_id": "concert_singer", "question": "How many singers?", "verify": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ke"].startswith("output")
    assert body["db_id"] == "concert_singer"
    assert body["trace"]["mode"] == "verified"
    assert client.get(f"/api/nl2ke/runs/{body['id']}").json()["trace"]["mode"] == "verified"
    overview = client.get(f"/nl2ke/runs/{body['id']}")
    assert overview.status_code == 200, overview.text
    assert "NL2KE 内部步骤" in overview.text
    for step, label in (("schema", "Schema"), ("meta", "问题元信息"),
                        ("ontology", "本体精炼"), ("fewshot", "Few-shot"),
                        ("attempts", "生成与校验"), ("prompt", "初始 Prompt"),
                        ("timings", "分阶段耗时")):
        page = client.get(f"/nl2ke/runs/{body['id']}/steps/{step}")
        assert page.status_code == 200, (step, page.text)
        assert label in page.text
    assert client.get(f"/nl2ke/runs/{body['id']}/steps/nope").status_code == 404
    runs = client.get("/api/nl2ke/runs").json()["items"]
    assert any(x["request_id"] == "req-test" for x in runs)
    assert all("trace" not in x or not x["trace"] for x in runs)

    r = client.post(f"/api/nl2ke/runs/{body['id']}/accept")
    assert r.status_code == 200, r.text
    gid = r.json()["gold_item_id"]
    assert gid
    assert client.get("/api/nl2ke/runs").json()["items"][0]["status"] == "accepted"
    vr = client.get("/api/validation/results", params={
        "object_type": "gold_item", "object_id": gid}).json()
    assert vr["total"] >= 1 and vr["items"][0]["stage"] == "accept"

    r = client.post(f"/api/nl2ke/runs/{body['id']}/to-batch")
    assert r.status_code == 200, r.text
    assert r.json()["batch_id"] and r.json()["unit_id"]
    assert r.json()["batch_url"].startswith("/annotation/batches/")
    assert client.get("/api/nl2ke/batches").json()["items"]

    r = client.post("/api/nl2ke/batch", json={
        "db_id": "concert_singer", "questions": ["How many singers?", "List stadiums."],
        "verify": False})
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 2


def test_nl2ke_equivalence_api(client, monkeypatch):
    login(client, "admin")
    r = client.post("/api/libraries", json={
        "key": "archer", "name": "Archer Benchmark", "kind": "reference"})
    assert r.status_code in (201, 409), r.text
    r = client.get("/library/switch", params={"key": "archer", "next": "/nl2ke"},
                   follow_redirects=False)
    assert r.status_code == 303

    from app.routers import nl2ke as nl2ke_router

    def fake_run(db, db_id, question, **kw):
        return {"db_id": db_id, "ke": "output = Count({x | x: Singer});",
                "latency_ms": 3, "verify_passed": True, "attempts": 1,
                "request_id": "req-eq", "usage": {}, "timings": {}}

    monkeypatch.setattr(nl2ke_router.bridge, "run", fake_run)
    r = client.post("/api/nl2ke/run", json={
        "db_id": "concert_singer", "question": "How many singers?", "verify": False,
        "run_sql": False})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    calls = {}

    def fake_judge(reference_ke, candidate_ke, *, request_timeout_s=60):
        calls.update(reference=reference_ke, candidate=candidate_ke,
                     timeout=request_timeout_s)
        return {"status": "proved_equal", "verdict": "equal",
                "level": "proved_equal", "reason": "same normalized",
                "total_ms": 1.2, "evidence": []}

    monkeypatch.setattr(nl2ke_router.ke_matcher, "judge", fake_judge)
    r = client.post(f"/api/nl2ke/runs/{rid}/equivalence", json={
        "reference_ke": "output = Count({x | x: Singer});",
        "request_timeout_s": 30})
    assert r.status_code == 200, r.text
    assert r.json()["equivalence"]["status"] == "proved_equal"
    assert calls["candidate"] == "output = Count({x | x: Singer});"
    assert calls["reference"].startswith("output")
    assert calls["timeout"] == 30

    item = next(x for x in client.get("/api/nl2ke/runs").json()["items"]
                if x["id"] == rid)
    assert item["equivalence"]["status"] == "proved_equal"

    def fake_many(reference_ke, candidates, **kw):
        assert len(candidates) == 2
        return {"ok": True, "count": 2, "passed": 2, "failed": 0,
                "results": [{"index": 0, "ok": True, "status": "equal"},
                            {"index": 1, "ok": True, "status": "different"}]}

    monkeypatch.setattr(nl2ke_router.ke_matcher, "judge_many", fake_many)
    r = client.post(f"/api/nl2ke/runs/{rid}/equivalence/batch", json={
        "candidates": ["output = Count({x | x: Singer});", "output = 1;"]})
    assert r.status_code == 200, r.text
    assert r.json()["batch"]["count"] == 2

    def broken_judge(reference_ke, candidate_ke, **kw):
        raise RuntimeError("matcher down")

    monkeypatch.setattr(nl2ke_router.ke_matcher, "judge", broken_judge)
    r = client.post(f"/api/nl2ke/runs/{rid}/equivalence",
                    json={"reference_ke": "output = 1;"})
    assert r.status_code == 502

    r = client.get("/library/switch", params={"key": "co-baseline", "next": "/nl2ke"},
                   follow_redirects=False)
    assert r.status_code == 303
    r = client.post(f"/api/nl2ke/runs/{rid}/equivalence",
                    json={"reference_ke": "output = 1;"})
    assert r.status_code == 400
