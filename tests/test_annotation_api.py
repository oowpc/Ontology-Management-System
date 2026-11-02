"""Agent API：标注批次全流程（JSON，无表单）。"""
import io
import uuid
import zipfile

from conftest import login


def test_annotation_api_full_flow(client):
    batch_id = "API-ANN-" + uuid.uuid4().hex[:6]
    login(client, "reviewer")
    r = client.post("/api/annotation/batches", json={
        "batch_id": batch_id, "name": "API 标注批次",
        "source_name": "API 来源", "source_version": "v1",
        "source_checksum": "api-checksum", "source_type": "policy",
        "domain": "测试", "guide_version": "api-v1",
        "annotator_a": "annotator", "annotator_b": "annotator2",
        "lead": "reviewer", "arbiter": "admin",
        "cross_checkers": ["annotator3", "reviewer"], "project_owner": "admin",
    })
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    assert r.json()["state"] == "draft"
    page = client.get(f"/annotation/batches/{bid}")
    assert page.status_code == 200 and 'data-tabgroup' in page.text and "标注单元" in page.text
    assert client.get("/annotation").status_code == 200

    r = client.post(f"/api/annotation/batches/{bid}/units", json={"units": [{
        "unit_id": "U-API-1", "source_id": "doc-api",
        "source_type": "policy", "raw_text": "API 测试原文", "language": "zh",
    }]})
    assert r.status_code == 200 and r.json()["created"] == 1, r.text
    units = client.get(f"/api/annotation/batches/{bid}/units").json()["items"]
    assert len(units) == 1
    uid = units[0]["id"]

    for action, expected in (("freeze", "dataset_frozen"), ("start_co", "co_blind")):
        r = client.post(f"/api/annotation/batches/{bid}/state", json={"action": action})
        assert r.status_code == 200 and r.json()["state"] == expected, r.text

    login(client, "annotator")
    r = client.post(f"/api/annotation/units/{uid}/evidence", json={
        "source_id": "doc-api", "quote": "API 证据", "locator": {"page": 1},
    })
    assert r.status_code == 201, r.text
    evid = r.json()["id"]
    r = client.post(f"/api/annotation/units/{uid}/co", json={
        "side": "A", "concepts": ["API概念A", "API概念B"],
        "operators": [], "evidence_ids": [evid], "reason": "A",
    })
    assert r.status_code == 201, r.text
    # Blind isolation: A cannot see B's CO before alignment.
    detail = client.get(f"/api/annotation/units/{uid}").json()
    assert "A" in detail["co"] and "B" not in detail["co"]

    login(client, "annotator2")
    r = client.post(f"/api/annotation/units/{uid}/co", json={
        "side": "B", "concepts": ["API概念A", "API概念B"], "reason": "B",
        "evidence_ids": [evid],
    })
    assert r.status_code == 201, r.text

    login(client, "reviewer")
    r = client.post(f"/api/annotation/batches/{bid}/state", json={"action": "start_alignment"})
    assert r.json()["state"] == "co_alignment"
    login(client, "annotator")
    r = client.post(f"/api/annotation/units/{uid}/align", json={
        "conclusion": "aligned", "concepts": ["API概念A", "API概念B"],
        "concept_rows": [{"name": "API概念A"}, {"name": "API概念B"}],
        "operator_rows": [{"name": "API算子", "subject": "API概念A",
                           "return_type": "API概念B"}],
        "evidence_ids": [evid], "reason": "一致",
    })
    assert r.status_code == 201, r.text

    login(client, "reviewer")
    assert client.post(f"/api/annotation/batches/{bid}/state",
                       json={"action": "start_merge"}).json()["state"] == "co_merge"
    r = client.post(f"/api/annotation/batches/{bid}/apply", json={"label": batch_id + "-v1"})
    assert r.status_code == 200, r.text
    vid = r.json()["version_id"]
    login(client, "admin")
    assert client.post(f"/api/annotation/versions/{vid}/freeze").status_code == 200
    assert client.post(f"/api/annotation/versions/{vid}/release").status_code == 200
    login(client, "reviewer")
    assert client.post(f"/api/annotation/batches/{bid}/state",
                       json={"action": "start_ke"}).json()["state"] == "ke_blind"

    for username, side in (("annotator", "A"), ("annotator2", "B")):
        login(client, username)
        r = client.post(f"/api/annotation/units/{uid}/ke", json={
            "side": side, "content": f"API KE {side}", "self_check": {"ok": True},
            "evidence_ids": [evid],
        })
        assert r.status_code == 201, r.text

    login(client, "reviewer")
    assert client.post(f"/api/annotation/batches/{bid}/state",
                       json={"action": "start_gold"}).json()["state"] == "gold_merge"
    r = client.post(f"/api/annotation/units/{uid}/gold", json={
        "source": "A", "content": "API GOLD", "reason": "一致",
        "evidence_ids": [evid],
    })
    assert r.status_code == 201, r.text
    assert client.post(f"/api/annotation/batches/{bid}/state",
                       json={"action": "start_cross"}).json()["state"] == "cross_review"

    login(client, "reviewer")
    assert client.post(f"/api/annotation/batches/{bid}/cross-review", json={
        "scope": "all", "conclusion": "pass",
    }).status_code == 201
    login(client, "annotator3")
    assert client.post(f"/api/annotation/batches/{bid}/cross-review", json={
        "scope": "all", "conclusion": "pass",
    }).status_code == 201
    login(client, "reviewer")
    assert client.post(f"/api/annotation/batches/{bid}/state",
                       json={"action": "validate"}).json()["state"] == "validation"
    login(client, "admin")
    assert client.post(f"/api/annotation/batches/{bid}/state",
                       json={"action": "pass"}).json()["state"] == "passed"

    versions = client.get("/api/annotation/versions", params={"batch_id": bid}).json()
    assert versions["total"] >= 1 and versions["items"][0]["status"] == "released"
    payload = client.get(f"/api/annotation/versions/{vid}/payload")
    assert payload.status_code == 200
    assert len(payload.json()["concepts"]) >= 2 and payload.json()["concepts"][0]["id"]
    rb = client.post(f"/api/annotation/versions/{vid}/rollback")
    assert rb.status_code == 201 and rb.json()["label"].startswith("RB-")
    assert rb.json()["status"] == "draft"
    rid = rb.json()["id"]
    diff = client.get(f"/api/annotation/versions/{rid}/diff")
    assert diff.status_code == 200 and "concepts" in diff.json()["diff"]
    assert client.get(f"/annotation/versions/{rid}/diff").status_code == 200
    r = client.get(f"/api/annotation/batches/{bid}/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/zip")
    assert "co_alignments.json" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()


def test_annotation_units_export_and_bulk_delete(client):
    import uuid
    batch_id = "API-UNIT-" + uuid.uuid4().hex[:6]
    login(client, "reviewer")
    r = client.post("/api/annotation/batches", json={
        "batch_id": batch_id, "name": "单元批量测试",
        "source_name": "s", "source_checksum": "x", "guide_version": "v1",
        "annotator_a": "annotator", "annotator_b": "annotator2",
        "cross_checkers": ["annotator3", "reviewer"], "project_owner": "admin",
    })
    bid = r.json()["id"]
    client.post(f"/api/annotation/batches/{bid}/units", json={"units": [{
        "unit_id": "U-BULK-1", "source_id": "d", "source_type": "policy",
        "raw_text": "批量导出测试",
    }]})
    uid = client.get(f"/api/annotation/batches/{bid}/units").json()["items"][0]["id"]
    assert client.get(f"/api/annotation/batches/{bid}/units/export").status_code == 200
    assert client.get(f"/api/annotation/batches/{bid}/units/export",
                      params={"format": "csv"}).text.startswith("\ufeff")
    r = client.post(f"/api/annotation/batches/{bid}/units/bulk-delete",
                    json={"unit_ids": [uid]})
    assert r.status_code == 200 and r.json()["units"] == 1
    assert client.get(f"/api/annotation/batches/{bid}/units").json()["total"] == 0
