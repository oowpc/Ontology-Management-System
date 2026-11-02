"""版本完整快照 + Token 过期/IP 元数据。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "k2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "payload" not in _cols("ontology_versions"):
        op.add_column("ontology_versions", sa.Column("payload", sa.JSON(), nullable=True))
    token_cols = _cols("api_tokens")
    if "expires_at" not in token_cols:
        op.add_column("api_tokens", sa.Column("expires_at", sa.DateTime(), nullable=True))
    if "last_used_ip" not in token_cols:
        op.add_column("api_tokens", sa.Column("last_used_ip", sa.String(length=64), nullable=True))
    if "token_name" not in _cols("audit_logs"):
        op.add_column("audit_logs", sa.Column("token_name", sa.String(length=64), nullable=True))


def downgrade() -> None:
    cols = _cols("api_tokens")
    if "last_used_ip" in cols:
        op.drop_column("api_tokens", "last_used_ip")
    if "expires_at" in cols:
        op.drop_column("api_tokens", "expires_at")
    if "token_name" in _cols("audit_logs"):
        op.drop_column("audit_logs", "token_name")
    if "payload" in _cols("ontology_versions"):
        op.drop_column("ontology_versions", "payload")
