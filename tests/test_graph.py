"""M13 验证：概念继承图——布局引擎单测 + API 结构 + 页面渲染。"""
from app.graphviz import build_forest, layout
from conftest import concept_id_by_name, login


class _C:
    def __init__(self, id, name, parent_id=None, status="approved", aliases=None, definition=""):
        self.id, self.name, self.parent_id = id, name, parent_id
        self.status, self.aliases, self.definition = status, aliases or [], definition


def test_layout_chain_and_fanout():
    nodes = build_forest([
        _C(1, "根"), _C(2, "子一", 1), _C(3, "子二", 1), _C(4, "孙", 2),
    ])
    g = layout(nodes)
    pos = {n["id"]: n for n in g["nodes"]}
    # 深度递增 → x 递增
    assert pos[1]["x"] < pos[2]["x"] < pos[4]["x"]
    # 同 (x,y) 坐标不重叠（不同层同行正常）
    xy = [(n["x"], n["y"]) for n in g["nodes"]]
    assert len(set(xy)) == len(xy)
    # 根在两子中间（tidy tree）
    assert abs(pos[1]["y"] - (pos[2]["y"] + pos[3]["y"]) / 2) < 20
    # 边数 = 节点数 - 根数
    assert len(g["edges"]) == 3
    assert g["width"] > 0 and g["height"] > 0


def test_layout_subtree_and_cycle_safe():
    nodes = build_forest([_C(1, "A"), _C(2, "B", 1), _C(3, "C", 2), _C(9, "孤立")])
    g = layout(nodes, root_id=1)
    assert {n["id"] for n in g["nodes"]} == {1, 2, 3}
    # 手工造环：1↔2 互指（防御不死循环）
    nodes[1]["parent_id"] = 2
    nodes[2]["parent_id"] = 1
    nodes = build_forest([_C(1, "A", 2), _C(2, "B", 1)])
    g2 = layout(nodes)
    assert len(g2["nodes"]) == 2  # 每个节点只布一次


def test_api_graph_structure(client):
    login(client, "annotator")
    d = client.get("/api/graph").json()
    assert d["total"] >= 20
    names = {n["name"] for n in d["nodes"]}
    assert "机构" in names and "行政区划" in names
    # links 与节点一致
    jid = next(n["id"] for n in d["nodes"] if n["name"] == "机构")
    assert any(l["source"] == jid for l in d["links"])
    # 按根过滤：只剩行政区划子树
    rid = concept_id_by_name(client, "行政区划")
    sub = client.get("/api/graph", params={"root_id": rid}).json()
    sub_names = {n["name"] for n in sub["nodes"]}
    assert "县级行政区" in sub_names and "机构" not in sub_names
    # 不存在根
    assert client.get("/api/graph", params={"root_id": 999999}).json()["nodes"] == []


def test_graph_page(client):
    login(client, "annotator")
    r = client.get("/graph")
    assert r.status_code == 200
    assert "<svg" in r.text and "机构" in r.text
    rid = concept_id_by_name(client, "行政区划")
    r2 = client.get("/graph", params={"root_id": rid})
    # 子树含县级行政区；非子树且非根的「国家机构」不出现（根下拉只含根概念）
    assert "县级行政区" in r2.text and "国家机构" not in r2.text
