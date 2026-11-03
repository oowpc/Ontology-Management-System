"""模型分域模块。所有类仍从 app.models 导出。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..config import settings
from ..db import Base

class Issue(Base):
    """正式 Issue 体系（gold_issues / question_issues 为模块内便签，不并入）。"""
    __tablename__ = "issues"
    __table_args__ = (
        CheckConstraint("status IN ('open','discussing','resolved','escalated','archived')",
                        name="ck_issues_status"),
        CheckConstraint("importance IN ('高','中','低') OR importance IS NULL", name="ck_issues_importance"),
        CheckConstraint("urgency IN ('高','中','低') OR urgency IS NULL", name="ck_issues_urgency"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)      # IS-0001 全局递增
    domain: Mapped[str] = mapped_column(String(64), default="通用", index=True)
    group_name: Mapped[str] = mapped_column(String(64), default="")
    type_tag: Mapped[str] = mapped_column(String(64), index=True)   # 模糊表达建模/指代消解/口径不一致/Why-How建模…
    title: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    importance: Mapped[Optional[str]] = mapped_column(String(8))
    urgency: Mapped[Optional[str]] = mapped_column(String(8))
    description: Mapped[str] = mapped_column(Text, default="")      # 例子→矛盾→提问
    conclusion: Mapped[Optional[str]] = mapped_column(Text)
    scope: Mapped[Optional[str]] = mapped_column(Text)              # 适用范围
    open_items: Mapped[Optional[str]] = mapped_column(Text)         # 尚未解决
    impact_none: Mapped[bool] = mapped_column(default=False)        # 结案时显式声明「无受影响条目」
    created_by: Mapped[str] = mapped_column(String(64))
    owner: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # SOP 标注工作流关联（都可空；不改变原 Issue 用法）
    annotation_batch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("annotation_batches.id"), index=True)
    annotation_unit_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("annotation_units.id"), index=True)
    annotation_stage: Mapped[Optional[str]] = mapped_column(String(32))
    annotation_versions: Mapped[Optional[list]] = mapped_column(JSON)
    annotation_evidence: Mapped[Optional[dict]] = mapped_column(JSON)

class IssueCase(Base):
    """同一问题下的对照案例（通常 1-3 条）。"""
    __tablename__ = "issue_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(256), default="")    # 原文出处
    original_text: Mapped[str] = mapped_column(Text, default="")
    variant_a: Mapped[Optional[str]] = mapped_column(Text)
    variant_b: Mapped[Optional[str]] = mapped_column(Text)
    diff_note: Mapped[str] = mapped_column(Text, default="")

class IssueQuestion(Base):
    """待讨论问题清单。"""
    __tablename__ = "issue_questions"
    __table_args__ = (CheckConstraint("source IN ('human','ai')", name="ck_iq_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(8), default="human")

class IssueViewpoint(Base):
    """各方观点。"""
    __tablename__ = "issue_viewpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True)
    person: Mapped[str] = mapped_column(String(64))
    stance: Mapped[str] = mapped_column(String(16), default="补充")  # 观点一/观点二/补充
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class IssueComment(Base):
    """讨论流水。"""
    __tablename__ = "issue_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True)
    author: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class IssueLink(Base):
    """Issue 与标注对象的关联。"""
    __tablename__ = "issue_links"
    __table_args__ = (
        CheckConstraint("entity_type IN ('concept','operator','question','ke_version','gold_item')",
                        name="ck_il_entity_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    note: Mapped[str] = mapped_column(String(256), default="")

class IssueImpact(Base):
    """结论落地：受影响的标注条目（回填清单）。"""
    __tablename__ = "issue_impacts"
    __table_args__ = (
        CheckConstraint("action IN ('需修改','需复核','无需改')", name="ck_im_action"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(8), default="需复核")
    done: Mapped[bool] = mapped_column(default=False)
    done_by: Mapped[Optional[str]] = mapped_column(String(64))
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

