"""KE 多父类：解析合并 / API 读写 / 环检测 / 导入导出 / 审计回滚。"""
import json

from conftest import concept_id_by_name, login


def _mk(client, name, parents=None):
    payload = {"name": name}
    if parents is not None:
        payload["parents"] = parents
    r = client.post("/api/concepts", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_parse_multi_parent_lines():
    from app.paster import parse_keol
    parsed = parse_keol(
        "concept 多父类甲\n"
        "concept 多父类乙\n"
        "concept 多父类子 <: 多父类甲\n"
        "concept 多父类子 <: 多父类乙\n"
        "concept 多父类子\n"
    )
    rows = {c["name"]: c for c in parsed["concepts"]}
    assert rows["多父类子"]["parents"] == ["多父类甲", "多父类乙"]
    assert rows["多父类子"]["parent"] == "多父类甲"


def test_ke_validator_accepts_multi_parent():
    from app.ke.ke_validate import parse_ke
    parsed = parse_ke(
        "concept 校验甲\n"
        "concept 校验乙\n"
        "concept 校验子 <: 校验甲\n"
        "concept 校验子 <: 校验乙\n"
        "output={校验子(x)|var x: 校验子}\n"
    )
    assert not parsed.errors
    row = next(c for c in parsed.concepts if c["name"] == "校验子")
    assert row["parents"] == ["校验甲", "校验乙"]


def test_api_multi_parent_and_cycle(client):
    login(client, "admin")
    a = _mk(client, "多父A")
    b = _mk(client, "多父B")
    c = _mk(client, "多父C")
    x = _mk(client, "多父子", [a, b])

    d = client.get(f"/api/concepts/{x}").json()
    assert d["parent_id"] == a
    assert d["parent_names"] == ["多父A", "多父B"]
    assert [p["name"] for p in d["parents"]] == ["多父A", "多父B"]

    r = client.patch(f"/api/concepts/{x}", json={"parents": [c, b]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["parent_id"] == c
    assert d["parent_names"] == ["多父C", "多父B"]

    # 父类筛选走关联表：按次父类 B 也能查到子概念
    items = client.get("/api/concepts", params={"parent_id": b}).json()["items"]
    assert any(i["name"] == "多父子" for i in items)
    # 详情页/编辑页展示多父类
    body = client.get(f"/concepts/{x}").text
    assert "多父C" in body and "多父B" in body and "2 个父类" in body
    edit = client.get(f"/concepts/{x}/edit").text
    assert "其他父类" in edit and 'id="picker-extras"' in edit

    # 通过次父类形成环也应被拒绝：X 已挂在 C 下，再把 C 挂到 X
    r = client.patch(f"/api/concepts/{c}", json={"parents": [x]})
    assert r.status_code == 400
    assert "循环" in r.text


def test_import_multi_parent_idempotent(client):
    login(client, "admin")
    payload = {"concepts": [
        {"name": "倒入根", "parents": []},
        {"name": "倒入左", "parents": ["倒入根"]},
        {"name": "倒入右", "parents": ["倒入根"]},
        {"name": "倒入子", "parents": ["倒入左", "倒入右"]},
    ]}
    body = json.dumps(payload, ensure_ascii=False)
    r = client.post("/api/import/json", content=body,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 200 and r.json()["concepts_created"] == 4

    from app.db import SessionLocal
    from app.models import Concept, ConceptParent
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    db: Session = SessionLocal()
    try:
        cid = db.scalar(select(Concept.id).where(Concept.name == "倒入子"))
        links = db.scalars(
            select(ConceptParent).where(ConceptParent.concept_id == cid)
            .order_by(ConceptParent.position)).all()
        assert [l.is_primary for l in links] == [True, False]
        assert len(links) == 2
    finally:
        db.close()

    stats = client.post("/api/import/json", content=body,
                        headers={"Content-Type": "application/json"}).json()
    assert stats["concepts_created"] == 0 and stats["concepts_updated"] == 0


def test_export_contains_parents(client):
    login(client, "admin")
    a = _mk(client, "导出父A")
    b = _mk(client, "导出父B")
    _mk(client, "导出子", [a, b])
    payload = client.get("/api/export/json").json()
    row = next(c for c in payload["concepts"] if c["name"] == "导出子")
    assert row["parents"] == ["导出父A", "导出父B"]
    assert row["parent"] == "导出父A"


def test_audit_rollback_multi_parent(client):
    login(client, "admin")
    a = _mk(client, "回滚父A")
    b = _mk(client, "回滚父B")
    c = _mk(client, "回滚父C")
    x = _mk(client, "回滚子", [a, b])
    r = client.patch(f"/api/concepts/{x}", json={"parents": [c]})
    assert r.status_code == 200

    hist = client.get(f"/api/concepts/{x}/history").json()["items"]
    upd = hist[0]
    assert upd["before"]["parents"] == ["回滚父A", "回滚父B"]
    assert upd["after"]["parents"] == ["回滚父C"]

    r = client.post(f"/api/concepts/{x}/rollback/{upd['id']}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["parent_names"] == ["回滚父A", "回滚父B"]
