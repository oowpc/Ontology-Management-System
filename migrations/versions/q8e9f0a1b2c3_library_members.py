"""本体库成员权限。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "p7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("library_members"):
        op.create_table(
            "library_members",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("library_id", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(16), nullable=False, server_default="annotator"),
            sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["library_id"], ["ontology_libraries.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.UniqueConstraint("library_id", "user_id", name="uq_library_member"),
            sa.CheckConstraint("role IN ('owner','reviewer','annotator')",
                               name="ck_library_member_role"),
        )
    idxs = {x["name"] for x in insp.get_indexes("library_members")}
    for name, cols in (("ix_library_members_library_id", ["library_id"]),
                       ("ix_library_members_user_id", ["user_id"])):
        if name not in idxs:
            op.create_index(name, "library_members", cols, unique=False)

    # 现有用户默认加入业务基线；参考库只读；Pilot 只给当前批次参与者。
    bind.execute(sa.text(
        "INSERT INTO library_members (library_id,user_id,role,created_by,created_at) "
        "SELECT 2,u.id,CASE WHEN u.role='reviewer' THEN 'owner' ELSE 'annotator' END,"
        "'system',CURRENT_TIMESTAMP FROM users u WHERE u.role != 'admin' "
        "AND NOT EXISTS (SELECT 1 FROM library_members m WHERE m.library_id=2 AND m.user_id=u.id)"
    ))
    bind.execute(sa.text(
        "INSERT INTO library_members (library_id,user_id,role,created_by,created_at) "
        "SELECT 1,u.id,CASE WHEN u.role='reviewer' THEN 'reviewer' ELSE 'annotator' END,"
        "'system',CURRENT_TIMESTAMP FROM users u WHERE u.role != 'admin' "
        "AND NOT EXISTS (SELECT 1 FROM library_members m WHERE m.library_id=1 AND m.user_id=u.id)"
    ))
    bind.execute(sa.text(
        "INSERT INTO library_members (library_id,user_id,role,created_by,created_at) "
        "SELECT 3,u.id,CASE WHEN u.role='reviewer' THEN 'reviewer' ELSE 'annotator' END,"
        "'system',CURRENT_TIMESTAMP FROM users u "
        "JOIN (SELECT DISTINCT m.username FROM annotation_members m "
        "      JOIN annotation_batches b ON b.id=m.batch_id WHERE b.library_id=3) x "
        "ON x.username=u.username WHERE u.role != 'admin' "
        "AND NOT EXISTS (SELECT 1 FROM library_members m WHERE m.library_id=3 AND m.user_id=u.id)"
    ))


def downgrade() -> None:
    pass
