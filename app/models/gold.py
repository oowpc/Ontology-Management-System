"""模型分域模块。所有类仍从 app.models 导出。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..config import settings
from ..db import Base

_GOLD_STAGES_SQL = ",".join(f"'{k}'" for k, _ in settings.gold_stages) or "'done'"
_GOLD_SLOTS_SQL = ",".join(f"'{s}'" for s in settings.gold_slots) or "'上午'"
class GoldArticle(Base):
    """篇级任务：CO 标注与对齐 → 大本体整理 → KE 标注与对齐 → 完成。"""
    __tablename__ = "gold_articles"
    __table_args__ = (
        CheckConstraint(f"stage IN ({_GOLD_STAGES_SQL})", name="ck_gold_articles_stage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)      # 篇1 … 篇13
    title: Mapped[str] = mapped_column(String(256))
    domain: Mapped[str] = mapped_column(String(64), index=True)     # 育儿/特困/金融/化学…
    group_name: Mapped[str] = mapped_column(String(16), index=True) # A组/B组/C组
    stage: Mapped[str] = mapped_column(String(32), default="co_annotation", index=True)
    ke_target: Mapped[int] = mapped_column(Integer, default=30)     # 每篇约 30 条
    ke_done: Mapped[int] = mapped_column(Integer, default=0)        # 已完成 KE 条数（组内自报）
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

class GoldScheduleCell(Base):
    """泳道排期单元格：day × 上/下午 × 组别。"""
    __tablename__ = "gold_schedule_cells"
    __table_args__ = (
        UniqueConstraint("day_index", "slot", "group_name", name="uq_gold_cell"),
        CheckConstraint(f"slot IN ({_GOLD_SLOTS_SQL})", name="ck_gold_cell_slot"),
        CheckConstraint("status IN ('planned','done','missed')", name="ck_gold_cell_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    day_index: Mapped[int] = mapped_column(Integer)
    slot: Mapped[str] = mapped_column(String(8))                    # 上午/下午
    group_name: Mapped[str] = mapped_column(String(16))             # A组/B组/C组
    task: Mapped[str] = mapped_column(String(256), default="")      # 如「CO 标注与对齐（篇1-育儿）」
    article_code: Mapped[str] = mapped_column(String(32), default="")  # 关联篇（可空，大本体整理无单篇）
    status: Mapped[str] = mapped_column(String(16), default="planned")
    note: Mapped[str] = mapped_column(String(256), default="")

class GoldIssue(Base):
    """标注分歧 issue（话题+评论；方案要求文字记录、聚类、必要时上会）。"""
    __tablename__ = "gold_issues"
    __table_args__ = (
        CheckConstraint("status IN ('open','resolved','escalated')", name="ck_gold_issues_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text, default="")          # 思考过程（方案要求先独立思考）
    cluster: Mapped[str] = mapped_column(String(64), default="", index=True)  # 聚类标签（同类问题类推）
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    related_type: Mapped[str] = mapped_column(String(16), default="")   # article/question/domain
    related_ref: Mapped[str] = mapped_column(String(128), default="")   # 篇1 / KE-001 / 育儿
    raised_by: Mapped[str] = mapped_column(String(64))
    resolution: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class GoldIssueComment(Base):
    __tablename__ = "gold_issue_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("gold_issues.id"), index=True)
    author: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class GoldSample(Base):
    """验收抽查记录：按范围抽样做语法校验等。

    M21 起分两态：kind=manual 手工登记（只记 total/passed 数字）；
    kind=auto 系统抽样（分层等距抽样落样本清单 → 双人盲评 → 分歧仲裁 → 关闭时回填
    total/passed），params 存抽样参数（含 seed，可复现），status=open/closed。"""
    __tablename__ = "gold_samples"
    __table_args__ = (
        CheckConstraint("kind IN ('manual','auto') OR kind IS NULL", name="ck_gold_samples_kind"),
        CheckConstraint("status IN ('open','closed') OR status IS NULL", name="ck_gold_samples_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    sample_date: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(128))                 # 如「育儿·一组·篇1」
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    checker: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[Optional[str]] = mapped_column(String(8))          # NULL=manual（旧数据）
    params: Mapped[Optional[dict]] = mapped_column(JSON)            # auto：filters/size/rate/seed
    status: Mapped[Optional[str]] = mapped_column(String(8))        # auto: open/closed；manual: NULL
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class GoldSampleItem(Base):
    """M21：系统抽样的样本清单（分层等距抽样结果，落库可查可复核）。"""
    __tablename__ = "gold_sample_items"
    __table_args__ = (
        UniqueConstraint("sample_id", "gold_item_id", name="uq_sample_item"),
        CheckConstraint("final_verdict IN ('通过','不通过') OR final_verdict IS NULL",
                        name="ck_gsi_final"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("gold_samples.id"), index=True)
    gold_item_id: Mapped[int] = mapped_column(ForeignKey("gold_items.id"), index=True)
    article_code: Mapped[str] = mapped_column(String(32), default="")   # 冗余展示
    ke_id: Mapped[str] = mapped_column(String(64), default="")
    final_verdict: Mapped[Optional[str]] = mapped_column(String(8))     # 双人一致或仲裁落定
    arbiter: Mapped[Optional[str]] = mapped_column(String(64))
    arbitrated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class GoldSampleReview(Base):
    """M21：双人盲评留痕——(样本, 条目, 复核人) 唯一，重复提交 409。"""
    __tablename__ = "gold_sample_reviews"
    __table_args__ = (
        UniqueConstraint("sample_id", "gold_item_id", "reviewer", name="uq_sample_review"),
        CheckConstraint("verdict IN ('通过','不通过')", name="ck_gsr_verdict"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("gold_samples.id"), index=True)
    gold_item_id: Mapped[int] = mapped_column(ForeignKey("gold_items.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(64))
    verdict: Mapped[str] = mapped_column(String(8))                     # 通过 / 不通过
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class GoldItem(Base):
    """GOLD 成品条目：（篇编号 + KE编号）唯一；定稿后仅审核员/管理员可改，全留痕。"""
    __tablename__ = "gold_items"
    __table_args__ = (
        UniqueConstraint("article_code", "ke_id", name="uq_gold_items_article_ke"),
        CheckConstraint("status IN ('draft','reviewed','gold')", name="ck_gold_items_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ontology_libraries.id"), index=True)
    article_code: Mapped[str] = mapped_column(String(32), index=True)   # 篇编号
    ke_id: Mapped[str] = mapped_column(String(64))                      # KE 编号（=题目ID）
    group_name: Mapped[str] = mapped_column(String(64), index=True)
    ke_text: Mapped[str] = mapped_column(Text, default="")              # 标注内容全文
    concepts: Mapped[list] = mapped_column(JSON, default=list)          # 引用概念/TMP 列表
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft→reviewed→gold
    version: Mapped[int] = mapped_column(Integer, default=1)
    validation: Mapped[Optional[dict]] = mapped_column(JSON)  # M20：KE 校验缓存（engine/ok/errors/warnings/validated_at）
    ke_document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ke_documents.id"), index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

class ApiToken(Base):
    """API Token（agent 接入）：库中只存 sha256，明文仅签发时返回一次。
    M28：scopes 最小权限（["read"] 只读 / ["read","write"] 读写；NULL=全权限，兼容旧 token）。"""
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="default")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[Optional[list]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_used_ip: Mapped[Optional[str]] = mapped_column(String(64))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class KeReview(Base):
    """逐条复核留痕。天然键（组, 题目ID）；最新一条为有效结论，上传全量重灌时自动重放
    （系统是复核结论的权威来源，组的表自报不覆盖系统复核）。"""
    __tablename__ = "ke_reviews"
    __table_args__ = (CheckConstraint("conclusion IN ('一致','不一致')", name="ck_ke_reviews_conclusion"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_name: Mapped[str] = mapped_column(String(64), index=True)
    ke_id: Mapped[str] = mapped_column(String(64), index=True)
    reviewer: Mapped[str] = mapped_column(String(64))
    conclusion: Mapped[str] = mapped_column(String(16))   # 一致 / 不一致
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class KeolEntity(Base):
    """KEOL 实例（const __名 : 类型）。镜像只读数据：权威源在 KEOL .md，
    由 scripts/sync_keol.py --with-instances 全量重建（不留审计逐条痕，synced_at 记批次）。"""
    __tablename__ = "keol_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256), unique=True, index=True)  # __广东省
    type: Mapped[str] = mapped_column(String(128), index=True)               # 概念名 / 内置类型
    src: Mapped[str] = mapped_column(String(256), default="")                # 来源 文件:行
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class KeolFact(Base):
    """KEOL 事实：谓词(参数) = 值。key=规范化参数表；subj=首参（便于反查）；kind=ref|string|number|set|term。"""
    __tablename__ = "keol_facts"
    __table_args__ = (Index("ix_keol_facts_pred_key", "pred", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pred: Mapped[str] = mapped_column(String(128), index=True)
    key: Mapped[str] = mapped_column(String(512), default="", index=True)
    subj: Mapped[str] = mapped_column(String(256), default="", index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16), default="term")
    src: Mapped[str] = mapped_column(String(256), default="")
    line: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class LlmCall(Base):
    """LLM 调用台账：场景 / 模型 / token 用量 / 耗时 / 重试次数 / 成败（成本与稳定性可见）。"""
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    scene: Mapped[str] = mapped_column(String(32), index=True)   # semantic_dedup / suggest_review / …
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    ok: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

