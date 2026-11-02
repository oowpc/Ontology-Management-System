"""Archer/外部 schema 与概念/算子 source_payload。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "u3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table, col in (("concepts", "source_payload"), ("operators", "source_payload")):
        if col not in {x["name"] for x in insp.get_columns(table)}:
            op.add_column(table, sa.Column(col, sa.JSON(), nullable=True))
    if not insp.has_table("ontology_sources"):
        op.create_table(
            "ontology_sources",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("library_id", sa.Integer(), nullable=True),
            sa.Column("key", sa.String(128), nullable=False),
            sa.Column("name", sa.String(256), nullable=False, server_default=""),
            sa.Column("schema_payload", sa.JSON(), nullable=False),
            sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["library_id"], ["ontology_libraries.id"]),
            sa.UniqueConstraint("library_id", "key", name="uq_ontology_source_key"),
        )
        op.create_index("ix_ontology_sources_library_id", "ontology_sources", ["library_id"])
        op.create_index("ix_ontology_sources_key", "ontology_sources", ["key"])


def downgrade() -> None:
    pass
