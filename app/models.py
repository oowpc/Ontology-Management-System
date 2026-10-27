"""全部 19 张表。M1 启用 users/concepts/operators/usage_stats；
submissions/tmp_mappings/backfill_records/audit_logs/health_reports 随后续模块启用；
M7 启用 ke_tasks/ke_uploads（KE 标注进度），users 增加 group_name；
M9 启用 gold_items（成品库）+ api_tokens（agent 接入）；M10 启用 ke_reviews（逐条复核）。

通用性：GOLD 阶段 / 泳道时段的 CHECK 约束跟随 settings（GOLD_STAGES / GOLD_SLOTS），
已有库的约束以建库时为准（改词表请在建库前设定）。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import settings
from .db import Base

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


class Concept(Base):
    __tablename__ = "concepts"
    __table_args__ = (CheckConstraint(f"status IN ({CONCEPT_STATUS})", name="ck_concepts_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)  # 概念名 = KE 正式标识（全局唯一）
    domain: Mapped[str] = mapped_column(String(64), default="通用", index=True)  # 领域分组（育儿/特困/金融/化学…；同名即同一概念，跨域冲突靠体检近义对发现）
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    definition: Mapped[str] = mapped_column(Text, default="")
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
    name: Mapped[str] = mapped_column(String(128))
    subject_concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id"), index=True)  # 主体概念
    owner_concept_id: Mapped[Optional[int]] = mapped_column(ForeignKey("concepts.id"))  # 所属概念，NULL=同主体
    param_types: Mapped[list] = mapped_column(JSON, default=list)
    return_type: Mapped[str] = mapped_column(String(256))  # 概念名或 String/Number/Set[..]
    source_uri: Mapped[Optional[str]] = mapped_column(String(512), index=True)  # 属性 IRI
    ontology_module: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(16), default="approved")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ImportBatch(Base):
    """一次 KEOL 词表导入的批次记录（统计 + 来源 + 创建/更新 ID，供回滚）。"""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
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
    trigger_type: Mapped[str] = mapped_column(String(16))  # manual | scheduled
    summary: Mapped[dict] = mapped_column(JSON)
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ================= M7 · KE 标注进度 =================

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


# ================= M8 · GOLDKE 管理（按《数据标注执行方案》） =================

class GoldArticle(Base):
    """篇级任务：CO 标注与对齐 → 大本体整理 → KE 标注与对齐 → 完成。"""
    __tablename__ = "gold_articles"
    __table_args__ = (
        CheckConstraint(f"stage IN ({_GOLD_STAGES_SQL})", name="ck_gold_articles_stage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
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
    sample_id: Mapped[int] = mapped_column(ForeignKey("gold_samples.id"), index=True)
    gold_item_id: Mapped[int] = mapped_column(ForeignKey("gold_items.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(64))
    verdict: Mapped[str] = mapped_column(String(8))                     # 通过 / 不通过
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ================= M9 · GOLD 成品库 / Agent 接入 =================

class GoldItem(Base):
    """GOLD 成品条目：（篇编号 + KE编号）唯一；定稿后仅审核员/管理员可改，全留痕。"""
    __tablename__ = "gold_items"
    __table_args__ = (
        UniqueConstraint("article_code", "ke_id", name="uq_gold_items_article_ke"),
        CheckConstraint("status IN ('draft','reviewed','gold')", name="ck_gold_items_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    article_code: Mapped[str] = mapped_column(String(32), index=True)   # 篇编号
    ke_id: Mapped[str] = mapped_column(String(64))                      # KE 编号（=题目ID）
    group_name: Mapped[str] = mapped_column(String(64), index=True)
    ke_text: Mapped[str] = mapped_column(Text, default="")              # 标注内容全文
    concepts: Mapped[list] = mapped_column(JSON, default=list)          # 引用概念/TMP 列表
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft→reviewed→gold
    version: Mapped[int] = mapped_column(Integer, default=1)
    validation: Mapped[Optional[dict]] = mapped_column(JSON)  # M20：KE 校验缓存（engine/ok/errors/warnings/validated_at）
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
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


# ================= M10 · KE 逐条复核 =================

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


# ================= M25 · KEOL 实例库镜像 =================

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


# ================= M23 · LLM 调用台账 =================
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


# ================= 题目级视图 =================

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


# ================= Issue 管理（分歧 → 讨论 → 结论 → 回填 闭环） =================

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
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(8), default="human")


class IssueViewpoint(Base):
    """各方观点。"""
    __tablename__ = "issue_viewpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True)
    person: Mapped[str] = mapped_column(String(64))
    stance: Mapped[str] = mapped_column(String(16), default="补充")  # 观点一/观点二/补充
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class IssueComment(Base):
    """讨论流水。"""
    __tablename__ = "issue_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
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
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(8), default="需复核")
    done: Mapped[bool] = mapped_column(default=False)
    done_by: Mapped[Optional[str]] = mapped_column(String(64))
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


# ================= SOP 手工标注工作流 =================

ANNOTATION_STATES = (
    "'draft','dataset_frozen','co_blind','co_alignment','co_merge',"
    "'ke_blind','gold_merge','cross_review','validation','passed','rework'"
)


class AnnotationBatch(Base):
    """CO/KE 手工标注批次：数据集冻结 → CO 双盲 → 对齐 → 合并 → KE → GOLD → 交叉检查 → 发布。"""
    __tablename__ = "annotation_batches"
    __table_args__ = (CheckConstraint(f"state IN ({ANNOTATION_STATES})", name="ck_ab_state"),)

    id: Mapped[int] = mapped_column(primary_key=True)
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
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("annotation_units.id"), index=True)
    side: Mapped[str] = mapped_column(String(1))
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    content: Mapped[str] = mapped_column(Text, default="")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    execution: Mapped[Optional[dict]] = mapped_column(JSON)   # SQL/LEAN/SPARQL 执行/编译结果
    self_check: Mapped[Optional[dict]] = mapped_column(JSON)
    annotator: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class GoldKe(Base):
    __tablename__ = "gold_kes"
    __table_args__ = (UniqueConstraint("unit_id", "version", name="uq_gk_unit_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("annotation_units.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="A")   # A/B/merged/relabel
    content: Mapped[str] = mapped_column(Text, default="")
    differences: Mapped[Optional[list]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    decided_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OntologyVersion(Base):
    """CO_M/KE_M 本体版本：与 import_batches 关联，保存校验与合并日志。"""
    __tablename__ = "ontology_versions"
    __table_args__ = (CheckConstraint("status IN ('draft','frozen','released')", name="ck_ov_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    label: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    parent_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ontology_versions.id"))
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    import_batch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("import_batches.id"))
    snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
    counts: Mapped[Optional[dict]] = mapped_column(JSON)
    validation: Mapped[Optional[dict]] = mapped_column(JSON)
    merge_log: Mapped[Optional[list]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    frozen_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    note: Mapped[str] = mapped_column(Text, default="")


class CoMergeLog(Base):
    __tablename__ = "co_merge_logs"
    __table_args__ = (CheckConstraint(
        "action IN ('merge','split','rename','reparent','other')", name="ck_cml_action"),)

    id: Mapped[int] = mapped_column(primary_key=True)
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
    batch_id: Mapped[int] = mapped_column(ForeignKey("annotation_batches.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(256), default="")
    conclusion: Mapped[str] = mapped_column(String(16))
    findings: Mapped[Optional[list]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class QueryRun(Base):
    """SQL/LEAN/SPARQL 执行/编译留痕：可复现、可复核，不覆盖历史。"""
    __tablename__ = "query_runs"
    __table_args__ = (CheckConstraint("side IN ('A','B') OR side IS NULL", name="ck_qr_side"),)

    id: Mapped[int] = mapped_column(primary_key=True)
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
