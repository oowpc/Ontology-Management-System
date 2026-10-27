"""修复基线 stamp 库缺失的模型表（gold_sample_* / keol_* / llm_calls）。

历史库在 Alembic 引入时被直接 stamp 到基线；如果这些表是在 stamp 之后才加入基线，
它们不会被创建。本迁移按模型元数据只补缺失表，已有表不动。

Revision ID: g7a8b9c0d1e2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

from app.db import Base
from app import models  # noqa: F401  注册全部模型


revision: str = "g7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    have = set(inspect(bind).get_table_names())
    # 只修基线时代已存在的表；不要用整个 Base.metadata，否则后续新表会被提前创建
    repair = {"gold_sample_items", "gold_sample_reviews", "keol_entities",
              "keol_facts", "llm_calls"}
    for table in Base.metadata.sorted_tables:
        if table.name in repair and table.name not in have:
            table.create(bind, checkfirst=True)


def downgrade() -> None:
    pass
