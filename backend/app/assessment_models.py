"""Speaking feedback is independent of, and never rewrites, transcript data."""
import uuid
from datetime import datetime
from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, Integer, Float, Boolean, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models import Base


class SpeakingAssessment(Base):
    __tablename__ = "speaking_assessments"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    grading_rule_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("grading_rule_versions.id", ondelete="RESTRICT"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    part: Mapped[int | None] = mapped_column(Integer)
    question: Mapped[str | None] = mapped_column(Text)
    transcript_snapshot: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    criteria: Mapped[list["AssessmentCriterion"]] = relationship(cascade="all, delete-orphan", order_by="AssessmentCriterion.code")


class AssessmentCriterion(Base):
    __tablename__ = "assessment_criteria"
    __table_args__ = (UniqueConstraint("assessment_id", "code", "source", name="uq_assessment_criterion_source"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("speaking_assessments.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    provider: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    analysis: Mapped[dict | None] = mapped_column(JSON)
    run_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback: Mapped[list["SpeakingFeedbackItem"]] = relationship(cascade="all, delete-orphan")
    recommendations: Mapped[list["SpeakingPracticeRecommendation"]] = relationship(cascade="all, delete-orphan")


class SpeakingFeedbackItem(Base):
    __tablename__ = "speaking_feedback_items"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    criterion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_criteria.id", ondelete="CASCADE"), index=True)
    segment_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[str] = mapped_column(Text)
    suggested_correction: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(40))
    issue_type: Mapped[str | None] = mapped_column(String(20))
    explanation_vi: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str | None] = mapped_column(String(10))
    confidence: Mapped[float | None] = mapped_column(Float)
    uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    suggested_practice: Mapped[str | None] = mapped_column(Text)


class SpeakingTextCache(Base):
    __tablename__ = "speaking_text_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    response: Mapped[dict] = mapped_column(JSON)


class SpeakingPracticeRecommendation(Base):
    __tablename__ = "speaking_practice_recommendations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    criterion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_criteria.id", ondelete="CASCADE"), index=True)
    exercise: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
