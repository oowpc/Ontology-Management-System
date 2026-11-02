"""算子输入/输出：KEOL 风格签名、输入概念链接、输出概念链接。"""
import json

from conftest import login


def _concept(client, name):
    for item in client.get("/api/concepts", params={"q": name}).json()["items"]:
        if item["name"] == name:
            return item
    raise AssertionError(name)


def test_operator_keol_signature_and_output_concept(client):
    login(client, "admin")
    org = _concept(client, "机构")
    person = _concept(client, "人")
    gov = _concept(client, "地方政府")

    r = client.post("/api/operators", json={
        "name": "机构_是否有人",
        "subject_concept_id": org["id"],
        "param_types": ["人"],
        "return_type": "Bool",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["signature"] == "op 机构_是否有人(机构, 人)：布尔"
    assert [x["name"] for x in body["inputs"]] == ["机构", "人"]
    assert body["inputs"][0]["concept_id"] == org["id"]
    assert body["inputs"][1]["concept_id"] == person["id"]
    assert body["output_concept_id"] is None

    oid = body["id"]
    page = client.get(f"/operators/{oid}")
    assert page.status_code == 200
    assert "op 机构_是否有人(机构, 人)：布尔" in page.text

    r = client.patch(f"/api/operators/{oid}", json={"return_type": "地方政府"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["signature"] == "op 机构_是否有人(机构, 人)：地方政府"
    assert body["output_concept_id"] == gov["id"]
    assert body["output_name"] == "地方政府"

    # 导出带输入/输出，导入后仍一致
    exported = client.get("/api/export/json").json()
    row = next(x for x in exported["operators"] if x["name"] == "机构_是否有人")
    assert row["inputs"] == ["机构", "人"]
    assert row["output_concept"] == "地方政府"


def test_operator_paste_signature_parser(client):
    from app.paster import parse_keol
    parsed = parse_keol("op 机构_是否有人(机构,人)：Bool")
    assert not parsed["errors"]
    op = parsed["operators"][0]
    assert op["name"] == "机构_是否有人"
    assert op["subject"] == "机构"
    assert op["param_types"] == ["人"]
    assert op["return_type"] == "Bool"
