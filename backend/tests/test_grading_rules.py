import unittest
import os
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from unittest.mock import patch
from app.main import app
from app.models import Base, TranscriptionJob, TranscriptSegment
from app.routes import session_dependency
from app.template_models import SpeakingTemplate, TemplateVersion
from app.grading_rules import parse_instructions

class GradingRulesTests(unittest.TestCase):
    def setUp(self):
        self.schema = None
        if os.getenv("TEST_DATABASE_URL"):
            self.schema = "grading_test_" + uuid4().hex
            self.admin = create_engine(os.environ["TEST_DATABASE_URL"])
            with self.admin.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
            self.engine = create_engine(os.environ["TEST_DATABASE_URL"], connect_args={"options": f"-csearch_path={self.schema}"})
        else:
            self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.template_id, self.version_id = uuid4(), uuid4()
        with Session(self.engine) as db:
            db.add(SpeakingTemplate(id=self.template_id, name="Example"))
            db.add(TemplateVersion(id=self.version_id, template_id=self.template_id, version=1, original_filename="x.docx", stored_filename="x.docx", sha256="a"*64, size_bytes=1, parsed={}, protection_decisions={}))
            db.commit()
        def session():
            with Session(self.engine) as db: yield db
        app.dependency_overrides[session_dependency] = session
        self.client = TestClient(app)
    def tearDown(self):
        self.client.close(); app.dependency_overrides.clear(); self.engine.dispose()
        if self.schema:
            with self.admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            self.admin.dispose()
    def test_parse_bilingual_and_review(self):
        result = parse_instructions("Keep all original red text unchanged. Mark grammar mistakes in red with an arrow. Part 1 must last 20–25 seconds. Parts 2 and 3 must last 40–45 seconds. Phần 4 từ 110 đến 120 giây.")
        self.assertTrue(result["review_required"])
        self.assertTrue(result["rules"]["preserve_original_red_text"])
        self.assertEqual([r["part"] for r in result["rules"]["duration_limits"]], [1,2,3,4])
        self.assertEqual(result["rules"]["correction_styles"][0]["correction_placement"], "beside_arrow")
        self.assertEqual(parse_instructions('Phải sử dụng "In my opinion".')["rules"]["required_structures"], ["In my opinion"])
        self.assertTrue(parse_instructions("Make it sound better")["review_required"])
    def test_versioning_persistence_and_inclusive_boundaries(self):
        rules = {"duration_limits": [{"part":p,"min_seconds":lo,"max_seconds":hi} for p,lo,hi in [(1,20,25),(2,40,45),(3,40,45),(4,110,120)]], "correction_styles":[], "preserve_original_red_text":True, "required_structures":["In my opinion"]}
        body = {"name":"Preset", "template_version_id":str(self.version_id), "instructions":" Original instructions\n", "rules":rules, "reviewed":True}
        created = self.client.post("/api/v1/grading-presets", json=body)
        self.assertEqual(created.status_code,201,created.text)
        version = created.json(); self.assertEqual(version["instructions"], body["instructions"])
        for part, low, high in [(1,20,25),(2,40,45),(3,40,45),(4,110,120)]:
            for duration, expected in [(low-0.1,"NOT PASSED"),(low,"PASSED"),(high,"PASSED"),(high+0.1,"NOT PASSED")]:
                response = self.client.post(f'/api/v1/grading-presets/versions/{version["id"]}/duration-results', json={"part":part,"duration_seconds":duration})
                self.assertEqual(response.json()["status"],expected)
                self.assertEqual(self.client.get('/api/v1/grading-presets/duration-results/'+response.json()["id"]).json()["status"],expected)
        updated = self.client.post(f'/api/v1/grading-presets/{version["preset_id"]}/versions', json={"instructions":"new", "rules":{**rules, "duration_limits":[]}, "reviewed":True})
        self.assertEqual(updated.json()["version"],2)
        self.assertEqual(self.client.get(f'/api/v1/grading-presets/versions/{version["id"]}').json()["rules"], rules)
        self.assertEqual(self.client.post("/api/v1/grading-presets", json={**body,"reviewed":False}).status_code,422)
        job_id = uuid4()
        with Session(self.engine) as db:
            db.add(TranscriptionJob(id=job_id, original_filename="x.mp3", stored_filename="x.mp3", media_type="audio/mpeg", size_bytes=1, status="COMPLETED", language="en", segments=[TranscriptSegment(ordinal=0, start_ms=0, end_ms=1000, text="Hello")]))
            db.commit()
        with patch("app.assessments.dispatch"):
            assessment = self.client.post("/api/v1/speaking-assessments", json={"job_id":str(job_id), "part":4, "grading_rule_version_id":version["id"]})
        self.assertEqual(assessment.status_code,202,assessment.text)
        self.assertEqual(assessment.json()["grading_rule_version_id"],version["id"])
