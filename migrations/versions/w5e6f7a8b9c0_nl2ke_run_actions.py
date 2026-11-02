"""NL2KE run batch tag / gold item link。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "v4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {x["name"] for x in insp.get_columns("nl2ke_runs")}
    if "batch_tag" not in cols:
        op.add_column("nl2ke_runs", sa.Column("batch_tag", sa.String(128), nullable=True))
        op.create_index("ix_nl2ke_runs_batch_tag", "nl2ke_runs", ["batch_tag"])
    if "gold_item_id" not in cols:
        op.add_column("nl2ke_runs", sa.Column("gold_item_id", sa.Integer(), nullable=True))
        op.create_index("ix_nl2ke_runs_gold_item_id", "nl2ke_runs", ["gold_item_id"])


def downgrade() -> None:
    pass
