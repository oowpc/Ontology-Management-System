"""种子：初始账号 + 演示数据（取自《中国政府本体 CO v0.8》，方便演示查重）。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from .config import settings
from .db import SessionLocal
from .models import Concept, Operator, UsageStat, User
from .security import hash_password

# (name, parent_name, aliases, definition)
DEMO_CONCEPTS = [
    ("机构", None, [], "各种国家机关、党的机构、基层群众性自治组织等的总称"),
    ("行政区划", None, ["行政区"], "国家为实行分级管理而划定的区域"),
    ("人", None, ["人员"], "自然人，任职者"),
    ("职务", None, [], "机构中的职位"),
    ("民族", None, [], "中国 56 个民族"),
    ("职责", None, ["职能"], "机构承担的职责"),
    ("国家机构", "机构", [], "宪法第三章规定的国家机构"),
    ("党的机构", "机构", ["党组织"], "中国共产党各级机构"),
    ("基层群众性自治组织", "机构", ["基层自治组织"], "村民委员会、居民委员会（宪法第111条）"),
    ("行政机关", "国家机构", ["政府"], "国家行政机关"),
    ("权力机关", "国家机构", ["人大"], "各级人民代表大会及其常务委员会"),
    ("监察机关", "国家机构", ["监委"], "各级监察委员会"),
    ("检察机关", "国家机构", ["检察院"], "各级人民检察院"),
    ("审判机关", "国家机构", ["法院"], "各级人民法院"),
    ("地方政府", "行政机关", ["人民政府"], "省、地、县、乡各级地方人民政府"),
    ("省级行政区", "行政区划", ["省级"], "省、自治区、直辖市、特别行政区"),
    ("地级行政区", "行政区划", ["地级"], "地级市、地区、自治州、盟"),
    ("县级行政区", "行政区划", ["县级"], "县、县级市、市辖区、旗、自治旗等"),
    ("乡级行政区", "行政区划", ["乡级"], "乡、镇、街道、苏木等"),
    ("领导职务", "职务", [], "机构正职、副职等领导岗位"),
]

# (name, subject, param_types, return_type)
DEMO_OPERATORS = [
    ("机构全称", "机构", [], "String"),
    ("机构简称", "机构", [], "String"),
    ("上级机构", "机构", [], "Set[机构]"),
    ("政府", "行政区划", [], "地方政府"),
    ("行政区划代码", "行政区划", [], "String"),
]

DEMO_USAGE = [("机构", 128), ("行政区划", 96), ("地方政府", 41)]

USERS = [("admin", "admin", None), ("reviewer", "reviewer", None),
         ("annotator", "annotator", "一组"), ("annotator2", "annotator", "二组"),
         ("annotator3", "annotator", "三组")]


def _seed_users(db):
    """幂等：补建缺失账号；为种子账号回填组（兼容 M7 之前创建的已有库）。"""
    existing = {u.username: u for u in db.scalars(select(User)).all()}
    for username, role, group in USERS:
        if username not in existing:
            db.add(User(username=username, password_hash=hash_password(username),
                        role=role, group_name=group))
        elif group and not existing[username].group_name:
            existing[username].group_name = group
    db.commit()


def _seed_ke(db):
    """M7 演示数据：向「一组」灌入示例标注 CSV（ke_tasks 非空则跳过）。"""
    if not settings.seed_ke:
        return
    from .ke.service import parse_csv_text, replace_group_tasks
    from .models import KeTask
    if (db.scalar(select(func.count()).select_from(KeTask)) or 0) > 0:
        return
    sample = (Path(__file__).resolve().parent / "ke" / "sample_ke.csv").read_text(encoding="utf-8-sig")
    rows, warnings = parse_csv_text(sample)
    replace_group_tasks(db, "一组", rows, "sample_ke.csv", "seed", warnings)
    from .gold.items import ingest_rows
    ingest_rows(db, "一组", rows, "seed")   # M9：带「KE内容」的演示行同步入成品库草稿


def seed_if_empty():
    db = SessionLocal()
    try:
        _seed_users(db)

        if settings.seed_demo and (db.scalar(select(func.count()).select_from(Concept)) or 0) == 0:
            ids: dict[str, int] = {}
            for name, parent, aliases, definition in DEMO_CONCEPTS:
                c = Concept(
                    name=name, aliases=aliases, definition=definition,
                    parent_id=ids.get(parent), status="approved",
                    created_by="admin", reviewed_by="admin",
                )
                db.add(c)
                db.flush()
                ids[name] = c.id
            for name, subject, params, return_type in DEMO_OPERATORS:
                db.add(Operator(
                    name=name, subject_concept_id=ids[subject], owner_concept_id=None,
                    param_types=params, return_type=return_type,
                    status="approved", created_by="admin",
                ))
            for cname, cnt in DEMO_USAGE:
                db.add(UsageStat(
                    concept_id=ids[cname], ke_count=cnt,
                    last_used_at=datetime.now(), source="seed-demo",
                ))
            db.commit()

        _seed_ke(db)

        if settings.seed_demo:
            from .gold.seed import seed_gold
            seed_gold(db)
    finally:
        db.close()
