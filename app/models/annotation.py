"""模型分域模块。所有类仍从 app.models 导出。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..config import settings
from ..db import Base

ANNOTATION_STATES = (
    "'draft','dataset_frozen','co_blind','co_alignment','co_merge',"
    "'ke_blind','gold_merge','cross_review','validation','passed','rework'"
)

class AnnotationBatch(Base):
    """CO/KE 手工标注批次：数据集冻结 → CO 双盲 → 对齐 → 合并 → KE → GOLD → 交叉检查 → 发布。"""
    __tablename__ = "annotation_batches"
    __table_args__ = (CheckConstraint(f"state IN ({ANNOTATION_STATES})", name="ck_ab_state"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    batch_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256))
    source_name: Mapped[str] = mapped_column(String(256), default="")
    source_version: Mapped[str] = mapped_column(String(128), default="")
    source_checksum: Mapped[str] = mapped_column(String(128), default="")
    source_type: Mapped[str] = mapped_column(String(16), default="policy")  # policy/query/sql/lean/sparql/other
    domain: Mapped[str] = mapped_column(String(64), default="通用", index=True)
    guide_version: Mapped[str] = mapped_column(String(64), default="")
    statistical_unit: Mapped[str] = mapped_column(String(64), default="标注单元")
    acceptance_threshold: Mapped[str] = mapped_column(String(256), default="")
    state: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    frozen_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class AnnotationMember(Base):
    """批次成员：标注 A/B、组长、裁决人、批次负责人、交叉检查、项目负责人。"""
    __tablename__ = "annotation_members"
    __table_args__ = (
        UniqueConstraint("batch_id", "username", "role", name="uq_am_batch_user_role"),
        CheckConstraint("role IN ('annotator','lead','arbiter','batch_owner','cross_checker','project_owner')",
                        name="ck_am_role"),
        CheckConstraint("side IN ('A','B') OR side IS NULL", name="ck_am_side"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))
    side: Mapped[Optional[str]] = mapped_column(String(1))
    group_name: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class AnnotationUnit(Base):
    """稳定标注单元：来源单元切出的可独立判定问题/事实。"""
    __tablename__ = "annotation_units"
    __table_args__ = (UniqueConstraint("batch_id", "unit_id", name="uq_au_batch_unit"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    unit_id: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(256), default="")
    source_type: Mapped[str] = mapped_column(String(16), default="policy")
    source_locator: Mapped[Optional[dict]] = mapped_column(JSON)   # page/paragraph/char_offset/url
    raw_text: Mapped[str] = mapped_column(Text, default="")
    translation_text: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(16), default="zh")
    source_payload: Mapped[Optional[dict]] = mapped_column(JSON)   # SQL/LEAN/SPARQL schema/环境快照
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    question_id: Mapped[Optional[int]] = mapped_column(ForeignKey("questions.id"))
    gold_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("gold_items.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class AnnotationEvidence(Base):
    __tablename__ = "annotation_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("annotation_units.id"), index=True)
    source_id: Mapped[str] = mapped_column(String(256), default="")
    locator: Mapped[Optional[dict]] = mapped_column(JSON)
    quote: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(16), default="zh")
    kind: Mapped[str] = mapped_column(String(16), default="text")  # text/schema/graph/execution
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class CoAnnotation(Base):
    """单题 CO 盲评：A/B 各自独立版本，只增不改。"""
    __tablename__ = "co_annotations"
    __table_args__ = (
        UniqueConstraint("unit_id", "side", "version", name="uq_co_unit_side_version"),
        CheckConstraint("side IN ('A','B')", name="ck_co_side"),
        CheckConstraint("decision IN ('include','exclude','abstain')", name="ck_co_decision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("annotation_units.id"), index=True)
    side: Mapped[str] = mapped_column(String(1))
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    decision: Mapped[str] = mapped_column(String(16), default="include")
    concepts: Mapped[list] = mapped_column(JSON, default=list)
    constants: Mapped[list] = mapped_column(JSON, default=list)
    operators: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    annotator: Mapped[str] = mapped_column(String(64))
    guide_version: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class CoAlignment(Base):
    """逐题 CO 对齐结论：单题唯一当前版本。"""
    __tablename__ = "co_alignments"
    __table_args__ = (
        UniqueConstraint("unit_id", "version", name="uq_ca_unit_version"),
        CheckConstraint("conclusion IN ('aligned','merged','split','relabel','pending')",
                        name="ck_ca_conclusion"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("annotation_units.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    co_a_id: Mapped[Optional[int]] = mapped_column(ForeignKey("co_annotations.id"))
    co_b_id: Mapped[Optional[int]] = mapped_column(ForeignKey("co_annotations.id"))
    conclusion: Mapped[str] = mapped_column(String(16), default="aligned")
    aligned: Mapped[Optional[dict]] = mapped_column(JSON)
    differences: Mapped[Optional[list]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    decided_by: Mapped[str] = mapped_column(String(64))
    guide_version: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class KeAnnotation(Base):
    __tablename__ = "ke_annotations"
    __table_args__ = (
        UniqueConstraint("unit_id", "side", "version", name="uq_kea_unit_side_version"),
        CheckConstraint("side IN ('A','B')", name="ck_kea_side"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("annotation_units.id"), index=True)
    side: Mapped[str] = mapped_column(String(1))
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    content: Mapped[str] = mapped_column(Text, default="")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    execution: Mapped[Optional[dict]] = mapped_column(JSON)   # SQL/LEAN/SPARQL 执行/编译结果
    self_check: Mapped[Optional[dict]] = mapped_column(JSON)
    ke_document_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    annotator: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class GoldKe(Base):
    __tablename__ = "gold_kes"
    __table_args__ = (UniqueConstraint("unit_id", "version", name="uq_gk_unit_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("annotation_units.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="A")   # A/B/merged/relabel
    content: Mapped[str] = mapped_column(Text, default="")
    differences: Mapped[Optional[list]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    ke_document_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    decided_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class OntologyVersion(Base):
    """CO_M/KE_M 本体版本：与 import_batches 关联，保存校验与合并日志。"""
    __tablename__ = "ontology_versions"
    __table_args__ = (
        CheckConstraint("status IN ('draft','frozen','released')", name="ck_ov_status"),
        Index("ix_ontology_versions_library_label", "library_id", "label", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    label: Mapped[str] = mapped_column(String(64))  # 库内唯一
    parent_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ontology_versions.id"))
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    import_batch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("import_batches.id"))
    snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    counts: Mapped[Optional[dict]] = mapped_column(JSON)
    validation: Mapped[Optional[dict]] = mapped_column(JSON)
    merge_log: Mapped[Optional[list]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    frozen_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    note: Mapped[str] = mapped_column(Text, default="")

class OntologySuggestion(Base):
    """KEOL/agent 提交的本体建议包；审批后才写入全局本体。"""
    __tablename__ = "ontology_suggestions"
    __table_args__ = (CheckConstraint(
        "status IN ('pending','approved','rejected','failed')", name="ck_os_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    source: Mapped[str] = mapped_column(String(32), default="keol", index=True)
    origin: Mapped[str] = mapped_column(String(32), default="keol", index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    package: Mapped[dict] = mapped_column(JSON)
    refs: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[Optional[dict]] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64))
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

class CoMergeLog(Base):
    __tablename__ = "co_merge_logs"
    __table_args__ = (CheckConstraint(
        "action IN ('merge','split','rename','reparent','other')", name="ck_cml_action"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ontology_versions.id"))
    action: Mapped[str] = mapped_column(String(16))
    source_units: Mapped[list] = mapped_column(JSON, default=list)
    target: Mapped[str] = mapped_column(String(256), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class AnnotationCrossReview(Base):
    __tablename__ = "annotation_cross_reviews"
    __table_args__ = (CheckConstraint(
        "conclusion IN ('pass','changes','block')", name="ck_acr_conclusion"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(256), default="")
    conclusion: Mapped[str] = mapped_column(String(16))
    findings: Mapped[Optional[list]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

