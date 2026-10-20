"""通用性验证：别家工况不改代码——CSV 表头映射 + 状态词表映射 + GOLD 词表配置解析。"""

EN_HEADERS = ("qid,field,abstract,state,assignee,created_at,started_at,finished_at,"
              "review_note,issue_tags")


def test_column_map_and_status_map():
    """英文表头 + 英文状态写法 → 规范行（不改代码，只给映射）。"""
    from app.config import settings
    from app.ke import service

    cmap = {k: EN_HEADERS.split(",")[i] for i, k in enumerate(
        ["id", "domain", "summary", "status", "person", "created", "started",
         "finished", "review", "tags"])}
    csv_text = EN_HEADERS + "\nX1,finance,q one,done,alice,2026-10-01,,,,\n"

    old_map = settings.ke_status_map
    settings.ke_status_map = {**old_map, "done": ["已完成", "done"]}
    try:
        rows, warnings = service.parse_csv_text(csv_text, column_map=cmap)
    finally:
        settings.ke_status_map = old_map

    assert rows[0]["id"] == "X1"
    assert rows[0]["status"] == "已完成"       # done → 规范值
    assert rows[0]["domain"] == "finance"


def test_missing_column_reports_mapped_names():
    """缺列时报错信息列出映射后的期望表头（别家能看懂自己该提供什么）。"""
    from app.ke import service
    try:
        service.parse_csv_text("qid,field\nX1,finance\n",
                               column_map={"id": "qid"})
        assert False, "应当抛 ValueError"
    except ValueError as e:
        assert "领域" in str(e) and "KE_COLUMN_MAP" in str(e)


def test_gold_vocab_config_defaults():
    """GOLD 阶段/时段词表走配置（默认口径不变，JSON 非法时回退默认）。"""
    from app.config import settings
    assert [k for k, _ in settings.gold_stages] == [
        "co_annotation", "co_merge", "ke_annotation", "done"]
    assert settings.gold_slots == ["上午", "下午"]
    assert settings.ke_column_map["article"] == "篇编号"
