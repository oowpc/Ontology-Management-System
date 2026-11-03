"""GOLD / 题库 / Issue 增加 library_id 并回填。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ff4a5b6c7d8e"
down_revision: Union[str, Sequence[str], None] = "ee3f4a5b6c7d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "gold_articles", "gold_schedule_cells", "gold_issues", "gold_issue_comments",
    "gold_samples", "gold_sample_items", "gold_sample_reviews", "gold_items",
    "questions", "question_concepts", "question_ke_versions", "question_issues",
    "issues", "issue_cases", "issue_questions", "issue_viewpoints",
    "issue_comments", "issue_links", "issue_impacts", "ke_uploads", "ke_tasks",
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in TABLES:
        cols = {x["name"] for x in insp.get_columns(table)}
        if "library_id" not in cols:
            op.add_column(table, sa.Column("library_id", sa.Integer(), nullable=True))
            op.create_index(f"ix_{table}_library_id", table, ["library_id"])
    # GOLD 草稿：优先归属生成它的 DataMaker job / NL2KE run，其余回默认库。
    op.execute("""
        UPDATE gold_items SET library_id = COALESCE(
            (SELECT dj.library_id FROM datamaker_jobs dj
             WHERE 'DM-' || dj.id = gold_items.article_code),
            (SELECT nr.library_id FROM nl2ke_runs nr
             WHERE nr.gold_item_id = gold_items.id),
            2)
        WHERE library_id IS NULL
    """)
    op.execute("UPDATE gold_sample_items SET library_id = COALESCE((SELECT g.library_id FROM gold_items g WHERE g.id = gold_sample_items.gold_item_id), 2) WHERE library_id IS NULL")
    op.execute("UPDATE gold_sample_reviews SET library_id = COALESCE((SELECT s.library_id FROM gold_samples s WHERE s.id = gold_sample_reviews.sample_id), 2) WHERE library_id IS NULL")
    op.execute("UPDATE gold_issue_comments SET library_id = COALESCE((SELECT i.library_id FROM gold_issues i WHERE i.id = gold_issue_comments.issue_id), 2) WHERE library_id IS NULL")
    op.execute("UPDATE question_concepts SET library_id = COALESCE((SELECT q.library_id FROM questions q WHERE q.id = question_concepts.question_id), 2) WHERE library_id IS NULL")
    op.execute("UPDATE question_ke_versions SET library_id = COALESCE((SELECT q.library_id FROM questions q WHERE q.id = question_ke_versions.question_id), 2) WHERE library_id IS NULL")
    op.execute("UPDATE question_issues SET library_id = COALESCE((SELECT q.library_id FROM questions q WHERE q.id = question_issues.question_id), 2) WHERE library_id IS NULL")
    for child in ("issue_cases", "issue_questions", "issue_viewpoints", "issue_comments", "issue_links", "issue_impacts"):
        op.execute(f"UPDATE {child} SET library_id = COALESCE((SELECT i.library_id FROM issues i WHERE i.id = {child}.issue_id), 2) WHERE library_id IS NULL")
    for table in TABLES:
        op.execute(f"UPDATE {table} SET library_id = 2 WHERE library_id IS NULL")


def downgrade() -> None:
    pass
