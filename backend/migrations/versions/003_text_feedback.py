from alembic import op
import sqlalchemy as sa

revision = "003_text_feedback"
down_revision = "002_speaking"


def upgrade():
    for column in [sa.Column("category", sa.String(40)), sa.Column("issue_type", sa.String(20)), sa.Column("explanation_vi", sa.Text()), sa.Column("severity", sa.String(10)), sa.Column("confidence", sa.Float()), sa.Column("suggested_practice", sa.Text())]:
        op.add_column("speaking_feedback_items", column)
    op.create_table("speaking_text_cache", sa.Column("key", sa.String(64), primary_key=True), sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False), sa.Column("response", sa.JSON(), nullable=False))
    op.create_index("ix_speaking_text_cache_job_id", "speaking_text_cache", ["job_id"])


def downgrade():
    op.drop_table("speaking_text_cache")
    for name in ["suggested_practice", "confidence", "severity", "explanation_vi", "issue_type", "category"]:
        op.drop_column("speaking_feedback_items", name)
