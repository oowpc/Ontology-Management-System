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

class Nl2keRun(Base):
    """OMS → NL2KE BYOS 的一次 KE 生成记录。"""
    __tablename__ = "nl2ke_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    db_id: Mapped[str] = mapped_column(String(128), index=True)
    batch_tag: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    question: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text, default="")
    ke: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="ok", index=True)
    verify: Mapped[bool] = mapped_column(default=True)
    verify_passed: Mapped[bool] = mapped_column(default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str] = mapped_column(String(128), default="")
    usage: Mapped[Optional[dict]] = mapped_column(JSON)
    timings: Mapped[Optional[dict]] = mapped_column(JSON)
    params: Mapped[Optional[dict]] = mapped_column(JSON)
    result_payload: Mapped[Optional[dict]] = mapped_column(JSON)
    sql_payload: Mapped[Optional[dict]] = mapped_column(JSON)
    error: Mapped[str] = mapped_column(Text, default="")
    gold_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("gold_items.id"))
    ke_document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ke_documents.id"), index=True)
    annotation_batch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("annotation_batches.id"), index=True)
    annotation_unit_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("annotation_units.id"), index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class DatamakerJob(Base):
    """datamaker CLI 生成任务；产物与日志落 workdir，不阻塞 OMS 请求。"""
    __tablename__ = "datamaker_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    db_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    model: Mapped[str] = mapped_column(String(128), default="")
    n: Mapped[int] = mapped_column(Integer, default=1)
    workers: Mapped[int] = mapped_column(Integer, default=1)
    ke_types: Mapped[str] = mapped_column(String(512), default="")
    params: Mapped[Optional[dict]] = mapped_column(JSON)
    workdir: Mapped[str] = mapped_column(String(512), default="")
    log_path: Mapped[str] = mapped_column(String(512), default="")
    pid: Mapped[Optional[int]] = mapped_column(Integer)
    worker_id: Mapped[Optional[str]] = mapped_column(String(128))
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    gold_count: Mapped[int] = mapped_column(Integer, default=0)
    gold_gate: Mapped[Optional[dict]] = mapped_column(JSON)
    native_job_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    annotation_batch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("annotation_batches.id"), index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class QueryRun(Base):
    """SQL/LEAN/SPARQL 执行/编译留痕：可复现、可复核，不覆盖历史。"""
    __tablename__ = "query_runs"
    __table_args__ = (CheckConstraint("side IN ('A','B') OR side IS NULL", name="ck_qr_side"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("annotation_units.id"), index=True)
    side: Mapped[Optional[str]] = mapped_column(String(1))
    source_type: Mapped[str] = mapped_column(String(16), default="sql")
    command: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="passed")  # passed/failed/error
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[Optional[dict]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class OntologyRule(Base):
    """全局规则：body/head 存 spec_v1 节点 JSON（body=NULL 即事实）。"""
    __tablename__ = "ontology_rules"
    __table_args__ = (
        CheckConstraint(f"status IN ({CONCEPT_STATUS})", name="ck_or_status"),
        Index("ix_ontology_rules_library_name", "library_id", "name", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))  # 库内唯一
    domain: Mapped[str] = mapped_column(String(64), default="通用", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[Optional[dict]] = mapped_column(JSON)
    head: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    supply: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="approved", index=True)
    source_uri: Mapped[Optional[str]] = mapped_column(String(512), index=True)
    ontology_module: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

class OntologyIndividual(Base):
    """全局个体：名字只在所属概念下唯一（对齐 KEOL identity: concept + name）。"""
    __tablename__ = "ontology_individuals"
    __table_args__ = (
        UniqueConstraint("concept_id", "name", name="uq_oi_concept_name"),
        CheckConstraint(f"status IN ({CONCEPT_STATUS})", name="ck_oi_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id"), index=True)
    domain: Mapped[str] = mapped_column(String(64), default="通用", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list] = mapped_column(JSON, default=list)
    supply: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="approved", index=True)
    source_uri: Mapped[Optional[str]] = mapped_column(String(512), index=True)
    ontology_module: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())



class KeolSyncRun(Base):
    """KEOL 本体源检查记录：源 hash、目标库计数与差异快照。"""
    __tablename__ = "keol_sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    source_path: Mapped[str] = mapped_column(String(512), default="")
    source_hash: Mapped[str] = mapped_column(String(128), default="", index=True)
    status: Mapped[str] = mapped_column(String(16), default="checked")
    counts: Mapped[Optional[dict]] = mapped_column(JSON)
    diff: Mapped[Optional[dict]] = mapped_column(JSON)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    checked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class MonitorEvent(Base):
    """DataAgent Monitor 事件收件箱；全局外部信号，转换 Issue 时归属当前库。"""
    __tablename__ = "monitor_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_event_id: Mapped[str] = mapped_column(String(128), index=True)
    fingerprint: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    product_id: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="medium", index=True)
    category: Mapped[str] = mapped_column(String(32), default="content", index=True)
    field: Mapped[str] = mapped_column(String(64), default="")
    analysis_status: Mapped[str] = mapped_column(String(32), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    before_preview: Mapped[str] = mapped_column(Text, default="")
    after_preview: Mapped[str] = mapped_column(Text, default="")
    official_url: Mapped[str] = mapped_column(String(1024), default="")
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    issue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("issues.id"), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
