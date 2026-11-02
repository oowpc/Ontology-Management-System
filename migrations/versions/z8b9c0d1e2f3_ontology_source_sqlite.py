"""外部 schema 的 SQLite 路径。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "z8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "y7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {x["name"] for x in insp.get_columns("ontology_sources")}
    if "sqlite_path" not in cols:
        op.add_column("ontology_sources", sa.Column("sqlite_path", sa.String(512), nullable=True))


def downgrade() -> None:
    pass
