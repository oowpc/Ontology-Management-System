"""DataMaker GOLD 质量门禁快照。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ii7d8e9f0a1b"
down_revision: Union[str, Sequence[str], None] = "hh6c7d8e9f0a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {x["name"] for x in sa.inspect(bind).get_columns("datamaker_jobs")}
    if "gold_gate" not in cols:
        op.add_column("datamaker_jobs", sa.Column("gold_gate", sa.JSON(), nullable=True))


def downgrade() -> None:
    pass
