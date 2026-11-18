from alembic import op
import sqlalchemy as sa
revision = "007_recording_mapping"
down_revision = "006_grading_rules"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("mapping_batches", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("template_version_id", sa.Uuid(), sa.ForeignKey("template_versions.id", ondelete="RESTRICT"), nullable=False), sa.Column("rule_version_id", sa.Uuid(), sa.ForeignKey("grading_rule_versions.id", ondelete="RESTRICT"), nullable=False), sa.Column("duration_metric", sa.String(24), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("recording_mappings", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("mapping_batches.id", ondelete="CASCADE"), nullable=False), sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False), sa.Column("student_id", sa.String(255)), sa.Column("part", sa.Integer()), sa.Column("question_id", sa.String(500)), sa.Column("status", sa.String(24), nullable=False), sa.Column("analysis_status", sa.String(24), nullable=False), sa.Column("duration", sa.JSON()), sa.Column("error", sa.String(500)), sa.UniqueConstraint("batch_id", "job_id", name="uq_batch_recording"))
    op.create_index("ix_recording_mappings_batch_id", "recording_mappings", ["batch_id"])
    op.create_index("ix_recording_mappings_job_id", "recording_mappings", ["job_id"])

def downgrade():
    op.drop_table("recording_mappings")
    op.drop_table("mapping_batches")
