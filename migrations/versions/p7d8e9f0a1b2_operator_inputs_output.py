"""算子输入/输出：operator_inputs + output_concept_id。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "p7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "o6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {x["name"] for x in insp.get_columns("operators")}
    if "output_concept_id" not in cols:
        op.add_column("operators", sa.Column("output_concept_id", sa.Integer(), nullable=True))
    op_indexes = {x["name"] for x in insp.get_indexes("operators")}
    if "ix_operators_output_concept_id" not in op_indexes:
        op.create_index("ix_operators_output_concept_id", "operators",
                        ["output_concept_id"], unique=False)

    if not insp.has_table("operator_inputs"):
        op.create_table(
            "operator_inputs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("library_id", sa.Integer(), nullable=True),
            sa.Column("operator_id", sa.Integer(), nullable=False),
            sa.Column("concept_id", sa.Integer(), nullable=True),
            sa.Column("type_name", sa.String(256), nullable=False, server_default=""),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.ForeignKeyConstraint(["library_id"], ["ontology_libraries.id"]),
            sa.ForeignKeyConstraint(["operator_id"], ["operators.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"]),
            sa.UniqueConstraint("operator_id", "position", name="uq_operator_input_position"),
        )
        for name, cols_ in (("ix_operator_inputs_library_id", ["library_id"]),
                            ("ix_operator_inputs_operator_id", ["operator_id"]),
                            ("ix_operator_inputs_concept_id", ["concept_id"]),
                            ("ix_operator_inputs_position", ["position"])):
            op.create_index(name, "operator_inputs", cols_, unique=False)

    # 每个既有算子至少补一个主体输入，签名才不会退化
    bind.execute(sa.text(
        "INSERT INTO operator_inputs (library_id, operator_id, concept_id, type_name, position) "
        "SELECT o.library_id, o.id, o.subject_concept_id, COALESCE(c.name,'Concept'), 0 "
        "FROM operators o LEFT JOIN concepts c ON c.id=o.subject_concept_id "
        "WHERE NOT EXISTS (SELECT 1 FROM operator_inputs i WHERE i.operator_id=o.id)"
    ))
    # 返回类型正好等于本库概念名时，补输出概念外键（Set[...] 等保持为空）
    bind.execute(sa.text(
        "UPDATE operators SET output_concept_id = ("
        "SELECT c.id FROM concepts c WHERE c.library_id=operators.library_id "
        "AND c.name=operators.return_type LIMIT 1) "
        "WHERE output_concept_id IS NULL"
    ))


def downgrade() -> None:
    pass
