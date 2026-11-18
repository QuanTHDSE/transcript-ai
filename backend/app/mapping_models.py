import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base

class MappingBatch(Base):
    __tablename__ = "mapping_batches"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    template_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("template_versions.id", ondelete="RESTRICT"))
    rule_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("grading_rule_versions.id", ondelete="RESTRICT"))
    duration_metric: Mapped[str] = mapped_column(String(24), default="task_elapsed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class RecordingMapping(Base):
    __tablename__ = "recording_mappings"
    __table_args__ = (UniqueConstraint("batch_id", "job_id", name="uq_batch_recording"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mapping_batches.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str | None] = mapped_column(String(255))
    part: Mapped[int | None] = mapped_column(Integer)
    question_id: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(24), default="UNMAPPED")
    analysis_status: Mapped[str] = mapped_column(String(24), default="NOT_STARTED")
    duration: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(500))
