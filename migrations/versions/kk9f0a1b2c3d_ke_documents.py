"""统一 KE 文档与版本。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "kk9f0a1b2c3d"
down_revision: Union[str, Sequence[str], None] = "jj8e9f0a1b2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ke_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("library_id", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(256), nullable=False, server_default=""),
        sa.Column("question", sa.Text(), nullable=False, server_default=""),
        sa.Column("ke_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="candidate"),
        sa.Column("ontology_version_id", sa.Integer(), nullable=True),
        sa.Column("gold_item_id", sa.Integer(), nullable=True),
        sa.Column("validation", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["ontology_libraries.id"]),
        sa.ForeignKeyConstraint(["ontology_version_id"], ["ontology_versions.id"]),
        sa.UniqueConstraint("library_id", "source_type", "source_ref",
                            name="uq_ke_document_source"),
    )
    op.create_index("ix_ke_documents_library_id", "ke_documents", ["library_id"])
    op.create_index("ix_ke_documents_source_type", "ke_documents", ["source_type"])
    op.create_index("ix_ke_documents_content_hash", "ke_documents", ["content_hash"])
    op.create_index("ix_ke_documents_status", "ke_documents", ["status"])
    op.create_index("ix_ke_documents_ontology_version_id", "ke_documents",
                    ["ontology_version_id"])
    op.create_index("ix_ke_documents_gold_item_id", "ke_documents", ["gold_item_id"])
    op.create_index("ix_ke_documents_library_status", "ke_documents",
                    ["library_id", "status"])
    op.create_table(
        "ke_document_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("library_id", sa.Integer(), nullable=True),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("ke_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("reason", sa.String(256), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["ontology_libraries.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["ke_documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "version", name="uq_ke_document_version"),
    )
    op.create_index("ix_ke_document_versions_library_id", "ke_document_versions",
                    ["library_id"])
    op.create_index("ix_ke_document_versions_document_id", "ke_document_versions",
                    ["document_id"])
    op.add_column("gold_items", sa.Column("ke_document_id", sa.Integer(), nullable=True))
    op.create_index("ix_gold_items_ke_document_id", "gold_items", ["ke_document_id"])
    op.add_column("nl2ke_runs", sa.Column("ke_document_id", sa.Integer(), nullable=True))
    op.create_index("ix_nl2ke_runs_ke_document_id", "nl2ke_runs", ["ke_document_id"])


def downgrade() -> None:
    pass
