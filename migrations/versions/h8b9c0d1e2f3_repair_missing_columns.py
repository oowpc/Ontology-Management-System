"""修复基线 stamp 库缺失列（gold_items.validation / gold_samples.kind,params,status）。

Revision ID: h8b9c0d1e2f3
Revises: g7a8b9c0d1e2
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "h8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "g7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = inspect(op.get_bind())

    def add(table: str, column: sa.Column):
        have = {c["name"] for c in insp.get_columns(table)}
        if column.name not in have:
            op.add_column(table, column)

    add("gold_items", sa.Column("validation", sa.JSON(), nullable=True))
    add("gold_samples", sa.Column("kind", sa.String(length=8), nullable=True))
    add("gold_samples", sa.Column("params", sa.JSON(), nullable=True))
    add("gold_samples", sa.Column("status", sa.String(length=8), nullable=True))


def downgrade() -> None:
    pass
