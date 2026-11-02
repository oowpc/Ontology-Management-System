"""DataMaker worker claim/lease 字段。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cc1e2f3a4b5c"
down_revision: Union[str, Sequence[str], None] = "bb0d1e2f3a4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {x["name"] for x in insp.get_columns("datamaker_jobs")}
    if "worker_id" not in cols:
        op.add_column("datamaker_jobs", sa.Column("worker_id", sa.String(128), nullable=True))
    if "heartbeat_at" not in cols:
        op.add_column("datamaker_jobs", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    pass
