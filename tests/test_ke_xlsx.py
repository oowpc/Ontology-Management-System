"""M19 验证：适配现行协作 xlsx（QA 问答格式 + Lean 题目标注格式），老 CSV 回归不破。

app.* 延迟导入（顶层导入会抢在 DB_URL 设置前建引擎）。
"""
import io

from conftest import login


def _make_xlsx(headers: list[str], rows: list[list], sheet_name: str = "测试表") -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(client, filename: str, raw: bytes, group="试验组X"):
    return client.post("/api/ke/upload",
                       files={"file": (filename, raw, "application/octet-stream")},
                       data={"group": group})


QA_HEADERS = ["自然语言", "自然语言（去废话）", "标注状态", "Gold_KE",
              "KE-Li", "KE-Li-标注状态", "KE-Wang", "KE-Wang-标注状态", "KE-对齐状态", "Issue"]
QA_ROWS = [
    ["[山东问1] 育儿补贴什么时候实施？", "育儿补贴什么时候实施？", "已完成",
     "concept 制度\nop 制度_开始时间(制度): 日期\noutput={制度_开始时间(a)|var a: 育儿补贴制度}",
     "concept 制度\n…Li 版", "", "concept 制度\n…Wang 版", "", "已对齐", ""],
    ["[山东问2] 补贴对象是谁？", "补贴对象是谁？", "标注中",
     "concept 补贴对象\noutput={a|var a:补贴对象}",
     "", "", "concept 补贴对象\n…Wang", "", "未对齐", "口径待讨论"],
]

LEAN_HEADERS = ["题目", "题干", "concpept", "operator", "领域", "标注状态", "conclusion",
                "smallonto concepts", "smallonto operators", "wpcke",
                "王金旺-KE", "王金旺-note", "李天音-KE", "李天音-note",
                "所属数据集", "原始ID"]
LEAN_ROWS = [
    ["conic10K-901", "求双曲线渐近线…", "Curve, Line", "op slope(l: Line) -> Number",
     "Lean", "已完成", "output = { slope(l) | var l: Line };", "", "", "wpcke-版",
     "王版KE", "", "李版KE", "", "Conic10K", "9001"],
    ["conic10K-902", "求椭圆离心率…", "Ellipse", "op eccentricity(c) -> Number",
     "Lean", "待复核", "", "", "", "wpcke-902版",  # conclusion 空 → 用 wpcke
     "王版KE2", "", "", "", "Conic10K", "9002"],
]


def test_qa_xlsx_upload(client):
    login(client, "admin")
    raw = _make_xlsx(QA_HEADERS, QA_ROWS, sheet_name="育儿补贴-问答")
    r = _upload(client, "育儿补贴-问答.xlsx", raw)
    assert r.status_code == 201, r.text
    # 进度任务：2 行入库
    items = client.get("/api/questions").json()  # 题目视图需 sync，先看 ke 侧
    groups = client.get("/api/ke/groups").json()["groups"]
    assert any(g["name"] == "试验组X" for g in groups)
    # 成品库：Gold_KE 入草稿（篇编号=工作表名）
    g = client.get("/api/gold/items", params={"q": "制度_开始时间"}).json()
    assert g["total"] >= 1
    it = g["items"][0]
    assert it["article_code"] == "育儿补贴-问答"
    assert "concept 制度" in it["ke_text"] and "output=" in it["ke_text"]
    # 多人列合成标注人
    from app.db import SessionLocal
    from app.models import KeTask
    from sqlalchemy import select
    db = SessionLocal()
    try:
        t = db.scalar(select(KeTask).where(KeTask.group_name == "试验组X",
                                           KeTask.ke_id == "山东问1"))
        assert t is not None and t.person == "Li、Wang"
        assert t.status == "已完成"
        assert t.review == "已对齐"
        assert "issue" in (db.scalar(select(KeTask).where(
            KeTask.group_name == "试验组X", KeTask.ke_id == "山东问2")).tags or [])
    finally:
        db.close()


def test_lean_xlsx_upload(client):
    login(client, "admin")
    raw = _make_xlsx(LEAN_HEADERS, LEAN_ROWS)
    r = _upload(client, "Lean题目标注.xlsx", raw)
    assert r.status_code == 201, r.text
    from app.db import SessionLocal
    from app.models import KeTask
    from sqlalchemy import select
    db = SessionLocal()
    try:
        t1 = db.scalar(select(KeTask).where(KeTask.ke_id == "conic10K-901"))
        assert t1 is not None and t1.domain == "Lean" and t1.status == "已完成"
        assert "王金旺" in t1.person and "李天音" in t1.person
        assert t1.article_code == "Conic10K"  # 篇编号取「所属数据集」
        assert t1.review.startswith("output =")  # conclusion 讨论结论进复核结论
        # KE 内容：wpcke 优先（conclusion 是讨论文本，不是 KE）
        g = client.get("/api/gold/items", params={"q": "wpcke"}).json()
        assert g["total"] >= 1 and g["items"][0]["article_code"] == "Conic10K"
        # wpcke 为空时回退到标注者 KE 列
        t2 = db.scalar(select(KeTask).where(KeTask.ke_id == "conic10K-902"))
        assert t2.review == ""
    finally:
        db.close()


def test_xlsx_reupload_idempotent(client):
    login(client, "admin")
    raw = _make_xlsx(QA_HEADERS, QA_ROWS)
    _upload(client, "幂等-问答.xlsx", raw, group="试验组Y")
    from app.db import SessionLocal
    from app.models import KeTask
    from sqlalchemy import func, select
    db = SessionLocal()
    try:
        n1 = db.scalar(select(func.count()).select_from(KeTask)
                       .where(KeTask.group_name == "试验组Y"))
    finally:
        db.close()
    _upload(client, "幂等-问答.xlsx", raw, group="试验组Y")
    db = SessionLocal()
    try:
        n2 = db.scalar(select(func.count()).select_from(KeTask)
                       .where(KeTask.group_name == "试验组Y"))
    finally:
        db.close()
    assert n1 == n2 == 2


def test_xlsx_unknown_headers_fallback(client):
    """别家 xlsx：表头不认识时回退 KE_COLUMN_MAP 规范列匹配。"""
    login(client, "admin")
    raw = _make_xlsx(["题目ID", "领域", "题目摘要", "标注状态", "标注人",
                      "创建时间", "开始时间", "完成时间", "复核结论", "问题标签", "KE内容"],
                     [["X-1", "测试域", "别家题一", "已完成", "张三",
                       "2026-10-01", "", "", "一致", "", "output={a|var a:补贴}"]])
    r = _upload(client, "别家-自定义.xlsx", raw, group="试验组Z")
    assert r.status_code == 201, r.text
    from app.db import SessionLocal
    from app.models import KeTask
    from sqlalchemy import select
    db = SessionLocal()
    try:
        t = db.scalar(select(KeTask).where(KeTask.ke_id == "X-1"))
        assert t is not None and t.person == "张三" and t.status == "已完成"
    finally:
        db.close()


def test_csv_still_works(client):
    """回归：老 CSV 管线不变。"""
    login(client, "admin")
    csv_text = "题目ID,领域,题目摘要,标注状态,标注人,创建时间,开始时间,完成时间,复核结论,问题标签\n" \
               "C-回归-1,测试,回归题,已完成,王芳,,,,一致,"
    r = _upload(client, "老格式.csv", csv_text.encode("utf-8-sig"), group="试验组W")
    assert r.status_code == 201, r.text
