"""变更历史：写操作统一在此留全字段快照；回滚读取快照并再记一条（历史只增不减）。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditLog, Concept, ConceptParent, GoldItem, Operator

ENTITY_MODELS = {"concept": Concept, "operator": Operator, "gold_item": GoldItem}
RESTORABLE = {
    "concept": {"name", "aliases", "definition", "parent_id", "parents",
                "source_uri", "ontology_module", "status", "merged_into_id"},
    "operator": {"name", "subject_concept_id", "owner_concept_id", "param_types",
                 "return_type", "source_uri", "ontology_module", "status"},
    "gold_item": {"article_code", "ke_id", "group_name", "ke_text", "concepts", "status", "version"},
}

ACTION_LABELS = {
    "create": "创建", "update": "修改", "status": "状态变更",
    "merge": "合并", "rollback": "回滚", "import": "导入", "delete": "删除",
    "review": "抽检打标", "arbitrate": "仲裁", "close": "关闭",
}


def snapshot(entity) -> dict:
    """全字段快照（datetime 转 ISO 字符串，可入 JSON 列）。"""
    snap = {}
    for col in entity.__table__.columns:
        v = getattr(entity, col.name)
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        snap[col.name] = v
    return snap


def concept_snapshot(db: Session, c: Concept) -> dict:
    """概念快照：列字段 + 多父类名（按主父类优先、声明序）。"""
    snap = snapshot(c)
    names = db.scalars(
        select(Concept.name)
        .join(ConceptParent, Concept.id == ConceptParent.parent_id)
        .where(ConceptParent.concept_id == c.id)
        .order_by(ConceptParent.is_primary.desc(), ConceptParent.position, Concept.name)
    ).all()
    snap["parents"] = list(names)
    return snap


def log_change(db: Session, entity_type: str, entity_id: int, action: str,
               before: dict | None, after: dict | None, actor: str):
    db.add(AuditLog(entity_type=entity_type, entity_id=entity_id, action=action,
                    before=before, after=after, actor=actor))


def entity_history(db: Session, entity_type: str, entity_id: int, limit: int = 100):
    return db.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.id.desc()).limit(limit)
    ).all()


def audit_dict(a: AuditLog) -> dict:
    return {
        "id": a.id, "entity_type": a.entity_type, "entity_id": a.entity_id,
        "action": a.action, "before": a.before, "after": a.after,
        "actor": a.actor, "created_at": a.created_at,
    }


def diff_entries(before: dict | None, after: dict | None) -> list[dict]:
    """字段级差异（页面展示用），忽略时间戳。"""
    keys = (set(before or {}) | set(after or {})) - {"created_at", "updated_at"}
    return [
        {"field": k, "before": (before or {}).get(k), "after": (after or {}).get(k)}
        for k in sorted(keys)
        if (before or {}).get(k) != (after or {}).get(k)
    ]


def rollback(db: Session, audit_id: int, actor: str):
    """回滚到该记录修改前的状态；回滚本身再记一条 action=rollback。"""
    entry = db.get(AuditLog, audit_id)
    if not entry:
        raise HTTPException(404, "历史记录不存在")
    if entry.before is None:
        raise HTTPException(400, "该记录为创建操作，不可回滚（如需撤销请使用停用）")
    model = ENTITY_MODELS.get(entry.entity_type)
    if not model:
        raise HTTPException(400, "未知实体类型")
    entity = db.get(model, entry.entity_id)
    if not entity:
        raise HTTPException(404, "实体已不存在，无法回滚")
    current = concept_snapshot(db, entity) if entry.entity_type == "concept" else snapshot(entity)
    if entry.entity_type == "concept":
        new_name = entry.before.get("name")
        if new_name and new_name != entity.name:
            dup = db.scalar(select(Concept).where(Concept.name == new_name, Concept.id != entity.id))
            if dup:
                raise HTTPException(409, f"回滚目标名称「{new_name}」已被占用")
        pid = entry.before.get("parent_id")
        if pid is not None:
            from .services import would_create_cycle  # 调用时导入，避免循环依赖
            if would_create_cycle(db, entity.id, pid):
                raise HTTPException(400, "回滚会形成循环继承")
    for field in RESTORABLE[entry.entity_type]:
        if field in entry.before and field != "parents":
            setattr(entity, field, entry.before[field])
    if entry.entity_type == "concept" and "parents" in (entry.before or {}):
        from .services import set_concept_parents  # 调用时导入，避免循环依赖
        ids = []
        for name in entry.before.get("parents") or []:
            p = db.scalar(select(Concept).where(Concept.name == name))
            if p:
                ids.append(p.id)
        set_concept_parents(db, entity, ids)
    db.flush()
    after = concept_snapshot(db, entity) if entry.entity_type == "concept" else snapshot(entity)
    log_change(db, entry.entity_type, entry.entity_id, "rollback", current, after, actor)
    db.commit()
    db.refresh(entity)
    return entity
