"""概念 description + 全量回填。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "s0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {x["name"] for x in insp.get_columns("concepts")}
    if "description" not in cols:
        op.add_column("concepts", sa.Column("description", sa.Text(), nullable=False,
                                            server_default=""))
    # 概念优先用定义；定义空则生成最小描述
    bind.execute(sa.text(
        "UPDATE concepts SET description = definition WHERE description='' AND definition!=''"
    ))
    bind.execute(sa.text(
        "UPDATE concepts SET description = '概念 ' || name || '（' || domain || '）' "
        "WHERE description=''"
    ))
    # 算子说明为空时至少给出签名描述
    bind.execute(sa.text(
        "UPDATE operators SET description = '算子 ' || name || '：主体 #' || "
        "COALESCE(CAST(subject_concept_id AS TEXT),'?') || ' → ' || return_type "
        "WHERE description=''"
    ))


def downgrade() -> None:
    pass
