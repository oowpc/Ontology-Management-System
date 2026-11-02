"""本体库隔离：ontology_libraries + 全量 library_id 回填。

Revision ID: n5b6c7d8e9f0
Revises: m4a5b6c7d8e9
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "n5b6c7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "m4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "concepts", "concept_parents", "operators", "import_batches",
    "annotation_batches", "annotation_members", "annotation_units",
    "annotation_evidence", "co_annotations", "co_alignments", "ke_annotations",
    "gold_kes", "ontology_versions", "ontology_suggestions", "co_merge_logs",
    "annotation_cross_reviews", "query_runs", "ontology_rules",
    "ontology_individuals",
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("ontology_libraries"):
        op.create_table(
            "ontology_libraries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key", sa.String(64), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False, server_default="business"),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("owner", sa.String(64), nullable=False, server_default="system"),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    lib_indexes = {x["name"] for x in insp.get_indexes("ontology_libraries")}
    for name, cols, unique in (
        ("ix_ontology_libraries_key", ["key"], True),
        ("ix_ontology_libraries_kind", ["kind"], False),
        ("ix_ontology_libraries_status", ["status"], False),
    ):
        if name not in lib_indexes:
            op.create_index(name, "ontology_libraries", cols, unique=unique)

    rows = {row[0] for row in bind.execute(sa.text("SELECT id FROM ontology_libraries"))}
    seed = [
        (1, "fibo-cmns", "FIBO + CMNS 参考本体", "reference",
         "FIBO 金融本体与 CMNS 通用本体，仅作参考/复制来源"),
        (2, "co-baseline", "CO v0.8 业务基线", "business",
         "政府本体 CO v0.8 业务基线（20 概念 / 5 算子）"),
        (3, "pilot-yuer", "育儿补贴 Pilot", "pilot",
         "育儿补贴政策标注试点，v1/v2 批次只写本库"),
    ]
    for lid, key, name, kind, desc in seed:
        if lid not in rows:
            bind.execute(sa.text(
                "INSERT INTO ontology_libraries "
                "(id,key,name,kind,description,owner,status,created_at) "
                "VALUES (:id,:key,:name,:kind,:desc,'system','active',CURRENT_TIMESTAMP)"),
                {"id": lid, "key": key, "name": name, "kind": kind, "desc": desc})
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text(
            "SELECT setval(pg_get_serial_sequence('ontology_libraries','id'), "
            "(SELECT MAX(id) FROM ontology_libraries), true)"))

    for table in TABLES:
        cols = {x["name"] for x in insp.get_columns(table)}
        if "library_id" not in cols:
            op.add_column(table, sa.Column("library_id", sa.Integer(), nullable=True))
        idxs = {x["name"] for x in insp.get_indexes(table)}
        idx_name = f"ix_{table}_library_id"
        if idx_name not in idxs:
            op.create_index(idx_name, table, ["library_id"], unique=False)

    # 参考本体 vs 业务基线按 domain/module 回填
    bind.execute(sa.text(
        "UPDATE concepts SET library_id = CASE "
        "WHEN domain='业务基线' OR ontology_module LIKE 'CO-v0.8%' THEN 2 ELSE 1 END "
        "WHERE library_id IS NULL"))
    bind.execute(sa.text(
        "UPDATE operators SET library_id = CASE "
        "WHEN ontology_module LIKE 'CO-v0.8%' THEN 2 ELSE 1 END WHERE library_id IS NULL"))
    bind.execute(sa.text("UPDATE concept_parents SET library_id = "
                         "(SELECT library_id FROM concepts WHERE concepts.id=concept_parents.concept_id) "
                         "WHERE library_id IS NULL"))
    bind.execute(sa.text("UPDATE import_batches SET library_id=1 WHERE library_id IS NULL"))
    bind.execute(sa.text("UPDATE ontology_rules SET library_id=2 WHERE library_id IS NULL"))
    bind.execute(sa.text("UPDATE ontology_individuals SET library_id=2 WHERE library_id IS NULL"))

    # v1/v2 以及其所有标注子表 → pilot
    bind.execute(sa.text("UPDATE annotation_batches SET library_id=3 WHERE library_id IS NULL"))
    for table in ("annotation_members", "annotation_units", "annotation_evidence",
                  "co_annotations", "co_alignments", "ke_annotations", "gold_kes",
                  "ontology_versions", "co_merge_logs", "annotation_cross_reviews",
                  "query_runs"):
        bind.execute(sa.text(
            f"UPDATE {table} SET library_id = (SELECT library_id FROM annotation_batches "
            f"WHERE annotation_batches.id={table}.batch_id) WHERE library_id IS NULL"))
    bind.execute(sa.text("UPDATE ontology_suggestions SET library_id=2 WHERE library_id IS NULL"))

    # 原全局唯一索引 → 库内唯一（新库可能已由当前模型建好索引，逐项判断）
    for idx, table in (("ix_concepts_name", "concepts"),
                       ("ix_ontology_rules_name", "ontology_rules"),
                       ("ix_ontology_versions_label", "ontology_versions")):
        if idx in {x["name"] for x in insp.get_indexes(table)}:
            op.drop_index(idx, table_name=table)
    for name, table, cols in (
        ("ix_concepts_library_name", "concepts", ["library_id", "name"]),
        ("ix_ontology_rules_library_name", "ontology_rules", ["library_id", "name"]),
        ("ix_ontology_versions_library_label", "ontology_versions", ["library_id", "label"]),
    ):
        if name not in {x["name"] for x in insp.get_indexes(table)}:
            op.create_index(name, table, cols, unique=True)


def downgrade() -> None:
    pass
