"""import_batches：KEOL 导入批次（统计/来源/回滚 ID）

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=128), nullable=False, server_default="keol-sync"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="completed"),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("files", sa.JSON(), nullable=True),
        sa.Column("created_concept_ids", sa.JSON(), nullable=True),
        sa.Column("updated_concept_ids", sa.JSON(), nullable=True),
        sa.Column("created_operator_ids", sa.JSON(), nullable=True),
        sa.Column("updated_operator_ids", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id"),
    )
    op.create_index("ix_import_batches_batch_id", "import_batches", ["batch_id"])
    op.create_index("ix_import_batches_status", "import_batches", ["status"])


def downgrade() -> None:
    op.drop_index("ix_import_batches_status", table_name="import_batches")
    op.drop_index("ix_import_batches_batch_id", table_name="import_batches")
    op.drop_table("import_batches")
