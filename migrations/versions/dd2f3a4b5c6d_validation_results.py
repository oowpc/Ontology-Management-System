"""统一校验结果表。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "dd2f3a4b5c6d"
down_revision: Union[str, Sequence[str], None] = "cc1e2f3a4b5c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "validation_results",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("library_id", sa.Integer(), nullable=True),
        sa.Column("object_type", sa.String(length=32), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=True),
        sa.Column("object_key", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("stage", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("engine", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("checked_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["ontology_libraries.id"]),
    )
    op.create_index("ix_validation_results_library_id", "validation_results", ["library_id"])
    op.create_index("ix_validation_results_object_type", "validation_results", ["object_type"])
    op.create_index("ix_validation_results_ok", "validation_results", ["ok"])
    op.create_index("ix_validation_results_checked_at", "validation_results", ["checked_at"])
    op.create_index("ix_validation_results_object", "validation_results",
                    ["object_type", "object_id"])
    op.create_index("ix_validation_results_library_time", "validation_results",
                    ["library_id", "checked_at"])


def downgrade() -> None:
    op.drop_table("validation_results")
