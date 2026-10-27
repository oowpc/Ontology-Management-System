"""M25/M26：KEOL 桥接——实例镜像同步、只读 API、校验实体联查、GOLD 回流。"""
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from conftest import login

# KEOL 侧 tools/load_db.py 的最小等价物（同步器以它为权威解析口径；接口与真实文件一致）
FIXTURE_LOADER = '''
import glob, os, re
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "keol.db")

def sources():
    pats = []
    pats += glob.glob(os.path.join(ROOT, "*.md"))
    pats += glob.glob(os.path.join(ROOT, "ke", "**", "*.md"), recursive=True)
    pats += glob.glob(os.path.join(ROOT, "领域", "**", "*.md"), recursive=True)
    return sorted(p for p in pats if "archive" not in p)

def strip_comment(line):
    i = line.find("--")
    return (line[:i] if i >= 0 else line).strip()

def iter_keol_lines(path):
    in_block = False
    with open(path, encoding="utf-8") as f:
        for ln, raw in enumerate(f, 1):
            s = raw.rstrip("\\n")
            if s.strip().startswith("```"):
                if s.strip().startswith("```keol"):
                    in_block = True
                elif in_block:
                    in_block = False
                continue
            if not in_block:
                continue
            line = strip_comment(s)
            if line:
                yield ln, line

def split_top(s):
    parts, depth, buf, in_str = [], 0, "", False
    for ch in s:
        if in_str:
            buf += ch
            if ch == chr(34):
                in_str = False
            continue
        if ch == chr(34):
            in_str = True
            buf += ch
            continue
        if ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(buf.strip()); buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf.strip())
    return parts

def parse_node(s):
    s = s.strip()
    if not s:
        return ("raw", "")
    if s.startswith("__") and not any(c in s for c in "(){}, " + chr(34)):
        return ("ref", s)
    if s.startswith(chr(34)) and s.endswith(chr(34)) and len(s) >= 2:
        return ("string", s[1:-1])
    if re.fullmatch(r"-?\\d+(\\.\\d+)?", s):
        return ("number", float(s) if "." in s else int(s))
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        return ("set", [parse_node(x) for x in split_top(inner)] if inner else [])
    m = re.match(r"^([^(){}]+?)\\((.*)\\)$", s, re.S)
    if m:
        inner = m.group(2).strip()
        args = [parse_node(x) for x in split_top(inner)] if inner else []
        return ("call", m.group(1).strip(), args)
    return ("raw", s)

def canon(node):
    t = node[0]
    if t == "ref":
        return node[1]
    if t == "string":
        return chr(34) + node[1] + chr(34)
    if t == "number":
        return str(node[1])
    if t == "set":
        return "{%s}" % ", ".join(canon(x) for x in node[1])
    if t == "call":
        return "%s(%s)" % (node[1], ", ".join(canon(x) for x in node[2]))
    return node[1]

def kind_of(node):
    return {"ref": "ref", "string": "string", "number": "number",
            "set": "set", "call": "term", "raw": "term"}[node[0]]
'''

CO_MD = """# 测试 CO

```keol
concept 机构
concept 行政区划
concept 地级行政区 <: 行政区划
op 上级行政区划(行政区划) : 行政区划
op 行政区划名称(行政区划) : String
```
"""

KE_MD = """# 区划事实

```keol
const __测试省 : 地级行政区
const __测试市 : 地级行政区
行政区划名称(__测试省) = "测试省"
上级行政区划(__测试市) = __测试省
```
"""

REVIEW_MD = """# 评审候选池（镜像口径：实例同步含 _ 文件，与词表同步不同）

```keol
const __候选实例 : 行政区划
```
"""


@pytest.fixture()
def keol_src(tmp_path):
    src = tmp_path / "keolsrc"
    (src / "tools").mkdir(parents=True)
    (src / "tools" / "load_db.py").write_text(FIXTURE_LOADER, encoding="utf-8")
    (src / "测试本体-CO-v0.1.md").write_text(CO_MD, encoding="utf-8")
    (src / "ke" / "区划").mkdir(parents=True)
    (src / "ke" / "区划" / "测试.md").write_text(KE_MD, encoding="utf-8")
    (src / "领域").mkdir()
    (src / "领域" / "_评审记录.md").write_text(REVIEW_MD, encoding="utf-8")
    return str(src)


def _sync(src):
    from app.db import SessionLocal
    from app.keolsync import sync_instances
    db = SessionLocal()
    try:
        return sync_instances(db, src)
    finally:
        db.close()


def test_sync_instances(keol_src):
    stats = _sync(keol_src)
    assert stats["consts"] == 3          # __测试省 __测试市 + 评审文件的 __候选实例（镜像全量口径）
    assert stats["facts"] == 2
    # 幂等：重建两次结果一致
    stats2 = _sync(keol_src)
    assert stats2["consts"] == 3 and stats2["facts"] == 2

    from app.db import SessionLocal
    from app.keolsync import mirror_status
    db = SessionLocal()
    status = mirror_status(db)
    db.close()
    assert status["entities"] == 3 and status["facts"] == 2
    assert status["synced_at"]


def test_keol_api(client, keol_src):
    _sync(keol_src)
    # 未登录 401
    assert client.get("/api/keol/status", follow_redirects=False).status_code == 401
    login(client, "annotator")
    st = client.get("/api/keol/status").json()
    assert st["entities"] == 3 and st["facts"] == 2

    r = client.get("/api/keol/entities", params={"q": "测试"}).json()
    assert r["count"] == 2
    r = client.get("/api/keol/entities", params={"type": "行政区划"}).json()
    assert r["count"] == 1 and r["items"][0]["name"] == "__候选实例"

    d = client.get("/api/keol/entities/__测试省").json()
    assert d["type"] == "地级行政区"
    assert d["literals"] == [{"pred": "行政区划名称", "value": "测试省"}]
    assert any(f["pred"] == "上级行政区划" and f["subj"] == "__测试市" for f in d["incoming"])
    assert client.get("/api/keol/entities/__不存在").status_code == 404

    f = client.get("/api/keol/facts", params={"pred": "上级行政区划"}).json()
    assert f["count"] == 1 and f["items"][0]["value"] == "__测试省"

    # 页面可渲染
    assert client.get("/keol").status_code == 200
    assert client.get("/keol", params={"name": "__测试市"}).status_code == 200


def test_validate_entity_lookup(client, keol_src):
    """M25：常量右值裸名 / __引用 命中实例镜像即放行；未收录的 __ 引用记 unknown_entity。"""
    _sync(keol_src)
    from app.db import SessionLocal
    from app.ke.ke_validate import validate_ke
    db = SessionLocal()
    try:
        ok_text = (
            "concept 省份\n"
            "op 省份_孩次(省份) : String\n"
            "output={省份_孩次(p)|var p: 省份, p = 测试省}\n"   # __测试省 在镜像 → 不报 unknown_const
        )
        res = validate_ke(db, ok_text)
        kinds = [w["kind"] for w in res["warnings"]]
        assert "unknown_const" not in kinds and "unknown_entity" not in kinds

        ref_text = (
            "op 上级行政区划(行政区划) : 行政区划\n"
            "concept 行政区划\n"
            "output={上级行政区划(__测试市)}\n"                 # __ 引用命中 → 不报 unbound_name
        )
        res = validate_ke(db, ref_text)
        kinds = [w["kind"] for w in res["warnings"]]
        assert "unbound_name" not in kinds and "unknown_entity" not in kinds

        bad_text = (
            "op 上级行政区划(行政区划) : 行政区划\n"
            "concept 行政区划\n"
            "output={上级行政区划(__未收录)}\n"
        )
        res = validate_ke(db, bad_text)
        assert any(w["kind"] == "unknown_entity" and "__未收录" in w["message"]
                   for w in res["warnings"])
    finally:
        db.close()


class _Item:
    def __init__(self, ke_id, text, article="篇1", group="A组"):
        self.article_code, self.ke_id, self.group_name = article, ke_id, group
        self.ke_text = text


def test_gold_export_render():
    from app.gold.keol_export import render_keol_md
    known_c, known_o, known_k = {"制度", "人", "机构"}, {"上级行政区划"}, {"__广东省"}
    items = [
        _Item("KE-1",  # 新概念（父类已收录）+ 新算子（返回内置类型）+ 纯变量问法
              "concept 育儿补贴制度 <: 制度\n"
              "op 制度_开始实施时间(制度) : Date\n"
              "output={制度_开始实施时间(a)|var a: 育儿补贴制度}"),
        _Item("KE-2",  # 概念去重；const → __；变量坐实进问法注释；set() → Set[]
              "concept 育儿补贴制度 <: 制度\n"
              "concept 省份 <: 地区\n"                       # 地区未收录 → 父类剥离注释
              "const 山东 : 省份\n"
              "op 省份_如何认定(省份) : set(String)\n"
              "output={省份_如何认定(p)|var p: 省份, p = 山东}"),
        _Item("KE-3",  # 参数坐实的「调用 = 值」→ 事实
              "concept 省份\n"
              "const 北京 : 省份\n"
              "op 省份_省会(省份) : String\n"
              "output={省份_省会(p)|var p: 省份, p = 北京, 省份_省会(p) = \"测试京\"}"),
        _Item("KE-4",  # 缺 output → 跳过
              "concept 孤儿概念"),
        _Item("KE-5",  # 返回类型未收录 → 待定注释
              "op 补贴_类型(制度) : 补贴类型\n"
              "output={补贴_类型(a)|var a: 制度}"),
    ]
    text, stats = render_keol_md(items, known_c, known_o, known_k, now="2026-10-27T00:00:00")
    assert stats["items"] == 5 and stats["parsed_ok"] == 4 and stats["skipped_error"] == 1
    assert stats["concepts_new"] == 2      # 育儿补贴制度（跨条目去重）/ 省份；KE-4 解析失败不计
    assert stats["ops_new"] == 3           # 制度_开始实施时间 / 省份_如何认定 / 省份_省会；补贴_类型注释化
    assert stats["consts_new"] == 2        # __山东 / __北京
    assert stats["facts"] == 1
    assert "concept 育儿补贴制度 <: 制度" in text
    assert text.count("concept 育儿补贴制度") == 1          # 跨条目去重
    assert "concept 省份  -- 来源 篇1 · KE-2 · A组；原父类「地区」未入库，待评审" in text
    assert "const __山东 : 省份" in text
    assert "op 省份_如何认定(省份) : Set[String]" in text    # set() → Set[]
    assert "-- 待定：op 补贴_类型(制度) : 补贴类型" in text
    assert "-- 问：省份_如何认定(__山东)" in text            # 变量坐实代入
    assert '省份_省会(__北京) = "测试京"' in text            # 事实提取
    assert "__广东省" not in text                           # KEOL 池已收录的 const 不重复声明
