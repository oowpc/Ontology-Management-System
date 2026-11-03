"""DataMaker 原生 API 任务 ID。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "jj8e9f0a1b2c"
down_revision: Union[str, Sequence[str], None] = "ii7d8e9f0a1b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {x["name"] for x in sa.inspect(bind).get_columns("datamaker_jobs")}
    if "native_job_id" not in cols:
        op.add_column("datamaker_jobs", sa.Column("native_job_id", sa.String(64), nullable=True))
        op.create_index("ix_datamaker_jobs_native_job_id", "datamaker_jobs", ["native_job_id"])


def downgrade() -> None:
    pass
