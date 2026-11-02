"""NL2KE BYOS 运行记录。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "t1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("nl2ke_runs"):
        return
    op.create_table(
        "nl2ke_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("library_id", sa.Integer(), nullable=True),
        sa.Column("db_id", sa.String(128), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("ke", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("verify", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("verify_passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("timings", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["library_id"], ["ontology_libraries.id"]),
    )
    op.create_index("ix_nl2ke_runs_library_id", "nl2ke_runs", ["library_id"])
    op.create_index("ix_nl2ke_runs_db_id", "nl2ke_runs", ["db_id"])
    op.create_index("ix_nl2ke_runs_status", "nl2ke_runs", ["status"])


def downgrade() -> None:
    pass
