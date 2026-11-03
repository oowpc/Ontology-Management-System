"""统一 KE 文档与版本：跨 DataMaker / NL2KE / 人工 / GOLD 的文档主键。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class KeDocument(Base):
    __tablename__ = "ke_documents"
    __table_args__ = (
        UniqueConstraint("library_id", "source_type", "source_ref",
                         name="uq_ke_document_source"),
        Index("ix_ke_documents_library_status", "library_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="manual", index=True)
    source_ref: Mapped[str] = mapped_column(String(256), default="")
    question: Mapped[str] = mapped_column(Text, default="")
    ke_text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(16), default="candidate", index=True)
    ontology_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_versions.id"), index=True)
    gold_item_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    annotation_batch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("annotation_batches.id"), index=True)
    validation: Mapped[Optional[dict]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(),
                                                 onupdate=func.now())


class KeDocumentVersion(Base):
    __tablename__ = "ke_document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version",
                                       name="uq_ke_document_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("ke_documents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    ke_text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(String(256), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
