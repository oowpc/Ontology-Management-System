"""统一校验结果：rule / operator / GOLD KE 的入库、复核、重校留痕。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Index, Integer,
                        String, func)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ValidationResult(Base):
    """一次统一校验的结果快照；历史只追加，不覆盖。"""

    __tablename__ = "validation_results"
    __table_args__ = (
        Index("ix_validation_results_object", "object_type", "object_id"),
        Index("ix_validation_results_library_time", "library_id", "checked_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    object_type: Mapped[str] = mapped_column(String(32), index=True)
    object_id: Mapped[Optional[int]] = mapped_column(Integer)
    object_key: Mapped[str] = mapped_column(String(256), default="")
    stage: Mapped[str] = mapped_column(String(32), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    engine: Mapped[str] = mapped_column(String(64), default="")
    result: Mapped[Optional[dict]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    checked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
