"""NL2KE run → 标注批次/单元链接。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "x6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "w5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {x["name"] for x in insp.get_columns("nl2ke_runs")}
    for col, table in (("annotation_batch_id", "annotation_batches"),
                       ("annotation_unit_id", "annotation_units")):
        if col not in cols:
            op.add_column("nl2ke_runs", sa.Column(col, sa.Integer(), nullable=True))
            op.create_index(f"ix_nl2ke_runs_{col}", "nl2ke_runs", [col])


def downgrade() -> None:
    pass
