"""领域分组 + 粘贴识别 + 跨领域体检验证。"""
import json

from conftest import concept_id_by_name, login

# 注意：app.* 模块一律在测试函数内延迟导入——顶层导入会在 DB_URL 环境变量
# 设置之前创建引擎，导致测试直接打到开发库（本文件曾因此污染 data/ontology.db）。

SAMPLE_TEXT = """# 演示文本
```keol
concept 粘贴补贴对象
concept 粘贴育儿补贴对象 <: 粘贴补贴对象
concept 粘贴补贴标准
concept 机构
concept 地方政府部门
op 粘贴补贴标准_金额(粘贴补贴标准) : Number
op 补贴_对象(补贴制度) : 补贴对象
const __粘贴补贴对象 : 粘贴补贴对象
粘贴补贴标准_金额(__国家基础标准) = 3600
粘贴补贴标准_金额(__甲, __乙) = 1
机构(__不存在的东西) = "x"
这一行无法识别
```
"""


# ---------------- 领域 ----------------

def test_concept_domain_flow(client):
    login(client, "admin")
    r = client.post("/api/concepts", json={"name": "领域流转概念", "domain": "育儿"})
    assert r.status_code == 201 and r.json()["domain"] == "育儿"
    cid = r.json()["id"]
    # 缺省领域
    r = client.post("/api/concepts", json={"name": "领域缺省概念"})
    assert r.json()["domain"] == "通用"
    # 过滤
    items = client.get("/api/concepts", params={"domain": "育儿"}).json()["items"]
    assert all(i["domain"] == "育儿" for i in items)
    assert any(i["name"] == "领域流转概念" for i in items)
    # 改领域
    r = client.patch(f"/api/concepts/{cid}", json={"domain": "特困"})
    assert r.json()["domain"] == "特困"
    # 提交审核也带领域
    jid = concept_id_by_name(client, "机构")
    r = client.post("/api/submissions", json={
        "name": "领域提交概念", "domain": "金融", "suggested_parent_id": jid,
        "reason": "r", "sample_question": "q"})
    assert r.json()["domain"] == "金融"


def test_export_import_with_domain(client):
    login(client, "admin")
    payload = client.get("/api/export/json").json()
    assert all("domain" in c for c in payload["concepts"])
    r = client.post("/api/import/json", content=json.dumps({"concepts": [
        {"name": "导入带领域概念", "domain": "化学"},
        {"name": "导入无领域概念"},
    ]}, ensure_ascii=False), headers={"Content-Type": "application/json"})
    assert r.json()["concepts_created"] == 2
    d1 = concept_id_by_name(client, "导入带领域概念")
    d2 = concept_id_by_name(client, "导入无领域概念")
    assert client.get(f"/api/concepts/{d1}").json()["domain"] == "化学"
    assert client.get(f"/api/concepts/{d2}").json()["domain"] == "通用"


# ---------------- 粘贴识别：解析 ----------------

def test_parse_keol():
    from app.paster import parse_keol
    p = parse_keol(SAMPLE_TEXT)
    assert len(p["concepts"]) == 5
    assert p["concepts"][1] == {
        "name": "粘贴育儿补贴对象", "parent": "粘贴补贴对象",
        "parents": ["粘贴补贴对象"], "line": 4,
    }
    assert len(p["operators"]) == 2
    assert p["operators"][0]["subject"] == "粘贴补贴标准"
    assert p["operators"][0]["param_types"] == []
    assert p["operators"][0]["return_type"] == "Number"
    assert len(p["consts"]) == 1
    assert len(p["facts"]) == 3
    assert len(p["errors"]) == 1


def test_check_type_expr():
    from app.paster import check_type_expr
    known = {"机构"}
    assert check_type_expr("String", known) is None
    assert check_type_expr("机构", known) is None
    assert check_type_expr("Set[机构]", known) is None
    assert check_type_expr("Set[Set[Number]]", known) is None
    assert check_type_expr("机沟", known) is not None
    assert check_type_expr("Set[机沟]", known) is not None
    assert check_type_expr("", known) is not None


def test_paste_report(client):
    login(client, "annotator")
    r = client.post("/api/paste/parse", json={"text": SAMPLE_TEXT, "domain": "育儿"})
    assert r.status_code == 200
    rep = r.json()
    by_name = {c["name"]: c for c in rep["concepts"]}
    assert by_name["粘贴补贴对象"]["status"] == "new"
    assert by_name["机构"]["status"] == "exists"
    assert by_name["地方政府部门"]["status"] == "similar"  # 与「地方政府」包含命中
    ops = {o["name"]: o for o in rep["operators"]}
    assert ops["粘贴补贴标准_金额"]["status"] == "new"
    assert ops["补贴_对象"]["status"] == "invalid"  # 主体概念「补贴制度」未声明
    facts = rep["facts"]
    assert facts[0]["issues"] == []                       # 签名 1 参 ✓
    assert "个数" in facts[1]["issues"][0]                # 2 参 vs 签名 1
    assert "未声明" in facts[2]["issues"][0]              # 算子「机构」不存在
    assert rep["counts"]["unparsed"] == 1


def test_paste_import_and_idempotent(client):
    login(client, "admin")
    body = {"text": SAMPLE_TEXT, "domain": "育儿", "article_code": "篇9",
            "import_concepts": True, "import_operators": True, "import_facts": True}
    r = client.post("/api/paste/import", json=body)
    assert r.status_code == 200
    s = r.json()
    assert s["concepts_created"] == 4      # 补贴对象/育儿补贴对象/补贴标准/地方政府部门（机构已存在）
    assert s["concepts_skipped"] == 1      # 机构
    assert s["operators_created"] == 1     # 补贴_对象 主体缺失被跳过
    assert s["facts_created"] == 3
    # 领域已写入
    cid = concept_id_by_name(client, "粘贴育儿补贴对象")
    d = client.get(f"/api/concepts/{cid}").json()
    assert d["domain"] == "育儿" and d["parent_name"] == "粘贴补贴对象"
    # 事实入成品库
    items = client.get("/api/gold/items", params={"q": "粘贴补贴标准_金额"}).json()
    assert items["total"] >= 1
    # 再次导入：全部跳过（幂等）
    s2 = client.post("/api/paste/import", json=body).json()
    assert s2["concepts_created"] == 0 and s2["facts_created"] == 0
    assert s2["concepts_skipped"] == 5 and s2["facts_skipped"] == 3


def test_paste_page(client):
    login(client, "reviewer")
    r = client.post("/paste/parse", data={"text": SAMPLE_TEXT, "domain": "育儿"})
    assert r.status_code == 200 and "识别报告" in r.text and "粘贴补贴对象" in r.text


# ---------------- 跨领域体检 ----------------

def test_cross_domain_health(client):
    login(client, "admin")
    client.post("/api/concepts", json={"name": "政府行政机关", "domain": "特困"})
    login(client, "reviewer")
    client.post("/api/health/run")
    report = client.get("/api/health/latest").json()["report"]
    cross = report["details"]["cross_domain"]
    assert any({p["a_name"], p["b_name"]} == {"行政机关", "政府行政机关"} for p in cross)
    pair = next(p for p in cross if {p["a_name"], p["b_name"]} == {"行政机关", "政府行政机关"})
    assert pair["a_domain"] != pair["b_domain"]
    assert "cross_domain" in report["summary"]
    r = client.get("/api/health/report.csv", params={"kind": "cross_domain"})
    assert r.status_code == 200 and "政府行政机关" in r.text
