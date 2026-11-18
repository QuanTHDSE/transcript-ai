"""Independent evaluator adapters. No production mock scores or transcript correction."""
import importlib
import os
from copy import deepcopy
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.assessment_models import SpeakingAssessment, AssessmentCriterion, SpeakingFeedbackItem, SpeakingPracticeRecommendation
from app.assessment_schemas import AssessmentCreate, AssessmentResponse, CriterionResult
from app.models import TranscriptionJob
from app.routes import DB
from app.tasks import database_engine, now
from app.celery_app import celery_app
from sqlalchemy.orm import Session

TEXT_CODES = {"fluency_coherence", "lexical_resource", "grammar_accuracy"}
AUDIO_CODES = {"pronunciation", "fluency_coherence"}


class TextEvaluator(Protocol):
    def evaluate_text(self, context: dict) -> list[CriterionResult]: ...


class AudioEvaluator(Protocol):
    def evaluate_audio(self, media: Path, context: dict) -> list[CriterionResult]: ...


def provider_for(source):
    configured = os.getenv(f"SPEAKING_{source}_PROVIDER", "app.speaking_audio:create_evaluator" if source == "AUDIO" else "")
    if not configured:
        return None
    module, factory = configured.split(":", 1)
    return getattr(importlib.import_module(module), factory)()


def aggregate(assessment):
    statuses = {c.status for c in assessment.criteria}
    assessment.status = ("PROCESSING" if "PROCESSING" in statuses else "QUEUED" if "QUEUED" in statuses
                         else "COMPLETED" if statuses == {"COMPLETED"} else "NOT_ASSESSED" if statuses == {"NOT_ASSESSED"} else "PARTIAL" if "COMPLETED" in statuses else "FAILED")


def dispatch(identity, sources=("TEXT", "AUDIO")):
    for source in sources:
        try:
            evaluate_assessment.apply_async(args=[str(identity), source], retry=False, expires=90)
        except Exception:
            # QUEUED criterion rows are recovered through the existing scheduler.
            import logging
            logging.getLogger(__name__).exception("Assessment publication failed")


def run_evaluation(identity, source, engine=None, provider=None):
    if source not in {"TEXT", "AUDIO"}:
        raise ValueError("Unknown evaluation source")
    engine = engine if engine is not None else database_engine()
    identity, token = UUID(str(identity)), str(uuid4())
    with Session(engine) as db, db.begin():
        assessment = db.scalars(select(SpeakingAssessment).where(SpeakingAssessment.id == identity).with_for_update()).first()
        if not assessment:
            return
        criteria = [c for c in assessment.criteria if c.source == source]
        if any(c.status == "PROCESSING" for c in criteria) or not any(c.status == "QUEUED" for c in criteria):
            return
        for criterion in criteria:
            criterion.status, criterion.run_token = "PROCESSING", token
            criterion.lease_until = now() + timedelta(seconds=max(60, int(os.getenv("JOB_TIME_LIMIT", "1800"))) + 60)
        aggregate(assessment)
        context = {"job_id": str(assessment.job_id), "segments": assessment.transcript_snapshot, "part": assessment.part, "question": assessment.question, "language": "en"}
        filename = db.get(TranscriptionJob, assessment.job_id).stored_filename
    result, failure, unavailable = [], None, False
    try:
        evaluator = provider if provider is not None else provider_for(source)
        if evaluator is None:
            unavailable = True
        else:
            if source == "TEXT":
                raw = evaluator.evaluate_text(deepcopy(context))
            else:
                root = Path(os.getenv("UPLOAD_DIR", "storage/uploads")).resolve()
                media = (root / filename).resolve(strict=True)
                if media.parent != root or not media.is_file():
                    raise ValueError("Invalid media path")
                raw = evaluator.evaluate_audio(media, deepcopy(context))
            result = [CriterionResult.model_validate(item) for item in raw]
            allowed = TEXT_CODES if source == "TEXT" else AUDIO_CODES
            # Existing third-party audio providers may only implement pronunciation.
            if source == "AUDIO" and {item.code for item in result} == {"pronunciation"}:
                result.append(CriterionResult(code="fluency_coherence", status="NOT_ASSESSED", analysis={"reason":"Audio fluency unsupported by provider"}))
            if {item.code for item in result} != allowed or len(result) != len(allowed):
                raise ValueError("Provider must return exactly its supported criteria")
            segments = {s["id"]: s for s in context["segments"]}
            for item in result:
                if item.status == "NOT_ASSESSED" and (item.score is not None or item.feedback):
                    raise ValueError("Unsupported criteria cannot contain scores or findings")
                for finding in item.feedback:
                    segment = segments.get(str(finding.segment_id))
                    duration = (item.analysis or {}).get("duration_ms") if source == "AUDIO" else None
                    valid_time = (0 <= finding.start_ms < finding.end_ms <= duration) if isinstance(duration, int) else (segment is not None and segment["start_ms"] <= finding.start_ms < finding.end_ms <= segment["end_ms"])
                    if not segment or not valid_time:
                        raise ValueError("Feedback must reference a valid segment and time interval")
                    if source == "TEXT" and finding.evidence not in segment["text"]:
                        raise ValueError("Text evidence must quote the source snapshot")
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("Speaking evaluation failed")
        from app.speaking_text import LLMError
        failure = str(exc) if isinstance(exc, LLMError) else "Evaluator failed or returned invalid evidence; check worker logs."
    with Session(engine) as db, db.begin():
        assessment = db.scalars(select(SpeakingAssessment).where(SpeakingAssessment.id == identity).with_for_update()).first()
        if not assessment:
            return
        criteria = [c for c in assessment.criteria if c.source == source]
        if any(c.run_token != token or c.status != "PROCESSING" for c in criteria):
            return
        by_code = {item.code: item for item in result}
        for criterion in criteria:
            item = by_code.get(criterion.code)
            criterion.status = "UNAVAILABLE" if unavailable else "FAILED" if failure else item.status
            criterion.error = "No evaluator configured for this source." if unavailable else failure
            criterion.provider = os.getenv(f"SPEAKING_{source}_PROVIDER") or None
            criterion.run_token, criterion.lease_until = None, None
            if criterion.status in {"COMPLETED", "NOT_ASSESSED"}:
                criterion.score = item.score
                criterion.analysis = item.analysis
                criterion.feedback = [SpeakingFeedbackItem(**finding.model_dump(exclude={"original_text", "corrected_text", "explanation_en", "start_time", "end_time", "recommended_practice"})) for finding in item.feedback]
                criterion.recommendations = [SpeakingPracticeRecommendation(**r.model_dump()) for r in item.recommendations]
        aggregate(assessment)


@celery_app.task(name="transcripto.assess")
def evaluate_assessment(identity, source):
    run_evaluation(identity, source)


def recover_assessments(engine=None):
    engine = engine if engine is not None else database_engine()
    with Session(engine) as db, db.begin():
        expired = db.scalars(select(AssessmentCriterion).where(AssessmentCriterion.status == "PROCESSING", AssessmentCriterion.lease_until < now())).all()
        for c in expired:
            c.status, c.error, c.run_token = "FAILED", "Evaluation interrupted; retry assessment.", None
        db.flush()
        for identity in {c.assessment_id for c in expired}:
            aggregate(db.get(SpeakingAssessment, identity))
        queued = db.execute(select(AssessmentCriterion.assessment_id, AssessmentCriterion.source).where(AssessmentCriterion.status == "QUEUED").distinct().limit(100)).all()
    for identity, source in queued:
        dispatch(identity, (source,))


router = APIRouter(prefix="/api/v1/speaking-assessments", tags=["speaking assessments"])


@router.get("", response_model=list[AssessmentResponse])
def list_assessments(db: DB, limit: int = 100):
    if not 1 <= limit <= 200:
        raise HTTPException(422, "Limit must be between 1 and 200")
    return db.scalars(
        select(SpeakingAssessment)
        .options(
            selectinload(SpeakingAssessment.criteria).selectinload(AssessmentCriterion.feedback),
            selectinload(SpeakingAssessment.criteria).selectinload(AssessmentCriterion.recommendations),
        )
        .order_by(SpeakingAssessment.created_at.desc(), SpeakingAssessment.id.desc())
        .limit(limit)
    ).all()


@router.post("", response_model=AssessmentResponse, status_code=202)
def create_assessment(body: AssessmentCreate, db: DB):
    if body.grading_rule_version_id:
        from app.grading_models import GradingRuleVersion
        if not db.get(GradingRuleVersion, body.grading_rule_version_id):
            raise HTTPException(404, "Grading rule version not found")
    job = db.get(TranscriptionJob, body.job_id)
    if not job:
        raise HTTPException(404, "Recording not found")
    if job.status != "COMPLETED" or not job.segments:
        raise HTTPException(409, "A completed nonempty transcript is required")
    if job.language == "vi":
        raise HTTPException(422, "English speaking recordings are required")
    assessment = SpeakingAssessment(job_id=job.id, part=body.part, question=body.question, grading_rule_version_id=body.grading_rule_version_id,
        transcript_snapshot=[{"id": str(s.id), "start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text} for s in job.segments],
        criteria=[AssessmentCriterion(code=code, source=source) for source, codes in [("TEXT",TEXT_CODES),("AUDIO",AUDIO_CODES)] for code in sorted(codes)])
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    dispatch(assessment.id)
    return assessment


@router.get("/{identity}", response_model=AssessmentResponse)
def get_assessment(identity: UUID, db: DB):
    assessment = db.get(SpeakingAssessment, identity)
    if not assessment:
        raise HTTPException(404, "Assessment not found")
    return assessment


@router.post("/{identity}/retry", response_model=AssessmentResponse, status_code=202)
def retry_assessment(identity: UUID, db: DB):
    assessment = db.scalars(select(SpeakingAssessment).where(SpeakingAssessment.id == identity).with_for_update()).first()
    if not assessment:
        raise HTTPException(404, "Assessment not found")
    if assessment.status in {"QUEUED", "PROCESSING"}:
        raise HTTPException(409, "Assessment is still active")
    sources = {c.source for c in assessment.criteria if c.status in {"FAILED", "UNAVAILABLE", "NOT_ASSESSED"}}
    if not sources:
        raise HTTPException(409, "No failed evaluation to retry")
    for criterion in assessment.criteria:
        if criterion.source in sources:
            criterion.status, criterion.error = "QUEUED", None
    aggregate(assessment)
    db.commit()
    db.refresh(assessment)
    dispatch(identity, sources)
    return assessment
