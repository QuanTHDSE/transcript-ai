# Codex Agent Instructions

Project: Transcripto AI

Tech stack:

- Frontend: Next.js, TypeScript, Tailwind CSS
- Backend: FastAPI, Python
- Database: PostgreSQL
- Queue: Celery + Redis
- Transcription: faster-whisper
- Media: FFmpeg

Token optimization rules:

1. Inspect only files relevant to the current task.
2. Do not read the entire repository unless required.
3. Make minimal, targeted code changes.
4. Do not rewrite working features.
5. Reuse existing utilities and components.
6. Avoid unnecessary dependencies.
7. Do not generate long explanations.
8. Do not repeat complete source files in responses.
9. Run targeted tests rather than the entire test suite for every minor change.
10. Do not generate unrelated features.
11. Do not perform repeated speculative debugging.
12. If blocked, report the exact problem and evidence.
13. Keep the final response under 120 words.
14. Report only changed files, test results, and remaining issues.
15. Do not claim a feature works without verifying it.

Implement the requested task fully, not just a plan.

Preserve existing project architecture and conventions.

Prioritize correct, maintainable, working code over lengthy reasoning or documentation.
