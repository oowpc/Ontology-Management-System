"""DataMaker 生成任务。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "bb0d1e2f3a4b"
down_revision: Union[str, Sequence[str], None] = "aa9c0d1e2f3a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "datamaker_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("library_id", sa.Integer(), sa.ForeignKey("ontology_libraries.id"), nullable=True),
        sa.Column("db_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("n", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("workers", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("ke_types", sa.String(512), nullable=False, server_default=""),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("workdir", sa.String(512), nullable=False, server_default=""),
        sa.Column("log_path", sa.String(512), nullable=False, server_default=""),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("annotation_batch_id", sa.Integer(),
                  sa.ForeignKey("annotation_batches.id"), nullable=True),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_datamaker_jobs_library_id", "datamaker_jobs", ["library_id"])
    op.create_index("ix_datamaker_jobs_db_id", "datamaker_jobs", ["db_id"])
    op.create_index("ix_datamaker_jobs_status", "datamaker_jobs", ["status"])
    op.create_index("ix_datamaker_jobs_annotation_batch_id", "datamaker_jobs",
                    ["annotation_batch_id"])


def downgrade() -> None:
    op.drop_table("datamaker_jobs")
