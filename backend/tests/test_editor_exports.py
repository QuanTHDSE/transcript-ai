import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.exports import render, timestamp
from app.main import app
from app.models import Base, TranscriptSegment, TranscriptionJob
from app.routes import session_dependency


class EditorExportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.directory = TemporaryDirectory()
        self.db_file = Path(self.directory.name) / "source.wav"
        self.db_file.write_bytes(b"media")
        self.job_id = uuid4()
        with Session(self.engine) as session:
            job = TranscriptionJob(id=self.job_id, original_filename="source.wav", stored_filename="source.wav", media_type="audio/wav", size_bytes=5, status="COMPLETED")
            job.segments = [TranscriptSegment(id=uuid4(), ordinal=0, start_ms=1_234, end_ms=2_500, text="Original speech")]
            session.add(job)
            session.commit()
            self.segment_id = str(job.segments[0].id)
        def database():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[session_dependency] = database
        self.env = patch.dict("os.environ", {"UPLOAD_DIR": self.directory.name})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.env.stop()
        self.directory.cleanup()
        self.engine.dispose()

    def test_saved_edit_is_returned_by_all_downloads(self):
        detail = self.client.get(f"/api/jobs/{self.job_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["segments"][0]["start_ms"], 1234)
        updated = self.client.patch(f"/api/jobs/{self.job_id}/segments/{self.segment_id}", json={"text": "Edited speech"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["text"], "Edited speech")
        for format in ("txt", "srt", "vtt", "json"):
            response = self.client.get(f"/api/jobs/{self.job_id}/export?format={format}")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Edited speech", response.text)
            self.assertNotIn("Original speech", response.text)
            self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("00:00:01,234 --> 00:00:02,500", self.client.get(f"/api/jobs/{self.job_id}/export?format=srt").text)
        self.assertIn("00:00:01.234 --> 00:00:02.500", self.client.get(f"/api/jobs/{self.job_id}/export?format=vtt").text)

    def test_edit_validation_readiness_and_media(self):
        response = self.client.patch(f"/api/jobs/{self.job_id}/segments/{self.segment_id}", json={"text": "  "})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get(f"/api/jobs/{self.job_id}/export?format=exe").status_code, 422)
        media = self.client.get(f"/api/jobs/{self.job_id}/media")
        self.assertEqual(media.content, b"media")
        self.assertIn("audio/wav", media.headers["content-type"])
        partial = self.client.get(f"/api/jobs/{self.job_id}/media", headers={"Range": "bytes=1-3"})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, b"edi")

    def test_subtitle_timestamp_validation_and_long_duration(self):
        self.assertEqual(timestamp(3_661_234), "01:01:01,234")
        self.assertEqual(timestamp(360_000_000), "100:00:00,000")
        for value in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                timestamp(value)
        with self.assertRaises(ValueError):
            render([TranscriptSegment(ordinal=0, start_ms=500, end_ms=500, text="bad")], "srt")
        srt, _ = render([TranscriptSegment(ordinal=0, start_ms=0, end_ms=1, text="ok")], "srt")
        self.assertEqual(srt, "1\n00:00:00,000 --> 00:00:00,001\nok\n")

    def test_delete_removes_transcript_and_media_but_rejects_active_jobs(self):
        with Session(self.engine) as session:
            session.get(TranscriptionJob, self.job_id).status = "PROCESSING"
            session.commit()
        self.assertEqual(self.client.delete(f"/api/jobs/{self.job_id}").status_code, 409)
        self.assertTrue(self.db_file.exists())
        with Session(self.engine) as session:
            session.get(TranscriptionJob, self.job_id).status = "COMPLETED"
            session.commit()
        self.assertEqual(self.client.delete(f"/api/jobs/{self.job_id}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/jobs/{self.job_id}").status_code, 404)
        self.assertFalse(self.db_file.exists())
        with Session(self.engine) as session:
            from uuid import UUID
            self.assertIsNone(session.get(TranscriptSegment, UUID(self.segment_id)))

    def test_detail_orders_segments_by_ordinal(self):
        with Session(self.engine) as session:
            first = session.get(TranscriptionJob, self.job_id).segments[0]
            first.ordinal = 2
            session.add(TranscriptSegment(job_id=self.job_id, ordinal=0, start_ms=0, end_ms=500, text="First"))
            session.commit()
        response = self.client.get(f"/api/jobs/{self.job_id}")
        self.assertEqual([s["ordinal"] for s in response.json()["segments"]], [0, 2])


if __name__ == "__main__":
    unittest.main()
