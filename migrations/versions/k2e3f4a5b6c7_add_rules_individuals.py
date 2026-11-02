"""全局规则/个体（KEOL spec_v1 对齐）。

Revision ID: k2e3f4a5b6c7
Revises: j1d2e3f4a5b6
Create Date: 2026-10-28
"""
from typing import Sequence, Union

from alembic import op

from app.db import Base
from app import models  # noqa: F401


revision: str = "k2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "j1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TABLES = ["ontology_rules", "ontology_individuals"]


def upgrade() -> None:
    bind = op.get_bind()
    for name in NEW_TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    pass
