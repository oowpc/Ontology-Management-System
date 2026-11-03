"""KEOL 持续同步检查记录。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "gg5b6c7d8e9f"
down_revision: Union[str, Sequence[str], None] = "ff4a5b6c7d8e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "keol_sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("library_id", sa.Integer(), nullable=True),
        sa.Column("source_path", sa.String(512), nullable=False, server_default=""),
        sa.Column("source_hash", sa.String(128), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="checked"),
        sa.Column("counts", sa.JSON(), nullable=True),
        sa.Column("diff", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("checked_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["ontology_libraries.id"]),
    )
    op.create_index("ix_keol_sync_runs_library_id", "keol_sync_runs", ["library_id"])
    op.create_index("ix_keol_sync_runs_source_hash", "keol_sync_runs", ["source_hash"])


def downgrade() -> None:
    op.drop_table("keol_sync_runs")
