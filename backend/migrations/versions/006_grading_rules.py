from alembic import op
import sqlalchemy as sa

revision = "006_grading_rules"
down_revision = "005_templates"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("grading_presets", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("name", sa.String(255), nullable=False), sa.Column("template_version_id", sa.Uuid(), sa.ForeignKey("template_versions.id", ondelete="RESTRICT"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("grading_rule_versions", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("preset_id", sa.Uuid(), sa.ForeignKey("grading_presets.id", ondelete="RESTRICT"), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("instructions", sa.Text(), nullable=False), sa.Column("rules", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("preset_id", "version", name="uq_grading_rule_version"))
    op.create_index("ix_grading_rule_versions_preset_id", "grading_rule_versions", ["preset_id"])
    op.create_table("grading_duration_results", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("rule_version_id", sa.Uuid(), sa.ForeignKey("grading_rule_versions.id", ondelete="RESTRICT"), nullable=False), sa.Column("result", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_grading_duration_results_rule_version_id", "grading_duration_results", ["rule_version_id"])
    with op.batch_alter_table("speaking_assessments") as batch:
        batch.add_column(sa.Column("grading_rule_version_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_assessment_grading_rule_version", "grading_rule_versions", ["grading_rule_version_id"], ["id"], ondelete="RESTRICT")

def downgrade():
    with op.batch_alter_table("speaking_assessments") as batch:
        batch.drop_constraint("fk_assessment_grading_rule_version", type_="foreignkey")
        batch.drop_column("grading_rule_version_id")
    op.drop_table("grading_duration_results")
    op.drop_table("grading_rule_versions")
    op.drop_table("grading_presets")
