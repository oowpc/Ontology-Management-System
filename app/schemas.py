"""Pydantic 出入参（仅输入需要校验；输出由路由组装 dict）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ConceptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    domain: str = "通用"
    aliases: list[str] = []
    definition: str = ""
    parent_id: int | None = None
    status: str = "approved"  # draft | approved


class ConceptUpdate(BaseModel):
    name: str | None = None
    domain: str | None = None
    aliases: list[str] | None = None
    definition: str | None = None
    parent_id: int | None = None


class OperatorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    subject_concept_id: int
    owner_concept_id: int | None = None  # 缺省 = 主体概念
    param_types: list[str] = []
    return_type: str = Field(min_length=1, max_length=256)


class OperatorUpdate(BaseModel):
    name: str | None = None
    subject_concept_id: int | None = None
    owner_concept_id: int | None = None
    param_types: list[str] | None = None
    return_type: str | None = None


class SubmissionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    domain: str = "通用"
    aliases: list[str] = []
    definition: str = ""
    suggested_parent_id: int
    reason: str = Field(min_length=1)           # 为什么现有概念不够
    sample_question: str = Field(min_length=1)  # 附题目


class ReviewAction(BaseModel):
    comment: str = Field(min_length=1)          # 审核意见必填
    merge_target_id: int | None = None


class BatchReviewAction(BaseModel):
    ids: list[int] = Field(min_length=1)
    action: str  # approve | reject
    comment: str = Field(min_length=1)


class BackfillRequest(BaseModel):
    text: str = Field(min_length=1)
    source_label: str = ""
