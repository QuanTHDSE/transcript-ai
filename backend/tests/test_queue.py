import os
import unittest
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.main import app
from app.models import Base, TranscriptSegment, TranscriptionJob as Job
from app.routes import session_dependency
from app.tasks import now, process_job, recover_jobs
from app.transcription import Segment, TransientError


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.schema = None
        if os.getenv("TEST_DATABASE_URL"):
            self.schema = "queue_test_" + uuid4().hex
            self.admin = create_engine(os.environ["TEST_DATABASE_URL"])
            with self.admin.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
            self.engine = create_engine(os.environ["TEST_DATABASE_URL"], connect_args={"options": f"-csearch_path={self.schema}"})
        else:
            self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        def database():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[session_dependency] = database
        self.client = TestClient(app)
        self.directory = TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"UPLOAD_DIR": self.directory.name, "JOB_MAX_ATTEMPTS": "3"})
        self.environment.start()
        self.publisher = patch("app.routes.publish", return_value=True)
        self.publish = self.publisher.start()

    def tearDown(self):
        app.dependency_overrides.clear()
        self.publisher.stop()
        self.environment.stop()
        self.directory.cleanup()
        self.engine.dispose()
        if self.schema:
            with self.admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            self.admin.dispose()

    def upload(self, names=("a.wav", "b.mp4")):
        response = self.client.post("/api/jobs", files=[("files", (name, b"test media")) for name in names])
        self.assertEqual(response.status_code, 200)
        return response.json()

    def jobs(self):
        return self.client.get("/api/jobs").json()

    def test_batch_failure_isolation_and_duplicate_delivery(self):
        batch = self.upload()
        self.assertEqual(self.publish.call_count, 2)
        with patch("app.tasks.transcribe_media", side_effect=ValueError("invalid")):
            with self.assertLogs("app.tasks", level="ERROR"):
                process_job(batch[0]["job_id"], engine=self.engine)
        with patch("app.tasks.transcribe_media", return_value=[Segment(0, 100, "Hello")]) as model:
            process_job(batch[1]["job_id"], engine=self.engine)
            process_job(batch[1]["job_id"], engine=self.engine)
            model.assert_called_once()
        self.assertEqual({j["status"] for j in self.jobs()}, {"FAILED", "COMPLETED"})
        with Session(self.engine) as session:
            self.assertEqual(len(session.scalars(select(TranscriptSegment)).all()), 1)

    def test_bounded_transient_retries_and_manual_retry(self):
        identity = self.upload(("a.wav",))[0]["job_id"]
        for attempt in range(1, 4):
            with patch("app.tasks.transcribe_media", side_effect=TransientError("offline")):
                with self.assertLogs("app.tasks", level="ERROR"):
                    process_job(identity, engine=self.engine)
            job = self.jobs()[0]
            self.assertEqual(job["attempts"], attempt)
            self.assertEqual(job["status"], "FAILED" if attempt == 3 else "QUEUED")
            with Session(self.engine) as session, session.begin():
                session.execute(update(Job).values(next_run_at=now() - timedelta(seconds=1)))
        self.assertEqual(self.client.post(f"/api/jobs/{identity}/retry").json()["status"], "QUEUED")
        self.assertEqual(self.client.post(f"/api/jobs/{identity}/retry").status_code, 409)

    def test_cancel_queued_and_running_fences_results(self):
        ids = [result["job_id"] for result in self.upload()]
        self.assertEqual(self.client.post(f"/api/jobs/{ids[0]}/cancel").json()["status"], "CANCELED")
        with patch("app.tasks.transcribe_media") as model:
            process_job(ids[0], engine=self.engine)
            model.assert_not_called()
        def inference(*args, **kwargs):
            self.assertEqual(self.client.get(f"/api/jobs/{ids[1]}").json()["status"], "PROCESSING")
            kwargs["on_stage"]("transcribing")
            self.assertEqual(self.client.get(f"/api/jobs/{ids[1]}").json()["stage"], "transcribing")
            self.client.post(f"/api/jobs/{ids[1]}/cancel")
            return [Segment(0, 100, "Discard me")]
        with patch("app.tasks.transcribe_media", side_effect=inference):
            process_job(ids[1], engine=self.engine)
        self.assertTrue(all(j["status"] == "CANCELED" for j in self.jobs()))
        with Session(self.engine) as session:
            self.assertEqual(session.scalars(select(TranscriptSegment)).all(), [])

    def test_interruption_recovery_and_exhaustion(self):
        self.upload()
        with Session(self.engine) as session, session.begin():
            jobs = session.scalars(select(Job)).all()
            for i, job in enumerate(jobs):
                job.status, job.attempts = "PROCESSING", 1 if i == 0 else 3
                job.run_token, job.lease_until = "old-token", now() - timedelta(seconds=1)
        with patch("app.tasks.publish") as publish:
            recover_jobs(self.engine)
            publish.assert_called_once()
        self.assertEqual({j["status"] for j in self.jobs()}, {"QUEUED", "FAILED"})

    def test_recovery_republishes_unpublished_jobs(self):
        self.upload(("a.wav",))
        with patch("app.tasks.publish") as publish:
            recover_jobs(self.engine)
            publish.assert_called_once()

    def test_upload_validation_and_cleanup(self):
        results = self.upload(("bad.exe", "../../safe.wav"))
        self.assertIn("error", results[0])
        self.assertIn("job_id", results[1])
        from pathlib import Path
        self.assertEqual(len(list(Path(self.directory.name).iterdir())), 1)
        with patch.dict(os.environ, {"MAX_UPLOAD_BYTES": "2"}):
            self.assertIn("error", self.upload(("too-big.wav",))[0])
        self.assertEqual(len(list(Path(self.directory.name).iterdir())), 1)

    def test_simultaneous_delivery_claims_only_once(self):
        identity = self.upload(("a.wav",))[0]["job_id"]
        entered, release = Event(), Event()
        def inference(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return []
        with patch("app.tasks.transcribe_media", side_effect=inference) as model:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(process_job, identity, self.engine)
                try:
                    self.assertTrue(entered.wait(5))
                    process_job(identity, engine=self.engine)
                finally:
                    release.set()
                future.result(timeout=5)
            self.assertEqual(model.call_count, 1)

    def test_not_due_retry_is_not_claimed(self):
        identity = self.upload(("a.wav",))[0]["job_id"]
        with Session(self.engine) as session, session.begin():
            session.execute(update(Job).values(next_run_at=now() + timedelta(minutes=1)))
        with patch("app.tasks.transcribe_media") as model:
            process_job(identity, engine=self.engine)
            model.assert_not_called()

    def test_publish_failure_preserves_durable_queue(self):
        self.publisher.stop()
        with patch("app.tasks.transcribe_job.apply_async", side_effect=ConnectionError("broker unavailable")):
            with self.assertLogs("app.tasks", level="ERROR"):
                result = self.upload(("a.wav",))[0]
        self.assertIn("job_id", result)
        self.assertEqual(self.jobs()[0]["status"], "QUEUED")
