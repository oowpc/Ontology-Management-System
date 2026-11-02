"""概念库内编号 local_code。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "s0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "r9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PREFIX = {"fibo-cmns": "FIBO", "co-baseline": "CO", "pilot-yuer": "PILOT", "archer": "ARCH"}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {x["name"] for x in insp.get_columns("concepts")}
    if "local_code" not in cols:
        op.add_column("concepts", sa.Column("local_code", sa.String(32), nullable=True))
        op.create_index("ix_concepts_local_code", "concepts", ["local_code"], unique=False)
    # 按库/全局 id 顺序生成稳定编号
    libs = {row[0]: row[1] for row in bind.execute(
        sa.text("SELECT id,key FROM ontology_libraries"))}
    for lid, key in libs.items():
        prefix = PREFIX.get(key, (key.upper().replace("-", "")[:6] or "LOC"))
        rows = bind.execute(sa.text(
            "SELECT id FROM concepts WHERE library_id=:lid ORDER BY id"), {"lid": lid}).fetchall()
        for n, (cid,) in enumerate(rows, 1):
            bind.execute(sa.text(
                "UPDATE concepts SET local_code=:code WHERE id=:cid"),
                {"code": f"{prefix}-{n:04d}", "cid": cid})
    # 兼容可能存在的空 library_id 行
    nulls = bind.execute(sa.text(
        "SELECT id FROM concepts WHERE local_code IS NULL ORDER BY id")).fetchall()
    for n, (cid,) in enumerate(nulls, 1):
        bind.execute(sa.text("UPDATE concepts SET local_code=:code WHERE id=:cid"),
                     {"code": f"LOC-{n:04d}", "cid": cid})
    try:
        op.create_index("uq_concepts_library_code", "concepts",
                        ["library_id", "local_code"], unique=True)
    except Exception:
        pass


def downgrade() -> None:
    pass
