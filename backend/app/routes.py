import os
import mimetypes
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import Session

from app.models import TranscriptionJob as Job
from app.models import TranscriptSegment
from app.exports import render
from app.tasks import database_engine, now, publish

router = APIRouter(prefix="/api")


def session_dependency():
    with Session(database_engine()) as session:
        yield session


DB = Annotated[Session, Depends(session_dependency)]


class SegmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ordinal: int
    start_ms: int
    end_ms: int
    text: str


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    original_filename: str
    status: str
    stage: str
    attempts: int
    language: str
    error_message: str | None
    media_type: str
    segments: list[SegmentResponse] = Field(default_factory=list)


class SegmentEdit(BaseModel):
    text: str


class UploadJobResponse(BaseModel):
    filename: str
    job_id: UUID | None = None
    error: str | None = None


@router.post("/jobs")
@router.post("/v1/transcriptions/upload")
def upload(db: DB, files: list[UploadFile] = File(...), language: str = Form("auto")):
    if language not in {"auto", "en", "vi"}:
        raise HTTPException(422, "Language must be auto, en, or vi")
    root = Path(os.getenv("UPLOAD_DIR", "storage/uploads")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    limit = int(os.getenv("MAX_UPLOAD_BYTES", "104857600"))
    results = []
    for file in files:
        path = None
        name = (file.filename or "file").replace("\\", "/").split("/")[-1][:255]
        try:
            suffix = Path(name).suffix.lower()
            if suffix not in {".mp3", ".wav", ".m4a", ".mp4", ".mov", ".webm"}:
                raise ValueError("Unsupported media extension")
            identity = uuid4()
            path = root / f"{identity}{suffix}"
            size = 0
            with path.open("xb") as output:
                while chunk := file.file.read(64 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("File exceeds configured upload limit")
                    output.write(chunk)
            if not size:
                raise ValueError("Empty file")
            # Actual media validation occurs in the asynchronous validating stage.
            job = Job(id=identity, original_filename=name, stored_filename=path.name,
                      size_bytes=size, media_type=mimetypes.guess_type(name)[0] or "application/octet-stream", language=language)
            db.add(job)
            db.commit()
            publish(identity)
            results.append({"filename": name, "job_id": str(identity)})
        except Exception as exc:
            db.rollback()
            if path:
                path.unlink(missing_ok=True)
            results.append({"filename": name, "error": str(exc) if isinstance(exc, ValueError) else "Upload could not be saved"})
        finally:
            file.file.close()
    return results


@router.get("/jobs", response_model=list[JobResponse])
def list_jobs(db: DB, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    return db.scalars(select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(limit).offset(offset)).all()


@router.get("/jobs/{job_id}", response_model=JobResponse)
def detail(job_id: UUID, db: DB):
    job = db.scalars(select(Job).where(Job.id == job_id).options(selectinload(Job.segments))).first()
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/jobs/{job_id}/media")
def media(job_id: UUID, db: DB):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    root = Path(os.getenv("UPLOAD_DIR", "storage/uploads")).resolve()
    path = (root / job.stored_filename).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "Media file not found")
    return FileResponse(path, media_type=job.media_type)


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: UUID, db: DB):
    job = db.scalars(select(Job).where(Job.id == job_id).with_for_update()).first()
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status in {"QUEUED", "PROCESSING"}:
        raise HTTPException(409, "Cancel the job before deleting it")
    root = Path(os.getenv("UPLOAD_DIR", "storage/uploads")).resolve()
    path = (root / job.stored_filename).resolve()
    if path.parent != root:
        raise HTTPException(409, "Invalid stored media path")
    staged = root / f"{uuid4()}.deleted"
    moved = False
    try:
        if path.exists():
            path.rename(staged)
            moved = True
        from app.assessment_models import SpeakingTextCache
        db.execute(delete(SpeakingTextCache).where(SpeakingTextCache.job_id == job_id))
        db.execute(delete(TranscriptSegment).where(TranscriptSegment.job_id == job_id))
        db.delete(job)
        db.commit()
    except Exception:
        db.rollback()
        if moved:
            staged.rename(path)
        raise
    if moved:
        staged.unlink(missing_ok=True)
    return Response(status_code=204)


@router.patch("/jobs/{job_id}/segments/{segment_id}", response_model=SegmentResponse)
def update_segment(job_id: UUID, segment_id: UUID, edit: SegmentEdit, db: DB):
    text = edit.text.strip()
    if not text or len(text) > 100_000:
        raise HTTPException(422, "Transcript text must contain 1 to 100000 characters")
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != "COMPLETED":
        raise HTTPException(409, "Only completed transcripts can be edited")
    segment = db.get(TranscriptSegment, segment_id)
    if not segment or segment.job_id != job_id:
        raise HTTPException(404, "Transcript segment not found")
    segment.text = text
    db.commit()
    db.refresh(segment)
    return segment


@router.get("/jobs/{job_id}/export")
def export_transcript(job_id: UUID, db: DB, format: str = Query(...)):
    if format not in {"txt", "srt", "vtt", "json"}:
        raise HTTPException(422, "Format must be txt, srt, vtt, or json")
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != "COMPLETED":
        raise HTTPException(409, "Transcript is not completed")
    segments = db.scalars(select(TranscriptSegment).where(TranscriptSegment.job_id == job_id).order_by(TranscriptSegment.ordinal)).all()
    try:
        content, content_type = render(segments, format)
    except ValueError as exc:
        raise HTTPException(500, "Transcript has invalid timestamps") from exc
    return Response(content, media_type=content_type, headers={"Content-Disposition": f'attachment; filename="transcript.{format}"'})


@router.post("/jobs/{job_id}/retry", response_model=JobResponse)
def retry_job(job_id: UUID, db: DB):
    changed = db.execute(update(Job).where(Job.id == job_id, Job.status == "FAILED")
                         .values(status="QUEUED", stage="waiting", attempts=0, next_run_at=None,
                                 run_token=None, lease_until=None, error_message=None, completed_at=None, started_at=None))
    db.commit()
    if not changed.rowcount:
        if not db.get(Job, job_id):
            raise HTTPException(404, "Job not found")
        raise HTTPException(409, "Only failed jobs can be retried")
    publish(job_id)
    return db.get(Job, job_id)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: UUID, db: DB):
    changed = db.execute(update(Job).where(Job.id == job_id, Job.status.in_(["QUEUED", "PROCESSING"]))
                         .values(status="CANCELED", stage="canceled", run_token=None, completed_at=now()))
    db.commit()
    if not changed.rowcount:
        if not db.get(Job, job_id):
            raise HTTPException(404, "Job not found")
        raise HTTPException(409, "Job is already finished")
    return db.get(Job, job_id)
