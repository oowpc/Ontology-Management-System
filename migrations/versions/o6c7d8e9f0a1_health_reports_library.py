"""health_reports 按本体库隔离。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "o6c7d8e9f0a1"
down_revision: Union[str, Sequence[str], None] = "n5b6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {x["name"] for x in insp.get_columns("health_reports")}
    if "library_id" not in cols:
        op.add_column("health_reports", sa.Column("library_id", sa.Integer(), nullable=True))
    idxs = {x["name"] for x in insp.get_indexes("health_reports")}
    if "ix_health_reports_library_id" not in idxs:
        op.create_index("ix_health_reports_library_id", "health_reports",
                        ["library_id"], unique=False)
    bind.execute(sa.text("UPDATE health_reports SET library_id=2 WHERE library_id IS NULL"))


def downgrade() -> None:
    pass
