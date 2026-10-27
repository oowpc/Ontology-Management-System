"""api_tokens 增加 scopes 列（token 最小权限；NULL=全权限，兼容存量 token）

Revision ID: b7f2e91a4c05
Revises: 72c8a39d3507
Create Date: 2026-10-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7f2e91a4c05'
down_revision: Union[str, Sequence[str], None] = '72c8a39d3507'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('api_tokens', sa.Column('scopes', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('api_tokens', 'scopes')
