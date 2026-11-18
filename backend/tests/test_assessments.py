import unittest
import os
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.main import app
from app.models import Base, TranscriptionJob, TranscriptSegment
from app.assessment_models import SpeakingAssessment
from app.assessments import run_evaluation, TEXT_CODES
from app.routes import session_dependency


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.schema = None
        if os.getenv("TEST_DATABASE_URL"):
            self.schema = "assessment_test_" + uuid4().hex
            self.admin = create_engine(os.environ["TEST_DATABASE_URL"])
            with self.admin.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
            self.engine = create_engine(os.environ["TEST_DATABASE_URL"], connect_args={"options":f"-csearch_path={self.schema}"})
        else:
            self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.job_id, self.segment_id = uuid4(), uuid4()
        with Session(self.engine) as db:
            db.add(TranscriptionJob(id=self.job_id, original_filename="speech.mp3", stored_filename="speech.mp3", media_type="audio/mpeg", size_bytes=1, status="COMPLETED", language="en",
                segments=[TranscriptSegment(id=self.segment_id, ordinal=0, start_ms=0, end_ms=1000, text="I is happy.")]))
            db.commit()
        def session():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[session_dependency] = session
        self.client = TestClient(app)
        self.publisher = patch("app.assessments.dispatch")
        self.dispatch = self.publisher.start()

    def tearDown(self):
        self.publisher.stop()
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()
        if self.schema:
            with self.admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            self.admin.dispose()

    def create(self):
        response = self.client.post("/api/v1/speaking-assessments", json={"job_id":str(self.job_id), "part":2, "question":"Describe a happy day."})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["id"]

    def test_text_results_preserve_original_and_audio_is_independent(self):
        identity = self.create()
        provider = Mock()
        provider.evaluate_text.return_value = [{"code":code, "feedback": [{"segment_id":str(self.segment_id), "start_ms":0,"end_ms":1000,"evidence":"I is", "suggested_correction":"I am", "explanation":"Subject agreement"}] if code == "grammar_accuracy" else []} for code in TEXT_CODES]
        run_evaluation(identity, "TEXT", self.engine, provider)
        run_evaluation(identity, "TEXT", self.engine, provider)
        provider.evaluate_text.assert_called_once()
        with patch("app.assessments.provider_for", return_value=None):
            run_evaluation(identity, "AUDIO", self.engine)
        result = self.client.get(f"/api/v1/speaking-assessments/{identity}")
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["status"],"PARTIAL")
        pronunciation = next(c for c in result.json()["criteria"] if c["code"]=="pronunciation")
        self.assertIsNone(pronunciation["score"])
        self.assertEqual(pronunciation["status"],"UNAVAILABLE")
        with Session(self.engine) as db:
            self.assertEqual(db.get(TranscriptSegment,self.segment_id).text,"I is happy.")
        self.assertEqual(self.client.post(f"/api/v1/speaking-assessments/{identity}/retry").status_code,202)
        self.assertEqual(self.dispatch.call_args.args[1],{"AUDIO"})

    def test_invalid_text_provider_cannot_score_pronunciation(self):
        identity = self.create()
        provider = Mock()
        provider.evaluate_text.return_value = [{"code":"pronunciation","score":9}]
        with self.assertLogs("app.assessments",level="ERROR"):
            run_evaluation(identity,"TEXT",self.engine,provider)
        result = self.client.get(f"/api/v1/speaking-assessments/{identity}").json()
        self.assertTrue(all(c["score"] is None for c in result["criteria"]))
        self.assertEqual([c["status"] for c in result["criteria"] if c["source"]=="TEXT"],["FAILED"]*3)

    def test_validation_and_independent_assessments(self):
        first,second=self.create(),self.create()
        self.assertNotEqual(first,second)
        listing=self.client.get("/api/v1/speaking-assessments")
        self.assertEqual(listing.status_code,200,listing.text)
        self.assertEqual({item["id"] for item in listing.json()},{first,second})
        self.assertTrue(all(len(item["criteria"]) == 5 for item in listing.json()))
        detail=self.client.get(f"/api/v1/speaking-assessments/{first}")
        self.assertEqual(detail.json()["transcript_snapshot"][0]["text"],"I is happy.")
        self.assertEqual(self.client.post(f"/api/v1/speaking-assessments/{first}/retry").status_code,409)
        self.assertEqual(self.client.post("/api/v1/speaking-assessments",json={"job_id":str(self.job_id),"part":4}).status_code,422)
        with Session(self.engine) as db:
            db.get(TranscriptionJob,self.job_id).status="PROCESSING"
            db.commit()
        self.assertEqual(self.client.post("/api/v1/speaking-assessments",json={"job_id":str(self.job_id)}).status_code,409)

    def test_audio_feedback_and_recommendations_are_independent(self):
        identity=self.create()
        provider=Mock()
        provider.evaluate_audio.return_value=[{"code":"pronunciation","score":6.5,"feedback":[{"segment_id":str(self.segment_id),"start_ms":10,"end_ms":500,"evidence":"Mock acoustic observation","explanation":"Test provider explanation"}],"recommendations":[{"exercise":"Test exercise","explanation":"Test recommendation"}]}]
        with TemporaryDirectory() as directory, patch.dict(os.environ,{"UPLOAD_DIR":directory}):
            Path(directory,"speech.mp3").touch()
            run_evaluation(identity,"AUDIO",self.engine,provider)
        result=self.client.get(f"/api/v1/speaking-assessments/{identity}").json()
        audio=next(c for c in result["criteria"] if c["source"]=="AUDIO" and c["code"]=="pronunciation")
        self.assertEqual(audio["status"],"COMPLETED")
        self.assertEqual(audio["score"],6.5)
        self.assertEqual(len(audio["recommendations"]),1)
        self.assertTrue(all(c["status"]=="QUEUED" for c in result["criteria"] if c["source"]=="TEXT"))

    def test_invalid_evidence_and_timestamps_rejected(self):
        for field,value in [("segment_id",str(uuid4())),("end_ms",2000),("evidence","invented quote")]:
            identity=self.create()
            finding={"segment_id":str(self.segment_id),"start_ms":0,"end_ms":1000,"evidence":"I is","explanation":"Test explanation",field:value}
            provider=Mock()
            provider.evaluate_text.return_value=[{"code":code,"feedback":[finding] if code=="grammar_accuracy" else []} for code in TEXT_CODES]
            with self.assertLogs("app.assessments",level="ERROR"):
                run_evaluation(identity,"TEXT",self.engine,provider)
            result=self.client.get(f"/api/v1/speaking-assessments/{identity}").json()
            self.assertTrue(all(c["status"]=="FAILED" for c in result["criteria"] if c["source"]=="TEXT"))

    def test_audio_fluency_persists_gap_times_without_text_or_pronunciation_scores(self):
        from app.speaking_audio import AudioFluencyEvaluator
        identity = self.create()
        provider = AudioFluencyEvaluator(reader=lambda _: [0]*160000,
            detector=lambda *_: [{"start":0,"end":16000},{"start":48000,"end":160000}])
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"UPLOAD_DIR":directory}):
            Path(directory,"speech.mp3").touch()
            run_evaluation(identity,"AUDIO",self.engine,provider)
        result = self.client.get(f"/api/v1/speaking-assessments/{identity}").json()
        audio = next(c for c in result["criteria"] if c["source"]=="AUDIO" and c["code"]=="fluency_coherence")
        self.assertEqual(audio["status"],"COMPLETED")
        self.assertEqual(audio["analysis"]["speech_duration_ms"],8000)
        self.assertEqual(audio["feedback"][0]["start_time"],1)
        self.assertEqual(audio["feedback"][0]["end_time"],3)
        self.assertTrue(audio["feedback"][0]["uncertain"])
        self.assertIsNone(audio["score"])
        pronunciation = next(c for c in result["criteria"] if c["code"]=="pronunciation")
        self.assertEqual(pronunciation["status"],"NOT_ASSESSED")
        self.assertIsNone(pronunciation["score"])
        self.assertTrue(all(c["status"]=="QUEUED" for c in result["criteria"] if c["source"]=="TEXT"))
