"""M20 · KE 校验引擎：方言解析 / 分级口径 / 定稿门槛 / 批量与单条 API。"""
import json

from conftest import login

GOOD_KE = "output = { PolicyStartDate(p) | p: Policy }"

BAD_KE_NO_OUTPUT = "concept 制度\nconcept 育儿补贴<:制度\n"

MESSY_KE = """1.这是讨论笔记（不是 KE）
concept 育儿补贴<:补贴
"""


def _create_item(client, ke_text, ke_id="T-1", article="篇X"):
    r = client.post("/api/gold/items", json={
        "article_code": article, "ke_id": ke_id, "group_name": "一组", "ke_text": ke_text})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _set_status(client, iid, status):
    return client.post(f"/api/gold/items/{iid}/status", json={"status": status})


def test_validate_good_ke(client):
    login(client, "admin")
    iid = _create_item(client, GOOD_KE)
    r = client.post(f"/api/gold/items/{iid}/validate")
    d = r.json()
    assert d["ok"] is True
    assert d["error_count"] == 0
    # 概念/算子未入库 → warning（词表提示不阻断；Syntastic 与内部降级都可能命中）
    assert d["warning_count"] >= 1
    assert d["stats"]["outputs"] == 1


def test_sqlke_output_shape(client):
    """OMS 只接受 SQLke 输出形态；旧方言不应再作为合法语法。"""
    login(client, "admin")
    ke = "output = { IssueDate(s) | s: Subsidy, IssueAmount(s) >= 100 }"
    iid = _create_item(client, ke, ke_id="T-sqlke")
    d = client.post(f"/api/gold/items/{iid}/validate").json()
    assert d["ok"] is True, d["errors"]
    assert d["stats"]["outputs"] == 1


def test_missing_output_is_error_and_blocks_finalize(client):
    login(client, "admin")
    iid = _create_item(client, BAD_KE_NO_OUTPUT, ke_id="T-noout")
    d = client.post(f"/api/gold/items/{iid}/validate").json()
    assert d["ok"] is False
    assert any(e["kind"] == "no_output" for e in d["errors"])
    # 定稿门槛：结构错误 → 422
    r = _set_status(client, iid, "gold")
    assert r.status_code == 422
    assert "定稿校验未通过" in r.json()["detail"]
    # reviewed 不受门槛限制
    assert _set_status(client, iid, "reviewed").status_code == 200


def test_discussion_notes_flagged(client):
    login(client, "admin")
    iid = _create_item(client, MESSY_KE, ke_id="T-note")
    d = client.post(f"/api/gold/items/{iid}/validate").json()
    assert d["ok"] is False
    assert any(e["kind"] == "unrecognized_line" for e in d["errors"])


def test_arity_mismatch_against_library(client):
    """库内已知签名的 arity 冲突是 error（用种子算子；先查一个现成算子）。"""
    login(client, "admin")
    ops = client.get("/api/operators").json()["items"]
    assert ops, "种子数据应带算子"
    op = ops[0]
    argc = 1 + len(op.get("param_types") or [])
    # 旧方言 `var` 已不再支持；该用例只保留为内部 arity 规则回归时，
    # 需要构造 SQLke；当前先跳过外部严格语法无法覆盖的中文种子算子。
    import pytest
    pytest.skip("seed operators use legacy Chinese names; strict SQLke arity test moved")
    iid = _create_item(client, ke, ke_id="T-arity")
    d = client.post(f"/api/gold/items/{iid}/validate").json()
    assert any(e["kind"] == "arity_mismatch" for e in d["errors"])


def test_validate_all_endpoint(client):
    login(client, "admin")
    _create_item(client, GOOD_KE, ke_id="T-va1")
    _create_item(client, BAD_KE_NO_OUTPUT, ke_id="T-va2")
    # 创建/更新时已即时校验（缓存为当前引擎版本）→ 批量只补陈旧缓存，幂等
    s = client.post("/api/gold/items/validate-all").json()
    assert s["total"] == s["checked"] + s["skipped"]
    assert s["checked"] == 0 and s["skipped"] == s["total"]
    # 缓存摘要随列表返回：T-va2 有结构错误
    items = client.get("/api/gold/items", params={"q": "T-va"}).json()["items"]
    by_ke = {i["ke_id"]: i for i in items}
    assert by_ke["T-va1"]["validation"]["ok"] is True
    assert by_ke["T-va2"]["validation"]["ok"] is False
    assert by_ke["T-va2"]["validation"]["error_count"] >= 1


def test_export_manifest(client):
    """M22：指纹 manifest 幂等稳定，内容变更 digest 变。"""
    login(client, "admin")
    iid = _create_item(client, GOOD_KE, ke_id="T-m1")
    r = client.get("/api/gold/items/export.manifest.json")
    assert r.status_code == 200
    m = r.json()
    assert m["version"] == 1 and m["total"] >= 1
    fp = [f for f in m["fingerprints"] if f["ke_id"] == "T-m1"]
    assert fp and len(fp[0]["sha256"]) == 64
    digest1 = m["digest"]
    # 同内容再导 → digest 稳定
    assert client.get("/api/gold/items/export.manifest.json").json()["digest"] == digest1
    # 内容变更 → 指纹与 digest 变（首尾空白归一化，故追加实质内容）
    client.patch(f"/api/gold/items/{iid}", json={"ke_text": GOOD_KE.replace("PolicyStartDate", "PolicyStartDate2")})
    m2 = client.get("/api/gold/items/export.manifest.json").json()
    assert m2["digest"] != digest1


def test_upload_triggers_validation(client):
    """上传带 KE内容的 CSV → 草稿入库即校验，ke_invalid 计数随上传返回。"""
    login(client, "annotator")
    csv_text = (
        "题目ID,领域,题目摘要,标注状态,标注人,创建时间,开始时间,完成时间,复核结论,问题标签,篇编号,KE内容\n"
        f"UV-1,育儿,测试题,已完成,标注员,,,,,,篇V,{json.dumps(BAD_KE_NO_OUTPUT)}\n"
    )
    r = client.post("/api/ke/upload", files={"file": ("t.csv", csv_text.encode("utf-8"))},
                    data={"group": "一组"})
    assert r.status_code == 201, r.text
    assert r.json()["items"]["ke_invalid"] == 1
