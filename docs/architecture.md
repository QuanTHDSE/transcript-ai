# Transcripto AI architecture (local MVP)

## Shape and boundaries

One monorepo: `frontend/` (Next.js, TypeScript, Tailwind), `backend/` (FastAPI API, SQLAlchemy models and Alembic migrations, Celery tasks), `storage/uploads/` (original media), and `docs/`. A single Docker Compose project runs frontend, API, one Celery worker, PostgreSQL, and Redis. API and worker share the local storage mount and backend code. PostgreSQL holds job metadata and editable transcript text; Redis is only the Celery broker. No authentication or user entity in this single-user local release. Bind web ports to localhost and keep PostgreSQL/Redis internal to Compose.

## Data model

- `jobs`: UUID primary key; original filename; stored filename/path (server-generated UUID, never a client path); media type; size bytes; status (`QUEUED`, `PROCESSING`, `COMPLETED`, `FAILED`, `CANCELED`); actual stage; language; attempt count; run token, lease expiry and retry time; optional error message; created, started, completed timestamps. One uploaded file creates one job. Index `(created_at, id)` for newest-first history.
- `segments`: UUID primary key; `job_id` foreign key with cascade delete; zero-based ordinal unique per job; `start_ms`, `end_ms` (nonnegative, end greater than start); editable `text`; optional language/confidence only if supplied reliably by the model. Index `(job_id, ordinal)`. Times and ordering are immutable in the initial editor; text is editable.

Database is authoritative for state and edited transcript content. Store only original media persistently; transcoded audio is worker temporary data. Exports are rendered from current segments on request, not cached files.

## Essential API (`/api`)

| Method/path | Purpose |
| --- | --- |
| `POST /jobs` | Multipart `files` array; validate each file, persist it, create and enqueue one independent job per accepted file; return per-file job IDs or validation errors. |
| `GET /jobs?limit=&offset=` | Newest-first history with status, filename, timestamps, and error summary. |
| `GET /jobs/{id}` | Job detail and ordered segments when available. |
| `PATCH /jobs/{id}/segments/{segment_id}` | Update segment text; reject blank text and segments belonging to another job. |
| `GET /jobs/{id}/export?format=txt|srt|vtt|json` | Download a completed job's current transcript; return a conflict while unfinished. |

Use consistent JSON errors and validate upload size, extensions, and actual media decodability. Supported inputs: MP3, WAV, M4A, MP4, MOV, WEBM. Define a configurable per-file size limit and expose it in the UI. Do not expose stored paths in API responses. The frontend supports multi-select upload, polling history/detail while jobs are active, segment text editing, and export controls.

## Processing flow

1. API streams each upload to a unique local file; after successful persistence, inserts a `QUEUED` job and publishes its Celery task. PostgreSQL is the durable queue intent: if publishing fails, the periodic recovery task republishes queued rows. Each file succeeds or fails independently.
2. Worker claims the job, sets `processing` and `started_at`, then uses FFmpeg to decode to temporary mono 16 kHz PCM audio. Reject corrupt or unsupported media with a useful job error.
3. Worker runs faster-whisper, converts segment times to integer milliseconds, and writes all segments plus `completed` state in one database transaction. Delete temporary audio in all outcomes.
4. On exception, record `FAILED` and a safe error summary, or schedule a bounded retry for transient failures. One worker process initially bounds model memory; load the model once per process. Atomic database claims reject duplicate messages, and attempt tokens prevent stale results from committing. Celery hard time limits bound processing; a periodic task recovers expired leases. Cancellation fences writes and stops work at a safe stage boundary. A Celery Beat process uses the same Redis and PostgreSQL infrastructure.
5. Exporters read ordered segments from PostgreSQL. TXT joins text; SRT/VTT format cue times; JSON includes segment times and text. Edits appear in every subsequent export.

## Decisions and risks

### Speaking assessment extension

Assessment creation targets a completed English recording and captures immutable segment text/ID/time snapshots plus optional IELTS part/question. `SpeakingAssessment` owns independent status; `AssessmentCriterion` owns source-specific status, optional practice estimate and provider trace; findings and practice recommendations are separate child tables. Findings retain snapshot segment IDs rather than relying on mutable transcript content.

`POST /api/v1/speaking-assessments`, `GET /api/v1/speaking-assessments/{id}` and `POST /api/v1/speaking-assessments/{id}/retry` are the versioned foundation APIs. TEXT and AUDIO evaluators run separately through existing Celery/Redis; retry preserves successful source results. Default providers are unavailable, not simulated. Server-configured module factories implement the typed contracts in `app/assessments.py` and return validated schemas from `app/assessment_schemas.py`. Text evaluators cannot return pronunciation results. All estimates are unofficial practice feedback, and transcript-only fluency observations exclude acoustic delivery.

- Local bind-mounted storage makes API/worker file access simple; disk capacity, backups, and orphan cleanup need operational attention. Never trust user filenames for filesystem paths.
- CPU transcription can be slow and model downloads/memory can be large. Pin the model/configuration, document first-run download and resource needs, and keep concurrency at one until measured.
- Uploads can exceed proxy/body limits or exhaust disk. Stream uploads, enforce size limits, check available space, and report per-file failures without losing other submissions.
- A database commit and broker publish are not atomic. Handle publish failure explicitly and provide a recovery command or periodic reconciliation for queued jobs without a task.
- FFmpeg input is untrusted. Run containers with limited privileges, enforce upload limits and processing timeouts, and do not interpolate filenames into shell commands.
