"""标注工作流补充：批次 domain + SQL/LEAN/SPARQL 执行留痕 query_runs。

Revision ID: j1d2e3f4a5b6
Revises: i9c0d1e2f3a4
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app.db import Base
from app import models  # noqa: F401


revision: str = "j1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "i9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    have = {c["name"] for c in insp.get_columns("annotation_batches")}
    if "domain" not in have:
        op.add_column("annotation_batches", sa.Column("domain", sa.String(length=64),
                                                      nullable=True, server_default="通用"))
    Base.metadata.tables["query_runs"].create(bind, checkfirst=True)


def downgrade() -> None:
    pass
