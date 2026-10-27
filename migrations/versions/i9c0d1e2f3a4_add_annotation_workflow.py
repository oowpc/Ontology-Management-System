"""SOP 手工标注工作流：批次/成员/单元/evidence/CO/对齐/KE/GOLD/本体版本/合并日志/交叉检查。

Revision ID: i9c0d1e2f3a4
Revises: h8b9c0d1e2f3
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app.db import Base
from app import models  # noqa: F401


revision: str = "i9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "h8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TABLES = [
    "annotation_batches", "annotation_members", "annotation_units",
    "annotation_evidence", "co_annotations", "co_alignments",
    "ke_annotations", "gold_kes", "ontology_versions",
    "co_merge_logs", "annotation_cross_reviews",
]


def upgrade() -> None:
    bind = op.get_bind()
    for name in NEW_TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)

    insp = inspect(bind)
    have = {c["name"] for c in insp.get_columns("issues")}
    extra = [
        sa.Column("annotation_batch_id", sa.Integer(), nullable=True),
        sa.Column("annotation_unit_id", sa.Integer(), nullable=True),
        sa.Column("annotation_stage", sa.String(length=32), nullable=True),
        sa.Column("annotation_versions", sa.JSON(), nullable=True),
        sa.Column("annotation_evidence", sa.JSON(), nullable=True),
    ]
    for col in extra:
        if col.name not in have:
            op.add_column("issues", col)
    for idx, col in [("ix_issues_annotation_batch_id", "annotation_batch_id"),
                     ("ix_issues_annotation_unit_id", "annotation_unit_id")]:
        existing = {i["name"] for i in insp.get_indexes("issues")}
        if idx not in existing:
            op.create_index(idx, "issues", [col])


def downgrade() -> None:
    pass
