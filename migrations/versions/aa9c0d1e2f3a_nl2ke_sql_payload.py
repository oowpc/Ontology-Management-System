"""NL2KE run SQL翻译/执行结果。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "aa9c0d1e2f3a"
down_revision: Union[str, Sequence[str], None] = "z8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "sql_payload" not in {x["name"] for x in insp.get_columns("nl2ke_runs")}:
        op.add_column("nl2ke_runs", sa.Column("sql_payload", sa.JSON(), nullable=True))


def downgrade() -> None:
    pass
