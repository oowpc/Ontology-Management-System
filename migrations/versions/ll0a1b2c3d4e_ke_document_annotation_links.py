"""KE 文档关联标注批次与人工 KE。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ll0a1b2c3d4e"
down_revision: Union[str, Sequence[str], None] = "kk9f0a1b2c3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {x["name"] for x in sa.inspect(bind).get_columns("ke_documents")}
    if "annotation_batch_id" not in cols:
        op.add_column("ke_documents", sa.Column("annotation_batch_id", sa.Integer(), nullable=True))
        op.create_index("ix_ke_documents_annotation_batch_id", "ke_documents",
                        ["annotation_batch_id"])
    for table in ("ke_annotations", "gold_kes"):
        cols = {x["name"] for x in sa.inspect(bind).get_columns(table)}
        if "ke_document_id" not in cols:
            op.add_column(table, sa.Column("ke_document_id", sa.Integer(), nullable=True))
            op.create_index(f"ix_{table}_ke_document_id", table, ["ke_document_id"])


def downgrade() -> None:
    pass
