"""DataAgent Monitor 事件收件箱。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "hh6c7d8e9f0a"
down_revision: Union[str, Sequence[str], None] = "gg5b6c7d8e9f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "monitor_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_event_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("product_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("source_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("category", sa.String(32), nullable=False, server_default="content"),
        sa.Column("field", sa.String(64), nullable=False, server_default=""),
        sa.Column("analysis_status", sa.String(32), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("before_preview", sa.Text(), nullable=False, server_default=""),
        sa.Column("after_preview", sa.Text(), nullable=False, server_default=""),
        sa.Column("official_url", sa.String(1024), nullable=False, server_default=""),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("issue_id", sa.Integer(), nullable=True),
        sa.Column("received_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"]),
        sa.UniqueConstraint("fingerprint", name="uq_monitor_event_fingerprint"),
    )
    for name, cols in (
        ("ix_monitor_events_source_event_id", ["source_event_id"]),
        ("ix_monitor_events_fingerprint", ["fingerprint"]),
        ("ix_monitor_events_product_id", ["product_id"]),
        ("ix_monitor_events_source_id", ["source_id"]),
        ("ix_monitor_events_severity", ["severity"]),
        ("ix_monitor_events_category", ["category"]),
        ("ix_monitor_events_status", ["status"]),
        ("ix_monitor_events_issue_id", ["issue_id"]),
    ):
        op.create_index(name, "monitor_events", cols)


def downgrade() -> None:
    op.drop_table("monitor_events")
