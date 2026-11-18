# Transcripto AI

Single-user local audio/video transcription with Next.js, FastAPI, PostgreSQL, Celery/Redis, FFmpeg and faster-whisper. Original speech is transcribed, never translated. MP3, WAV, M4A, MP4, MOV and WEBM uploads are supported; browser playback depends on its codec support.

## Start (PowerShell, project root)

Install Docker Desktop with the Linux engine enabled. Allow at least 4 GiB Docker memory and disk space for images, uploads and model weights. The worker defaults to CPU/int8 `tiny`, one process, two threads and a 2 GiB memory cap. Internet access is required for initial package and model downloads.

```powershell
Set-Location D:\MyProject\transcript-ai
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 api worker scheduler
```

Open [the application](http://localhost:3000), [API docs](http://localhost:8000/docs), or [API health](http://localhost:8000/health). API startup applies Alembic migrations; the worker waits for API health. Redis and PostgreSQL are internal to Compose. `NEXT_PUBLIC_API_URL` defaults to `http://localhost:8000`; frontend public configuration is compiled at build time, so rebuild after changing it.

Upload files at `/upload`, follow independent jobs at `/jobs`, then open a completed transcript. Click timestamps to seek, edit and save each segment, and download edited TXT/SRT/VTT/JSON. Unsaved drafts are retained in browser local storage; exports are disabled until saved. Failed jobs can be retried; cancel active jobs before deletion. Delete removes the original media and transcript permanently.

## Configuration and data

See `.env.example`. Set `POSTGRES_PASSWORD` and the matching password in `DATABASE_URL` together. `MAX_UPLOAD_BYTES` defaults to 100 MiB per file. Languages are `auto`, `en`, `vi`. Raising `WORKER_CONCURRENCY` creates additional model copies: adjust `WORKER_CPUS`, `WHISPER_CPU_THREADS` and `WORKER_MEMORY_LIMIT` together.

`storage/uploads/` is bind-mounted; PostgreSQL, Redis and model weights use named volumes. `docker compose stop` stops services while retaining data; `docker compose up -d` restarts them. Back up uploads and PostgreSQL together. Do not remove volumes unless deliberately discarding data.

Retries have a default three-attempt budget. A scheduler checks queued jobs every 30 seconds, including broker publish failures. Interrupted processing is recovered after `JOB_TIME_LIMIT + 60` seconds (default about 31 minutes); native inference is hard-limited by Celery. Cancellation prevents result writes but may wait for active inference to release resources. Keep worker/scheduler timeout settings consistent.

## Checks

Host backend checks require Python and `backend/requirements-dev.txt` (Docker uses Python 3.12). Install FFmpeg/FFprobe for host media checks.

```powershell
Set-Location backend
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
Set-Location ../frontend
npm ci
npx tsc --noEmit
npm run build
node --test tests/drafts.test.mjs
Set-Location ..
```

The draft regression test uses Node 22.18+ or newer with native TypeScript stripping. Unit tests mock inference and normally use SQLite; set `TEST_DATABASE_URL` to test queue transitions in isolated temporary PostgreSQL schemas. These checks do not establish browser E2E success.

## Optional real-media integration

Supply short MP3 and MP4 clips containing actual speech. With the stack running:

```powershell
python backend/tests/integration_mvp.py --mp3 C:/clips/speech.mp3 --mp4 C:/clips/speech.mp4
```

This live HTTP check uploads each type separately, submits a mixed batch with invalid media, waits for real inference, validates stored timestamps, reads original media ranges, edits text, checks TXT/SRT, retries the invalid file and deletes only its own test jobs. It does not automate browser playback or highlighting. Results are printed only after assertions succeed.

For a reproducible public speech sample, download [Whisper's JFK test clip](https://github.com/openai/whisper/blob/main/tests/jfk.flac) into `storage/uploads/integration-samples/jfk.flac`, then convert it inside the running worker:

```powershell
docker compose exec -T worker ffmpeg -y -i /app/storage/uploads/integration-samples/jfk.flac /app/storage/uploads/integration-samples/speech.mp3
docker compose exec -T worker ffmpeg -y -f lavfi -i color=c=black:s=320x240:r=10 -i /app/storage/uploads/integration-samples/jfk.flac -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest /app/storage/uploads/integration-samples/speech.mp4
python backend/tests/integration_mvp.py --mp3 storage/uploads/integration-samples/speech.mp3 --mp4 storage/uploads/integration-samples/speech.mp4
```

Check the current execution evidence and limitations in [docs/tasks.md](docs/tasks.md). Existing dependency audit findings, if present, require remediation before any nonlocal deployment; this MVP binds its web ports to localhost.

### Review status

The 2026-10-31 review executed the live HTTP journey with real JFK speech, PostgreSQL, Redis and Celery, and verified browser playback, editing and downloads. All 19 backend checks passed with real-audio and PostgreSQL options enabled. A full browser file-picker journey, Vietnamese accuracy and live worker-kill recovery remain unverified. PyAV is pinned to 18.1.0 because version 19 breaks faster-whisper 1.2.1 audio decoding. The existing frontend dependency audit still reports three vulnerabilities.

Docker's PyPI transfer was unusually slow in the review environment. The backend integration image used a temporary, ignored wheel cache downloaded through the host; standard Compose startup instructions above still apply, but a clean backend build may require substantial download time. Keep the first-run model download in mind when waiting for the first job.
