"""KEOL/agent 本体建议箱。"""
from typing import Sequence, Union

from alembic import op

from app.db import Base
from app import models  # noqa: F401

revision: str = "m4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "l3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    Base.metadata.tables["ontology_suggestions"].create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    pass
