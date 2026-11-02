"""模型分域模块。所有类仍从 app.models 导出。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..config import settings
from ..db import Base

CONCEPT_STATUS = "'draft','pending','approved','disabled'"
_GOLD_STAGES_SQL = ",".join(f"'{k}'" for k, _ in settings.gold_stages) or "'done'"
_GOLD_SLOTS_SQL = ",".join(f"'{s}'" for s in settings.gold_slots) or "'上午'"

class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('annotator','reviewer','admin')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))  # annotator 标注员 / reviewer 审核员 / admin 管理员
    group_name: Mapped[Optional[str]] = mapped_column(String(64))  # M7：所属标注组（如「一组」），NULL=不属于任何组
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class OntologySource(Base):
    """外部/导入库的物理 schema（如 Archer 的一个数据库）。"""

    __tablename__ = "ontology_sources"
    __table_args__ = (UniqueConstraint("library_id", "key", name="uq_ontology_source_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    key: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    schema_payload: Mapped[dict] = mapped_column(JSON)
    sqlite_path: Mapped[Optional[str]] = mapped_column(String(512))
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class OntologyLibrary(Base):
    """本体库：概念/算子/规则/个体/批次/版本/建议的隔离边界。"""
    __tablename__ = "ontology_libraries"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default="business", index=True)  # reference/business/pilot
    description: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(64), default="system")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class LibraryMember(Base):
    """本体库成员：owner 可管理成员，reviewer 可审核编辑，annotator 只读/标注。"""

    __tablename__ = "library_members"
    __table_args__ = (
        UniqueConstraint("library_id", "user_id", name="uq_library_member"),
        CheckConstraint("role IN ('owner','reviewer','annotator')", name="ck_library_member_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="annotator")
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class Concept(Base):
    __tablename__ = "concepts"
    __table_args__ = (
        CheckConstraint(f"status IN ({CONCEPT_STATUS})", name="ck_concepts_status"),
        Index("ix_concepts_library_name", "library_id", "name", unique=True),
        UniqueConstraint("library_id", "local_code", name="uq_concepts_library_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    local_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))  # 概念名 = 库内唯一
    domain: Mapped[str] = mapped_column(String(64), default="通用", index=True)  # 领域分组（育儿/特困/金融/化学…；同名即同一概念，跨域冲突靠体检近义对发现）
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    definition: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    source_payload: Mapped[Optional[dict]] = mapped_column(JSON)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("concepts.id"), index=True)
    source_uri: Mapped[Optional[str]] = mapped_column(String(512), index=True)  # 外部本体 IRI
    ontology_module: Mapped[Optional[str]] = mapped_column(String(128), index=True)  # 来源模块（CMNS-DatesAndTimes / Equities）
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    merged_into_id: Mapped[Optional[int]] = mapped_column(ForeignKey("concepts.id"))
    created_by: Mapped[str] = mapped_column(String(64))
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64))
    review_comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    parent = relationship("Concept", remote_side=[id], foreign_keys=[parent_id])

class ConceptParent(Base):
    """概念的多父类关联（KE 允许多父类；concepts.parent_id 保留为主父类）。"""

    __tablename__ = "concept_parents"
    __table_args__ = (
        UniqueConstraint("concept_id", "parent_id", name="uq_concept_parent"),
        CheckConstraint("concept_id != parent_id", name="ck_concept_parent_self"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class Operator(Base):
    __tablename__ = "operators"
    __table_args__ = (
        UniqueConstraint("name", "subject_concept_id", name="uq_operator_name_subject"),
        CheckConstraint(f"status IN ({CONCEPT_STATUS})", name="ck_operators_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    subject_concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id"), index=True)  # 主体概念
    owner_concept_id: Mapped[Optional[int]] = mapped_column(ForeignKey("concepts.id"))  # 所属概念，NULL=同主体
    param_types: Mapped[list] = mapped_column(JSON, default=list)
    return_type: Mapped[str] = mapped_column(String(256))  # 概念名或 String/Number/Set[..]
    description: Mapped[str] = mapped_column(Text, default="")
    source_payload: Mapped[Optional[dict]] = mapped_column(JSON)
    output_concept_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("concepts.id"), index=True)
    source_uri: Mapped[Optional[str]] = mapped_column(String(512), index=True)  # 属性 IRI
    ontology_module: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(16), default="approved")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

class OperatorInput(Base):
    """算子输入：按顺序关联概念；无概念时退化为类型名（String/Number/Set[...]）。"""

    __tablename__ = "operator_inputs"
    __table_args__ = (
        UniqueConstraint("operator_id", "position", name="uq_operator_input_position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    operator_id: Mapped[int] = mapped_column(
        ForeignKey("operators.id", ondelete="CASCADE"), index=True)
    concept_id: Mapped[Optional[int]] = mapped_column(ForeignKey("concepts.id"), index=True)
    type_name: Mapped[str] = mapped_column(String(256), default="")
    position: Mapped[int] = mapped_column(Integer, default=0, index=True)

class ImportBatch(Base):
    """一次 KEOL 词表导入的批次记录（统计 + 来源 + 创建/更新 ID，供回滚）。"""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    batch_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(512), default="")
    actor: Mapped[str] = mapped_column(String(128), default="keol-sync")
    status: Mapped[str] = mapped_column(String(16), default="completed", index=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    files: Mapped[list] = mapped_column(JSON, default=list)
    created_concept_ids: Mapped[list] = mapped_column(JSON, default=list)
    updated_concept_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_operator_ids: Mapped[list] = mapped_column(JSON, default=list)
    updated_operator_ids: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (CheckConstraint("status IN ('pending','approved','rejected','merged')", name="ck_submissions_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id"), index=True)  # 同步创建的 pending 概念
    reason: Mapped[str] = mapped_column(Text)  # 为什么现有概念不够
    sample_question: Mapped[str] = mapped_column(Text)  # 附题目
    suggested_parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("concepts.id"))
    suggested_parent_ids: Mapped[list] = mapped_column(JSON, default=list)  # 多父类建议（含主）
    similar_snapshot: Mapped[list] = mapped_column(JSON, default=list)  # 提交瞬间查重快照
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    submitted_by: Mapped[str] = mapped_column(String(64))
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64))
    review_comment: Mapped[Optional[str]] = mapped_column(Text)
    merge_target_id: Mapped[Optional[int]] = mapped_column(ForeignKey("concepts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class TmpMapping(Base):
    __tablename__ = "tmp_mappings"
    __table_args__ = (CheckConstraint("status IN ('open','resolved','merged')", name="ck_tmp_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tmp_id: Mapped[str] = mapped_column(String(32), unique=True)  # TMP-0001 全局递增
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    concept_id: Mapped[Optional[int]] = mapped_column(ForeignKey("concepts.id"))  # 审核通过后回填
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class BackfillRecord(Base):
    __tablename__ = "backfill_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    tmp_id: Mapped[str] = mapped_column(String(32), index=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id"))
    replacement: Mapped[str] = mapped_column(String(128))  # 实际替换文本（概念名）
    source_label: Mapped[str] = mapped_column(String(256), default="")  # 批次说明/文件名
    replaced_count: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)  # concept | operator
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(32))  # create|update|status|merge|rollback
    before: Mapped[Optional[dict]] = mapped_column(JSON)
    after: Mapped[Optional[dict]] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(64))
    token_name: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class UsageStat(Base):
    __tablename__ = "usage_stats"

    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id"), primary_key=True)
    ke_count: Mapped[int] = mapped_column(Integer, default=0)  # KE 文本扫描命中数
    internal_refs: Mapped[int] = mapped_column(Integer, default=0)  # 子类/算子/提交单引用数
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(256), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

class HealthReport(Base):
    __tablename__ = "health_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    trigger_type: Mapped[str] = mapped_column(String(16))  # manual | scheduled
    summary: Mapped[dict] = mapped_column(JSON)
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class KeUpload(Base):
    """上传批次台账：谁、什么时候、传了多少（重复上传以最新一次为准）。"""
    __tablename__ = "ke_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_name: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(256))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    done_count: Mapped[int] = mapped_column(Integer, default=0)  # 状态=已完成 的行数
    warnings: Mapped[list] = mapped_column(JSON, default=list)   # 解析校验提示
    uploaded_by: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class KeTask(Base):
    """KE 标注任务行：每组每天全量上传后按（组别, 题目ID）覆盖。"""
    __tablename__ = "ke_tasks"
    __table_args__ = (
        UniqueConstraint("group_name", "ke_id", name="uq_ke_tasks_group_ke"),
        CheckConstraint("status IN ('待标注','标注中','待复核','已完成','已退回')", name="ck_ke_tasks_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_name: Mapped[str] = mapped_column(String(64), index=True)
    ke_id: Mapped[str] = mapped_column(String(64))            # 题目ID（组内唯一）
    domain: Mapped[str] = mapped_column(String(64), default="未分类")
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="待标注")
    person: Mapped[str] = mapped_column(String(64), default="未分配")
    created_dt: Mapped[Optional[datetime]] = mapped_column(DateTime)   # 创建时间
    started_dt: Mapped[Optional[datetime]] = mapped_column(DateTime)   # 开始时间
    finished_dt: Mapped[Optional[datetime]] = mapped_column(DateTime)  # 完成时间
    review: Mapped[str] = mapped_column(String(16), default="")        # 复核结论：一致/不一致/空
    tags: Mapped[list] = mapped_column(JSON, default=list)             # 问题标签（多值）
    article_code: Mapped[str] = mapped_column(String(32), default="", index=True)  # 篇编号（可选列，篇级自动汇总）
    upload_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ke_uploads.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

