"""模型分域模块。所有类仍从 app.models 导出。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..config import settings
from ..db import Base

class Question(Base):
    """题目级工作台：ext_id 幂等回填自 ke_tasks（新增维度，不改 ke_* 表）。"""
    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint("status IN ('not_started','drafting','pending_review','approved','rejected')",
                        name="ck_questions_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ext_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)  # 原始题目编号（跨组重号消歧为 组/题号）
    domain: Mapped[str] = mapped_column(String(64), default="未分类", index=True)
    group_name: Mapped[str] = mapped_column(String(64), default="", index=True)
    text: Mapped[str] = mapped_column(Text, default="")          # 原文（回填自 ke_tasks.summary）
    difficulty: Mapped[Optional[str]] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="not_started", index=True)
    assignee: Mapped[str] = mapped_column(String(64), default="")
    reviewer: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

class QuestionConcept(Base):
    """题目 ↔ 概念/算子 关联（二选一必填）。"""
    __tablename__ = "question_concepts"
    __table_args__ = (
        CheckConstraint("(concept_id IS NULL) <> (operator_id IS NULL)", name="ck_qc_exactly_one"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), index=True)
    concept_id: Mapped[Optional[int]] = mapped_column(ForeignKey("concepts.id"), index=True)
    operator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("operators.id"), index=True)
    note: Mapped[str] = mapped_column(Text, default="")

class QuestionKeVersion(Base):
    """KE 版本：题内自增 + 单当前版（写新版同事务清旧 is_current）。"""
    __tablename__ = "question_ke_versions"
    __table_args__ = (UniqueConstraint("question_id", "version", name="uq_qkv_q_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer)
    author: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class QuestionIssue(Base):
    """题目级 issue（open / resolved / escalated）。"""
    __tablename__ = "question_issues"
    __table_args__ = (
        CheckConstraint("status IN ('open','resolved','escalated')", name="ck_qi_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

