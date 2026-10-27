"""concepts/operators 增加 source_uri、ontology_module（导入来源）

Revision ID: d4e5f6a7b8c9
Revises: c3f2a9b71d40
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3f2a9b71d40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("concepts", "operators"):
        op.add_column(table, sa.Column("source_uri", sa.String(length=512), nullable=True))
        op.add_column(table, sa.Column("ontology_module", sa.String(length=128), nullable=True))
        op.create_index(f"ix_{table}_source_uri", table, ["source_uri"])
        op.create_index(f"ix_{table}_ontology_module", table, ["ontology_module"])


def downgrade() -> None:
    for table in ("concepts", "operators"):
        op.drop_index(f"ix_{table}_ontology_module", table_name=table)
        op.drop_index(f"ix_{table}_source_uri", table_name=table)
        op.drop_column(table, "ontology_module")
        op.drop_column(table, "source_uri")
