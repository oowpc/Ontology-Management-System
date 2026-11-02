"""算子描述（Archer note/path 等）。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "r9f0a1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "q8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {x["name"] for x in insp.get_columns("operators")}
    if "description" not in cols:
        op.add_column("operators", sa.Column("description", sa.Text(), nullable=False,
                                             server_default=""))


def downgrade() -> None:
    pass
