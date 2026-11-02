"""NL2KE run 参数与完整响应。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "x6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {x["name"] for x in insp.get_columns("nl2ke_runs")}
    for col in ("params", "result_payload"):
        if col not in cols:
            op.add_column("nl2ke_runs", sa.Column(col, sa.JSON(), nullable=True))


def downgrade() -> None:
    pass
