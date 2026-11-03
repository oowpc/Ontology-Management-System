"""DataMaker 自动生成 GOLD 草稿计数。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ee3f4a5b6c7d"
down_revision: Union[str, Sequence[str], None] = "dd2f3a4b5c6d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {x["name"] for x in sa.inspect(bind).get_columns("datamaker_jobs")}
    if "gold_count" not in cols:
        op.add_column("datamaker_jobs",
                      sa.Column("gold_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    pass
