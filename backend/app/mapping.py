"""Explicit template-to-recording mapping; suggestions never confirm a match."""
import json
import os
import re
import subprocess
from pathlib import Path
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.routes import DB
from app.models import TranscriptionJob
from app.template_models import TemplateVersion
from app.grading_models import GradingPreset, GradingRuleVersion
from app.grading_rules import Rules
from app.mapping_models import MappingBatch, RecordingMapping
from app.celery_app import celery_app
from app.tasks import database_engine

router = APIRouter(prefix="/api/v1/mapping-batches", tags=["recording mapping"])

def questions(parsed):
    result, part = [], None
    def visit(blocks):
        nonlocal part
        for block in blocks:
            if block.get("type") == "table":
                for row in block.get("rows", []):
                    for cell in row: visit(cell)
            elif block.get("type") == "paragraph":
                text = block.get("text", "")
                match = re.search(r"\b(?:part|phần)\s*(\d+)\b", text, re.I)
                if match: part = int(match.group(1))
                if block.get("question") and block.get("id"):
                    result.append({"id": block["id"], "part": part, "text": text})
    visit(parsed.get("blocks", []))
    return result

def suggest(filename, transcript, items):
    hints = re.findall(r"\b(?:part|p|phần)[\s_.-]*(\d+)\b", filename, re.I)
    hinted = int(hints[0]) if len(set(hints)) == 1 else None
    words = set(re.findall(r"[a-z]{4,}", transcript.lower()))
    ranked = []
    for item in items:
        terms = set(re.findall(r"[a-z]{4,}", item["text"].lower()))
        overlap = len(words & terms)
        if (hinted is not None and hinted == item["part"]) or overlap >= 2:
            ranked.append({"question_id": item["id"], "part": item["part"], "reason": "Filename part hint" if hinted == item["part"] else "Transcript word overlap", "overlap": overlap})
    return sorted(ranked, key=lambda x: x["overlap"], reverse=True)

def duration_metrics(media_duration_ms, intervals, pause_ms=1500):
    if media_duration_ms <= 0: raise ValueError("Invalid media duration")
    if not intervals:
        return {"media_duration_ms": media_duration_ms, "speech_start_ms": None, "speech_end_ms": None, "task_elapsed_ms": None, "active_voiced_ms": 0, "long_pauses": [], "boundary_status": "UNCERTAIN", "note": "No speech detected; duration cannot be assessed."}
    if any(not 0 <= start < end <= media_duration_ms for start, end in intervals): raise ValueError("Invalid speech intervals")
    gaps = [{"start_ms": a[1], "end_ms": b[0]} for a,b in zip(intervals, intervals[1:]) if b[0]-a[1] >= pause_ms]
    return {"media_duration_ms": media_duration_ms, "speech_start_ms": intervals[0][0], "speech_end_ms": intervals[-1][1], "task_elapsed_ms": intervals[-1][1]-intervals[0][0], "active_voiced_ms": sum(end-start for start,end in intervals), "long_pauses": gaps, "boundary_status": "UNCERTAIN", "note": "VAD estimates boundaries; background voices, noise and clipped speech may affect them. Review audio."}

class BatchInput(BaseModel):
    template_version_id: UUID
    rule_version_id: UUID
    duration_metric: Literal["media", "task_elapsed", "active_voiced"] = "task_elapsed"
class AddRecording(BaseModel):
    job_id: UUID
class ConfirmMapping(BaseModel):
    student_id: str = Field(min_length=1, max_length=255)
    part: int = Field(ge=1, le=20)
    question_id: str = Field(min_length=1, max_length=500)
    confirmed: Literal[True]

def payload(batch, db):
    template = db.get(TemplateVersion, batch.template_version_id)
    items = questions(template.parsed)
    rows = db.scalars(select(RecordingMapping).where(RecordingMapping.batch_id == batch.id)).all()
    recordings = []
    for row in rows:
        job = db.get(TranscriptionJob, row.job_id)
        transcript = " ".join(s.text for s in job.segments) if job.status == "COMPLETED" else ""
        selected = next((q for q in items if q["id"] == row.question_id), None)
        ready = bool(row.status == "CONFIRMED" and selected and job.status == "COMPLETED" and job.segments and row.analysis_status == "COMPLETED")
        aligned = {"question": selected, "segments": [{"id": str(s.id), "start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text} for s in job.segments]} if ready else None
        recordings.append({"id": row.id, "recording_id": row.job_id, "transcript_id": row.job_id if job.status == "COMPLETED" else None, "filename": job.original_filename, "transcription_status": job.status, "student_id": row.student_id, "part": row.part, "question_id": row.question_id, "status": row.status, "analysis_status": row.analysis_status, "assessment_ready": ready, "aligned": aligned, "duration": row.duration, "error": row.error, "suggestions": suggest(job.original_filename, transcript, items)})
    students = sorted({r.student_id for r in rows if r.student_id})
    missing = [{"student_id": student, "question_id": item["id"], "part": item["part"]} for student in students for item in items if not any(r.student_id == student and r.question_id == item["id"] and r.status == "CONFIRMED" for r in rows)]
    return {"id": batch.id, "template_id": template.template_id, "template_version_id": batch.template_version_id, "rule_version_id": batch.rule_version_id, "duration_metric": batch.duration_metric, "questions": items, "recordings": recordings, "missing": missing}

@router.post("", status_code=201)
def create_batch(body: BatchInput, db: DB):
    template = db.get(TemplateVersion, body.template_version_id)
    version = db.get(GradingRuleVersion, body.rule_version_id)
    preset = db.get(GradingPreset, version.preset_id) if version else None
    if not template or not version: raise HTTPException(404, "Template or rule version not found")
    if not preset or preset.template_version_id != template.id: raise HTTPException(422, "Rule preset belongs to a different template version")
    batch = MappingBatch(template_version_id=template.id, rule_version_id=version.id, duration_metric=body.duration_metric)
    db.add(batch); db.commit(); db.refresh(batch)
    return payload(batch, db)

@router.get("")
def list_batches(db: DB):
    return [payload(batch, db) for batch in db.scalars(select(MappingBatch).order_by(MappingBatch.created_at.desc()).limit(100)).all()]

@router.get("/{batch_id}")
def get_batch(batch_id: UUID, db: DB):
    batch = db.get(MappingBatch, batch_id)
    if not batch: raise HTTPException(404, "Batch not found")
    return payload(batch, db)

@router.post("/{batch_id}/recordings", status_code=201)
def add_recording(batch_id: UUID, body: AddRecording, db: DB):
    batch, job = db.get(MappingBatch, batch_id), db.get(TranscriptionJob, body.job_id)
    if not batch or not job: raise HTTPException(404, "Batch or recording not found")
    if not job.original_filename.lower().endswith((".mp3", ".wav", ".m4a", ".mp4", ".webm")): raise HTTPException(422, "Unsupported recording format")
    if db.scalar(select(RecordingMapping.id).where(RecordingMapping.batch_id == batch_id, RecordingMapping.job_id == body.job_id)): raise HTTPException(409, "Recording already in batch")
    db.add(RecordingMapping(batch_id=batch_id, job_id=body.job_id, status="UNMAPPED", analysis_status="NOT_STARTED")); db.commit()
    return payload(batch, db)

@router.patch("/{batch_id}/recordings/{mapping_id}")
def confirm(batch_id: UUID, mapping_id: UUID, body: ConfirmMapping, db: DB):
    batch = db.get(MappingBatch, batch_id)
    row = db.scalars(select(RecordingMapping).where(RecordingMapping.id == mapping_id).with_for_update()).first()
    if not batch or not row or row.batch_id != batch_id: raise HTTPException(404, "Mapping not found")
    if row.analysis_status in {"QUEUED", "PROCESSING"}: raise HTTPException(409, "Analysis in progress")
    item = next((q for q in questions(db.get(TemplateVersion, batch.template_version_id).parsed) if q["id"] == body.question_id), None)
    if not body.student_id.strip() or not item or (item["part"] is not None and item["part"] != body.part): raise HTTPException(422, "Question and part must belong to the selected template version")
    row.student_id, row.part, row.question_id, row.status = body.student_id.strip(), body.part, body.question_id, "CONFIRMED"
    row.analysis_status, row.duration, row.error = "NOT_STARTED", None, None
    db.commit()
    return payload(batch, db)

@router.post("/{batch_id}/recordings/{mapping_id}/prepare", status_code=202)
def prepare(batch_id: UUID, mapping_id: UUID, db: DB):
    row = db.scalars(select(RecordingMapping).where(RecordingMapping.id == mapping_id, RecordingMapping.batch_id == batch_id).with_for_update()).first()
    if not row: raise HTTPException(404, "Mapping not found")
    job = db.get(TranscriptionJob, row.job_id)
    if row.status != "CONFIRMED" or job.status != "COMPLETED" or not job.segments: raise HTTPException(409, "Confirm mapping and wait for a completed nonempty transcript")
    if row.analysis_status in {"QUEUED", "PROCESSING"}: raise HTTPException(409, "Analysis already queued")
    row.analysis_status, row.error = "QUEUED", None
    db.commit()
    try: analyze_mapping.apply_async(args=[str(row.id)], retry=False)
    except Exception:
        row.analysis_status, row.error = "FAILED", "Unable to queue audio analysis; retry."
        db.commit()
        raise HTTPException(503, row.error)
    return {"status": "QUEUED"}

def recover_mappings(engine=None):
    engine = engine if engine is not None else database_engine()
    with Session(engine) as db, db.begin():
        rows = db.scalars(select(RecordingMapping).join(TranscriptionJob, RecordingMapping.job_id == TranscriptionJob.id).where(RecordingMapping.status == "CONFIRMED", RecordingMapping.analysis_status == "NOT_STARTED", TranscriptionJob.status == "COMPLETED").with_for_update(skip_locked=True).limit(100)).all()
        identities = []
        for row in rows:
            job = db.get(TranscriptionJob, row.job_id)
            if job.segments:
                row.analysis_status = "QUEUED"
                identities.append(row.id)
    for identity in identities:
        try:
            analyze_mapping.apply_async(args=[str(identity)], retry=False)
        except Exception:
            with Session(engine) as db, db.begin():
                row = db.get(RecordingMapping, identity)
                if row and row.analysis_status == "QUEUED":
                    row.analysis_status, row.error = "NOT_STARTED", "Queue unavailable; will retry."

@celery_app.task(name="transcripto.mapping_audio")
def analyze_mapping(identity):
    from app.speaking_audio import read_audio, silero_intervals, normalize_intervals
    engine = database_engine()
    with Session(engine) as db, db.begin():
        row = db.scalars(select(RecordingMapping).where(RecordingMapping.id == UUID(identity)).with_for_update()).first()
        if not row or row.analysis_status != "QUEUED" or row.status != "CONFIRMED": return
        row.analysis_status = "PROCESSING"
        job = db.get(TranscriptionJob, row.job_id)
        batch = db.get(MappingBatch, row.batch_id)
        filename, metric, part, rule_id = job.stored_filename, batch.duration_metric, row.part, batch.rule_version_id
    try:
        root = Path(os.getenv("UPLOAD_DIR", "storage/uploads")).resolve()
        media = (root / filename).resolve(strict=True)
        if media.parent != root: raise ValueError("Invalid media path")
        probe = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries", "format=duration", "-of", "json", str(media)], check=True, capture_output=True, text=True, timeout=60)
        media_ms = round(float(json.loads(probe.stdout)["format"]["duration"]) * 1000)
        samples = read_audio(media)
        intervals = normalize_intervals(silero_intervals(samples, 16000), len(samples))
        # Codec padding may extend decoded PCM slightly past the container duration.
        intervals = [(a, min(b, media_ms)) for a,b in intervals if a < min(b, media_ms)]
        result = duration_metrics(media_ms, intervals, int(os.getenv("SPEAKING_LONG_PAUSE_MS", "1500")))
        with Session(engine) as db:
            rules = Rules.model_validate(db.get(GradingRuleVersion, rule_id).rules)
        rule = next((r for r in rules.duration_limits if r.part == part), None)
        value = result[{"media":"media_duration_ms", "task_elapsed":"task_elapsed_ms", "active_voiced":"active_voiced_ms"}[metric]]
        result["grading_metric"] = metric
        result["duration_check"] = "NOT_ASSESSED" if rule is None or value is None or (metric != "media" and result["speech_start_ms"] is None) else "PASSED" if rule.min_seconds <= value / 1000 <= rule.max_seconds else "NOT PASSED"
        result["duration_check_certainty"] = "UNCERTAIN" if metric != "media" else "MEASURED"
        error = None
    except Exception:
        result, error = None, "Audio analysis failed; check media and worker logs."
    with Session(engine) as db, db.begin():
        row = db.scalars(select(RecordingMapping).where(RecordingMapping.id == UUID(identity)).with_for_update()).first()
        if row and row.analysis_status == "PROCESSING":
            row.duration, row.error, row.analysis_status = result, error, "FAILED" if error else "COMPLETED"
