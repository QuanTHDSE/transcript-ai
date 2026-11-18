"""Store source-specific fluency and explicit audio capability results."""
from uuid import uuid4
from alembic import op
import sqlalchemy as sa

revision = "004_audio_analysis"
down_revision = "003_text_feedback"


def upgrade():
    bind = op.get_bind()
    constraints = sa.inspect(bind).get_unique_constraints("assessment_criteria")
    old = next(c for c in constraints if c["column_names"] == ["assessment_id", "code"])
    convention = {"uq": "uq_%(table_name)s_%(column_0_name)s_%(column_1_name)s"}
    with op.batch_alter_table("assessment_criteria", naming_convention=convention) as batch:
        batch.drop_constraint(old["name"] or "uq_assessment_criteria_assessment_id_code", type_="unique")
        batch.create_unique_constraint("uq_assessment_criterion_source", ["assessment_id", "code", "source"])
        batch.add_column(sa.Column("analysis", sa.JSON()))
    op.add_column("speaking_feedback_items", sa.Column("uncertain", sa.Boolean(), nullable=False, server_default=sa.false()))
    table = sa.table("assessment_criteria", sa.column("id", sa.Uuid()), sa.column("assessment_id", sa.Uuid()), sa.column("code"), sa.column("source"), sa.column("status"), sa.column("analysis", sa.JSON()))
    ids = list(bind.execute(sa.text("SELECT id FROM speaking_assessments")).scalars())
    from uuid import UUID
    for identity in ids:
        bind.execute(table.insert().values(id=uuid4(), assessment_id=UUID(str(identity)), code="fluency_coherence", source="AUDIO", status="NOT_ASSESSED", analysis={"reason":"Created before audio fluency support; retry audio evaluation"}))


def downgrade():
    raise RuntimeError("Source-specific fluency results cannot be merged losslessly; restore a backup")
