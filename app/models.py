"""全部 19 张表。M1 启用 users/concepts/operators/usage_stats；
submissions/tmp_mappings/backfill_records/audit_logs/health_reports 随后续模块启用；
M7 启用 ke_tasks/ke_uploads（KE 标注进度），users 增加 group_name；
M9 启用 gold_items（成品库）+ api_tokens（agent 接入）；M10 启用 ke_reviews（逐条复核）。

通用性：GOLD 阶段 / 泳道时段的 CHECK 约束跟随 settings（GOLD_STAGES / GOLD_SLOTS），
已有库的约束以建库时为准（改词表请在建库前设定）。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, CheckConstraint, DateTime, ForeignKey, Integer,
                        String, Text, UniqueConstraint, func)
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
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    merged_into_id: Mapped[Optional[int]] = mapped_column(ForeignKey("concepts.id"))
    created_by: Mapped[str] = mapped_column(String(64))
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64))
    review_comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    parent = relationship("Concept", remote_side=[id], foreign_keys=[parent_id])


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
    status: Mapped[str] = mapped_column(String(16), default="approved")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (CheckConstraint("status IN ('pending','approved','rejected','merged')", name="ck_submissions_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id"), index=True)  # 同步创建的 pending 概念
    reason: Mapped[str] = mapped_column(Text)  # 为什么现有概念不够
    sample_question: Mapped[str] = mapped_column(Text)  # 附题目
    suggested_parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("concepts.id"))
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
    """验收抽查记录：按范围抽样做语法校验等。"""
    __tablename__ = "gold_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    sample_date: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(128))                 # 如「育儿·一组·篇1」
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    checker: Mapped[str] = mapped_column(String(64))
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
    created_by: Mapped[str] = mapped_column(String(64))
    updated_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ApiToken(Base):
    """API Token（agent 接入）：库中只存 sha256，明文仅签发时返回一次。"""
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="default")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
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
