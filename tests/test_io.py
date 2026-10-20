"""M6 验证：导出导入幂等（验收5）、CSV 往返、字段映射、权限。"""
import json

from conftest import login


def _normalized(payload):
    payload = dict(payload)
    payload.pop("exported_at", None)
    payload["concepts"] = sorted(payload["concepts"], key=lambda c: c["name"])
    payload["operators"] = sorted(payload["operators"], key=lambda o: (o["name"], o["subject"] or ""))
    return payload


def test_export_import_idempotent(client):
    """验收5：导出 → 清空 → 导入 → 再导出，结构完全一致；二次导入零变化。"""
    login(client, "admin")
    export1 = _normalized(client.get("/api/export/json").json())

    # 直接清库（模拟导入到空环境）
    from app.db import SessionLocal
    from app.models import Concept, Operator, UsageStat
    db = SessionLocal()
    db.query(UsageStat).delete()
    db.query(Operator).delete()
    db.query(Concept).delete()
    db.commit()
    db.close()

    # 导入回全部内容
    r = client.post("/api/import/json", content=json.dumps(export1, ensure_ascii=False),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    stats = r.json()
    assert stats["concepts_created"] == len(export1["concepts"])
    assert stats["operators_created"] == len(export1["operators"])

    # 再导出：结构完全一致
    export2 = _normalized(client.get("/api/export/json").json())
    assert export1 == export2

    # 二次导入：全部「未变」（幂等）
    r = client.post("/api/import/json", content=json.dumps(export2, ensure_ascii=False),
                    headers={"Content-Type": "application/json"})
    stats2 = r.json()
    assert stats2["concepts_created"] == 0 and stats2["concepts_updated"] == 0
    assert stats2["operators_created"] == 0 and stats2["operators_updated"] == 0
    assert stats2["concepts_unchanged"] == len(export2["concepts"])


def test_import_small_payload_twice(client):
    login(client, "admin")
    payload = {"concepts": [
        {"name": "导入根概念", "aliases": ["导入根"], "definition": "v1", "parent": None, "status": "approved"},
        {"name": "导入子概念", "parent": "导入根概念"},
    ], "operators": [
        {"name": "导入算子", "subject": "导入根概念", "param_types": ["String"], "return_type": "Number"},
    ]}
    body = json.dumps(payload, ensure_ascii=False)
    r1 = client.post("/api/import/json", content=body, headers={"Content-Type": "application/json"}).json()
    assert r1["concepts_created"] == 2 and r1["operators_created"] == 1
    r2 = client.post("/api/import/json", content=body, headers={"Content-Type": "application/json"}).json()
    assert r2["concepts_created"] == 0 and r2["concepts_updated"] == 0
    assert r2["operators_created"] == 0
    # 子概念父类已挂上
    from conftest import concept_id_by_name
    cid = concept_id_by_name(client, "导入子概念")
    d = client.get(f"/api/concepts/{cid}").json()
    assert d["parent_name"] == "导入根概念"


def test_csv_roundtrip(client):
    login(client, "admin")
    r = client.get("/api/export/csv", params={"kind": "concepts"})
    assert r.status_code == 200 and "名称" in r.text or "name" in r.text
    stats = client.post("/api/import/csv", content=r.content,
                        headers={"Content-Type": "text/csv"}).json()
    assert stats["concepts_created"] == 0 and stats["concepts_updated"] == 0
    assert stats["concepts_unchanged"] > 0


def test_field_map(client):
    login(client, "admin")
    fm = json.dumps({"name": "概念名"}, ensure_ascii=False)
    r = client.get("/api/export/json", params={"field_map": fm})
    assert "概念名" in r.json()["concepts"][0]
    # 用同一映射导入：名称键被反向还原，幂等
    stats = client.post("/api/import/json", params={"field_map": fm},
                        content=r.content, headers={"Content-Type": "application/json"}).json()
    assert stats["concepts_created"] == 0 and stats["concepts_updated"] == 0


def test_io_requires_admin(client):
    login(client, "annotator")
    assert client.get("/api/export/json").status_code == 403
    assert client.post("/api/import/json", content="{}").status_code == 403


def test_io_page_and_form_import(client):
    login(client, "admin")
    assert client.get("/io").status_code == 200
    r = client.post("/io/import", data={
        "format": "json",
        "text": json.dumps({"concepts": [{"name": "页面导入概念", "parent": "机构"}]},
                           ensure_ascii=False),
    })
    assert r.status_code == 200 and "新建 1" in r.text
