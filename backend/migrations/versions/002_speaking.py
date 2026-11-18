"""Speaking assessment foundation."""
from alembic import op
import sqlalchemy as sa

revision = "002_speaking"
down_revision = "001_queue"


def key():
    return sa.Column("id", sa.Uuid(), primary_key=True)


def upgrade():
    op.create_table("speaking_assessments", key(),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("part", sa.Integer()), sa.Column("question", sa.Text()),
        sa.Column("transcript_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table("assessment_criteria", key(),
        sa.Column("assessment_id", sa.Uuid(), sa.ForeignKey("speaking_assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(40), nullable=False), sa.Column("source", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("provider", sa.Text()), sa.Column("error", sa.Text()),
        sa.Column("score", sa.Float()), sa.Column("run_token", sa.String(36)), sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("assessment_id", "code"))
    op.create_table("speaking_feedback_items", key(),
        sa.Column("criterion_id", sa.Uuid(), sa.ForeignKey("assessment_criteria.id", ondelete="CASCADE"), nullable=False),
        sa.Column("segment_id", sa.Uuid(), nullable=False), sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False), sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("suggested_correction", sa.Text()), sa.Column("explanation", sa.Text(), nullable=False))
    op.create_table("speaking_practice_recommendations", key(),
        sa.Column("criterion_id", sa.Uuid(), sa.ForeignKey("assessment_criteria.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exercise", sa.Text(), nullable=False), sa.Column("explanation", sa.Text(), nullable=False))
    for table, column in [("speaking_assessments", "job_id"), ("assessment_criteria", "assessment_id"), ("speaking_feedback_items", "criterion_id"), ("speaking_practice_recommendations", "criterion_id")]:
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table in ["speaking_practice_recommendations", "speaking_feedback_items", "assessment_criteria", "speaking_assessments"]:
        op.drop_table(table)
