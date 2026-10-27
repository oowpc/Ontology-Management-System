"""concept_parents：KE 多父类关联表（回填 concepts.parent_id）

Revision ID: c3f2a9b71d40
Revises: b7f2e91a4c05
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3f2a9b71d40"
down_revision: Union[str, Sequence[str], None] = "b7f2e91a4c05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "concept_parents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("concept_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("concept_id != parent_id", name="ck_concept_parent_self"),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["concepts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("concept_id", "parent_id", name="uq_concept_parent"),
    )
    op.create_index("ix_concept_parents_concept_id", "concept_parents", ["concept_id"])
    op.create_index("ix_concept_parents_parent_id", "concept_parents", ["parent_id"])
    op.execute(
        "INSERT INTO concept_parents (concept_id, parent_id, is_primary, position) "
        "SELECT id, parent_id, 1, 0 FROM concepts WHERE parent_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_concept_parents_parent_id", table_name="concept_parents")
    op.drop_index("ix_concept_parents_concept_id", table_name="concept_parents")
    op.drop_table("concept_parents")
