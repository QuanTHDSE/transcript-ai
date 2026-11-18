"""Initialize existing model tables and upgrade the earlier scaffold."""
from alembic import op
import sqlalchemy as sa

revision = "001_queue"
down_revision = None


def upgrade():
    # Prior scaffold had models but no migrations. Preserve any existing data.
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if "jobs" not in tables:
        op.create_table("jobs",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("original_filename", sa.String(255), nullable=False),
            sa.Column("stored_filename", sa.String(255), nullable=False, unique=True),
            sa.Column("media_type", sa.String(100), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("error_message", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("completed_at", sa.DateTime(timezone=True)))
        op.create_index("ix_jobs_created_id", "jobs", ["created_at", "id"])
    if "segments" not in tables:
        op.create_table("segments",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("start_ms", sa.BigInteger(), nullable=False),
            sa.Column("end_ms", sa.BigInteger(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.UniqueConstraint("job_id", "ordinal"),
            sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_segment_times"))
        op.create_index("ix_segments_job_id", "segments", ["job_id"])
    columns = {c["name"] for c in sa.inspect(bind).get_columns("jobs")}
    additions = [
        sa.Column("media_type", sa.String(100), nullable=False, server_default="application/octet-stream"),
        sa.Column("stage", sa.String(30), nullable=False, server_default="waiting"),
        sa.Column("language", sa.String(10), nullable=False, server_default="auto"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
    ]
    for column in additions:
        if column.name not in columns:
            op.add_column("jobs", column)
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("jobs")}
    if "ix_jobs_status" not in indexes:
        op.create_index("ix_jobs_status", "jobs", ["status"])
    op.execute("UPDATE jobs SET status = UPPER(status)")
    op.execute("UPDATE jobs SET stage = CASE status WHEN 'COMPLETED' THEN 'done' WHEN 'FAILED' THEN 'failed' WHEN 'CANCELED' THEN 'canceled' ELSE 'waiting' END")
    # Legacy PROCESSING jobs have no lease and cannot safely be assumed alive.
    op.execute("UPDATE jobs SET status='FAILED', stage='failed', error_message='Legacy interrupted job; retry manually.' WHERE status='PROCESSING' AND lease_until IS NULL")


def downgrade():
    raise RuntimeError("Destructive downgrade is unsupported; restore a database backup")
