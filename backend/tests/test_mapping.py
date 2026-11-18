import unittest
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.main import app
from app.models import Base, TranscriptionJob, TranscriptSegment
from app.routes import session_dependency
from app.template_models import SpeakingTemplate, TemplateVersion
from app.grading_models import GradingPreset, GradingRuleVersion
from app.mapping import questions, suggest, duration_metrics, analyze_mapping, recover_mappings
from app.mapping_models import RecordingMapping

class MappingTests(unittest.TestCase):
    def setUp(self):
        self.schema = None
        if os.getenv("TEST_DATABASE_URL"):
            self.schema = "mapping_test_" + uuid4().hex
            self.admin = create_engine(os.environ["TEST_DATABASE_URL"])
            with self.admin.begin() as connection: connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
            self.engine = create_engine(os.environ["TEST_DATABASE_URL"], connect_args={"options":f"-csearch_path={self.schema}"})
        else:
            self.engine = create_engine("sqlite://", connect_args={"check_same_thread":False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.tid,self.tvid,self.pid,self.rid,self.job1,self.job2 = [uuid4() for _ in range(6)]
        self.parsed={"blocks":[{"type":"paragraph","id":"body/0","text":"Part 1","question":False},{"type":"paragraph","id":"body/1","text":"Where is your hometown?","question":True},{"type":"paragraph","id":"body/2","text":"What is your favourite hobby?","question":True},{"type":"paragraph","id":"body/3","text":"Part 2","question":False},{"type":"paragraph","id":"body/4","text":"Describe a memorable journey?","question":True}]}
        with Session(self.engine) as db:
            db.add(SpeakingTemplate(id=self.tid,name="Template")); db.flush()
            db.add(TemplateVersion(id=self.tvid,template_id=self.tid,version=1,original_filename="t.docx",stored_filename="t.docx",sha256="a"*64,size_bytes=1,parsed=self.parsed,protection_decisions={})); db.flush()
            db.add(GradingPreset(id=self.pid,name="Rules",template_version_id=self.tvid)); db.flush()
            db.add(GradingRuleVersion(id=self.rid,preset_id=self.pid,version=1,instructions="test",rules={}))
            for identity,name in [(self.job1,"part1.mp3"),(self.job2,"part2.mp4")]: db.add(TranscriptionJob(id=identity,original_filename=name,stored_filename=str(identity),media_type="audio/mpeg",size_bytes=1,status="COMPLETED",language="en",segments=[TranscriptSegment(ordinal=0,start_ms=0,end_ms=1000,text="My hometown is lovely")]))
            db.commit()
        def session():
            with Session(self.engine) as db: yield db
        app.dependency_overrides[session_dependency]=session
        self.client=TestClient(app)
    def tearDown(self):
        self.client.close();app.dependency_overrides.clear();self.engine.dispose()
        if self.schema:
            with self.admin.begin() as connection: connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            self.admin.dispose()
    def test_suggestions_are_candidates_and_ambiguous_part(self):
        items=questions(self.parsed)
        self.assertEqual([(q["part"],q["id"]) for q in items],[(1,"body/1"),(1,"body/2"),(2,"body/4")])
        self.assertEqual(len(suggest("part1.mp3","",items)),2)
        self.assertEqual(suggest("part2.mp3","",items)[0]["question_id"],"body/4")
        self.assertEqual(suggest("unknown.mp3","",items),[])
    def test_two_students_missing_and_invalid_mapping(self):
        other_template,other_version=uuid4(),uuid4()
        with Session(self.engine) as db:
            db.add(SpeakingTemplate(id=other_template,name="Other"));db.flush()
            db.add(TemplateVersion(id=other_version,template_id=other_template,version=1,original_filename="o.docx",stored_filename="o.docx",sha256="b"*64,size_bytes=1,parsed=self.parsed,protection_decisions={}));db.commit()
        mismatch=self.client.post("/api/v1/mapping-batches",json={"template_version_id":str(other_version),"rule_version_id":str(self.rid)})
        self.assertEqual(mismatch.status_code,422)
        created=self.client.post("/api/v1/mapping-batches",json={"template_version_id":str(self.tvid),"rule_version_id":str(self.rid)})
        self.assertEqual(created.status_code,201,created.text); identity=created.json()["id"]
        for job in (self.job1,self.job2): self.assertEqual(self.client.post(f"/api/v1/mapping-batches/{identity}/recordings",json={"job_id":str(job)}).status_code,201)
        batch=self.client.get(f"/api/v1/mapping-batches/{identity}").json(); first,second=batch["recordings"]
        invalid=self.client.patch(f'/api/v1/mapping-batches/{identity}/recordings/{first["id"]}',json={"student_id":"A","part":2,"question_id":"body/1","confirmed":True})
        self.assertEqual(invalid.status_code,422)
        for row,student,part,question in [(first,"A",1,"body/1"),(second,"B",2,"body/4")]:
            response=self.client.patch(f'/api/v1/mapping-batches/{identity}/recordings/{row["id"]}',json={"student_id":student,"part":part,"question_id":question,"confirmed":True}); self.assertEqual(response.status_code,200,response.text)
        batch=response.json(); self.assertEqual({r["student_id"] for r in batch["recordings"]},{"A","B"}); self.assertEqual(len(batch["missing"]),4)
        self.assertEqual(batch["recordings"][0]["transcript_id"],batch["recordings"][0]["recording_id"])
    def test_audio_duration_and_silence(self):
        data=duration_metrics(10000,[(1000,3000),(5000,9000)])
        self.assertEqual((data["media_duration_ms"],data["task_elapsed_ms"],data["active_voiced_ms"]),(10000,8000,6000))
        self.assertEqual(data["long_pauses"],[{"start_ms":3000,"end_ms":5000}])
        self.assertEqual(data["boundary_status"],"UNCERTAIN")
        self.assertIsNone(duration_metrics(1000,[])["task_elapsed_ms"])
    def test_confirmed_completed_recording_is_queued_once(self):
        identity=self.client.post("/api/v1/mapping-batches",json={"template_version_id":str(self.tvid),"rule_version_id":str(self.rid)}).json()["id"]
        self.client.post(f"/api/v1/mapping-batches/{identity}/recordings",json={"job_id":str(self.job1)})
        row=self.client.get(f"/api/v1/mapping-batches/{identity}").json()["recordings"][0]
        self.client.patch(f'/api/v1/mapping-batches/{identity}/recordings/{row["id"]}',json={"student_id":"A","part":1,"question_id":"body/1","confirmed":True})
        with patch("app.mapping.analyze_mapping.apply_async") as publish:
            recover_mappings(self.engine); recover_mappings(self.engine)
            publish.assert_called_once()
        row=self.client.get(f"/api/v1/mapping-batches/{identity}").json()["recordings"][0]
        self.assertEqual(row["analysis_status"],"QUEUED")
    def test_worker_extracts_duration_and_applies_selected_metric(self):
        with Session(self.engine) as db:
            db.get(GradingRuleVersion,self.rid).rules={"duration_limits":[{"part":1,"min_seconds":5,"max_seconds":8}]}
            db.commit()
        identity=self.client.post("/api/v1/mapping-batches",json={"template_version_id":str(self.tvid),"rule_version_id":str(self.rid)}).json()["id"]
        self.client.post(f"/api/v1/mapping-batches/{identity}/recordings",json={"job_id":str(self.job1)})
        item=self.client.get(f"/api/v1/mapping-batches/{identity}").json()["recordings"][0]
        self.assertEqual(self.client.post(f'/api/v1/mapping-batches/{identity}/recordings/{item["id"]}/prepare').status_code,409)
        self.client.patch(f'/api/v1/mapping-batches/{identity}/recordings/{item["id"]}',json={"student_id":"A","part":1,"question_id":"body/1","confirmed":True})
        with Session(self.engine) as db:
            db.get(RecordingMapping,UUID(item["id"])).analysis_status="QUEUED";db.commit()
        with TemporaryDirectory() as directory:
            Path(directory,str(self.job1)).touch()
            with patch.dict(os.environ,{"UPLOAD_DIR":directory}), patch("app.mapping.database_engine",return_value=self.engine), patch("app.mapping.subprocess.run",return_value=SimpleNamespace(stdout='{"format":{"duration":"10"}}')), patch("app.speaking_audio.read_audio",return_value=[0]*160000), patch("app.speaking_audio.silero_intervals",return_value=[{"start":16000,"end":48000},{"start":80000,"end":144000}]):
                analyze_mapping(str(item["id"]))
        row=self.client.get(f"/api/v1/mapping-batches/{identity}").json()["recordings"][0]
        self.assertEqual(row["duration"]["duration_check"],"PASSED")
        self.assertEqual(row["duration"]["task_elapsed_ms"],8000)
        self.assertEqual(row["duration"]["duration_check_certainty"],"UNCERTAIN")
        self.assertTrue(row["assessment_ready"])
        self.assertEqual(row["aligned"]["question"]["id"],"body/1")
    @unittest.skipUnless(os.getenv("SPEAKING_AUDIO_TEST_FILE"), "Set SPEAKING_AUDIO_TEST_FILE for real audio")
    def test_real_audio_duration_extraction(self):
        import shutil
        identity=self.client.post("/api/v1/mapping-batches",json={"template_version_id":str(self.tvid),"rule_version_id":str(self.rid)}).json()["id"]
        self.client.post(f"/api/v1/mapping-batches/{identity}/recordings",json={"job_id":str(self.job1)})
        item=self.client.get(f"/api/v1/mapping-batches/{identity}").json()["recordings"][0]
        self.client.patch(f'/api/v1/mapping-batches/{identity}/recordings/{item["id"]}',json={"student_id":"A","part":1,"question_id":"body/1","confirmed":True})
        with Session(self.engine) as db:
            db.get(RecordingMapping,UUID(item["id"])).analysis_status="QUEUED";db.commit()
        with TemporaryDirectory() as directory:
            shutil.copyfile(os.environ["SPEAKING_AUDIO_TEST_FILE"],Path(directory,str(self.job1)))
            with patch.dict(os.environ,{"UPLOAD_DIR":directory}), patch("app.mapping.database_engine",return_value=self.engine):
                analyze_mapping(item["id"])
        row=self.client.get(f"/api/v1/mapping-batches/{identity}").json()["recordings"][0]
        self.assertEqual(row["analysis_status"],"COMPLETED",row["error"])
        self.assertGreater(row["duration"]["media_duration_ms"],0)
        self.assertGreater(row["duration"]["active_voiced_ms"],0)
        self.assertLessEqual(row["duration"]["speech_end_ms"],row["duration"]["media_duration_ms"])
