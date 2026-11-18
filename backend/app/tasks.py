"""Database-owned retry schedule; broker messages are wakeups, not job state."""
import logging
import os
import errno
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import create_engine, delete, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.models import TranscriptSegment, TranscriptionJob as Job
from app.transcription import TransientError, transcribe_media

logger = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc)


def max_attempts():
    return max(1, int(os.getenv("JOB_MAX_ATTEMPTS", "3")))


@lru_cache
def database_engine():
    return create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


class Canceled(Exception):
    pass


def is_transient(exc):
    """Recognize transport failures, including wrapped model-download errors."""
    for _ in range(8):
        if isinstance(exc, (TransientError, ConnectionError, TimeoutError, OperationalError, SoftTimeLimitExceeded)):
            return True
        if isinstance(exc, OSError) and exc.errno in {errno.EAGAIN, errno.ECONNRESET, errno.ECONNREFUSED, errno.ETIMEDOUT, errno.ENETUNREACH}:
            return True
        response = getattr(exc, "response", None)
        if getattr(response, "status_code", None) in {408, 429, 500, 502, 503, 504}:
            return True
        exc = exc.__cause__ or exc.__context__
        if exc is None:
            break
    return False


def publish(job_id):
    try:
        transcribe_job.apply_async(args=[str(job_id)], retry=False, expires=90)
        return True
    except Exception:
        logger.exception("Broker unavailable for %s; recovery will republish", job_id)
        return False


def process_job(job_id: str, engine=None):
    engine = engine if engine is not None else database_engine()
    identity, token = UUID(job_id), str(uuid4())
    with Session(engine) as session, session.begin():
        claimed = session.execute(update(Job).where(
            Job.id == identity, Job.status == "QUEUED", Job.attempts < max_attempts(),
            or_(Job.next_run_at.is_(None), Job.next_run_at <= now()),
        ).values(status="PROCESSING", stage="validating", run_token=token,
                 attempts=Job.attempts + 1, started_at=now(), error_message=None,
                 lease_until=now() + timedelta(seconds=max(60, int(os.getenv("JOB_TIME_LIMIT", "1800"))) + 60)))
        if not claimed.rowcount:
            return
        job = session.get(Job, identity)
        filename, language, attempt = job.stored_filename, job.language, job.attempts

    def stage(value):
        with Session(engine) as session, session.begin():
            changed = session.execute(update(Job).where(Job.id == identity, Job.run_token == token,
                                                       Job.status == "PROCESSING").values(stage=value))
            if not changed.rowcount:
                raise Canceled()

    try:
        root = Path(os.getenv("UPLOAD_DIR", "storage/uploads")).resolve()
        source = (root / filename).resolve()
        if source.parent != root:
            raise ValueError("Invalid stored filename")
        segments = transcribe_media(source, language, on_stage=stage)
        with Session(engine) as session, session.begin():
            owned = session.execute(update(Job).where(Job.id == identity, Job.run_token == token,
                                                      Job.status == "PROCESSING")
                                    .values(status="COMPLETED", stage="done", completed_at=now(),
                                            run_token=None, lease_until=None, error_message=None))
            if not owned.rowcount:
                return
            session.execute(delete(TranscriptSegment).where(TranscriptSegment.job_id == identity))
            session.add_all([TranscriptSegment(job_id=identity, ordinal=i, start_ms=s.start_ms,
                                              end_ms=s.end_ms, text=s.text) for i, s in enumerate(segments)])
    except Canceled:
        return
    except Exception as exc:
        transient = is_transient(exc)
        retry = transient and attempt < max_attempts()
        logger.exception("Attempt %s failed for %s", attempt, identity)
        with Session(engine) as session, session.begin():
            session.execute(update(Job).where(Job.id == identity, Job.run_token == token, Job.status == "PROCESSING")
                            .values(status="QUEUED" if retry else "FAILED", stage="retry_wait" if retry else "failed",
                                    next_run_at=now() + timedelta(seconds=min(300, 10 * 2 ** (attempt - 1))) if retry else None,
                                    completed_at=None if retry else now(), run_token=None, lease_until=None,
                                    error_message="Temporary failure; retry scheduled." if retry else "Transcription failed; check worker logs."))


def recover_jobs(engine=None):
    engine = engine if engine is not None else database_engine()
    with Session(engine) as session, session.begin():
        expired = session.scalars(select(Job).where(Job.status == "PROCESSING", Job.lease_until < now()).with_for_update(skip_locked=True)).all()
        for job in expired:
            job.status = "QUEUED" if job.attempts < max_attempts() else "FAILED"
            job.stage = "waiting" if job.status == "QUEUED" else "failed"
            job.error_message = "Worker interrupted; retry scheduled." if job.status == "QUEUED" else "Worker interruption retry limit reached."
            job.run_token = None
            job.lease_until = None
            job.next_run_at = now()
            job.completed_at = now() if job.status == "FAILED" else None
    with Session(engine) as session:
        ids = session.scalars(select(Job.id).where(Job.status == "QUEUED", Job.attempts < max_attempts(),
                             or_(Job.next_run_at.is_(None), Job.next_run_at <= now())).order_by(Job.created_at).limit(100)).all()
    for identity in ids:
        publish(identity)


@celery_app.task(name="transcripto.transcribe")
def transcribe_job(job_id: str):
    process_job(job_id)


@celery_app.task(name="transcripto.recover")
def recover_queue():
    recover_jobs()
    from app.assessments import recover_assessments
    recover_assessments()
    from app.mapping import recover_mappings
    recover_mappings()
