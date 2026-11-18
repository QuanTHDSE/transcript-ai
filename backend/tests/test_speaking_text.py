import json
import os
import unittest
from io import BytesIO
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from uuid import uuid4
from threading import Event
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.models import Base, TranscriptSegment, TranscriptionJob
from app.assessment_models import AssessmentCriterion, SpeakingAssessment, SpeakingTextCache
from app.assessments import TEXT_CODES, run_evaluation
from app.speaking_text import ChatCompletionsClient, LLMError, SpeakingTextEvaluator, chunks


class TextFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.schema = None
        if os.getenv("TEST_DATABASE_URL"):
            self.schema = "speaking_text_test_" + uuid4().hex
            self.admin = create_engine(os.environ["TEST_DATABASE_URL"])
            with self.admin.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
            self.engine = create_engine(os.environ["TEST_DATABASE_URL"], connect_args={"options": f"-csearch_path={self.schema}"})
        else:
            self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.segment_id = uuid4()
        self.job_id = uuid4()
        self.context = {"job_id": str(self.job_id), "part": 2, "question": "Describe a happy day", "segments": [{"id": str(self.segment_id), "start_ms": 100, "end_ms": 900, "text": "I is happy today."}]}
        with Session(self.engine) as db:
            job = TranscriptionJob(id=self.job_id, original_filename="x.wav", stored_filename="x.wav", media_type="audio/wav", size_bytes=1, status="COMPLETED", language="en")
            job.segments = [TranscriptSegment(id=self.segment_id, ordinal=0, start_ms=100, end_ms=900, text="I is happy today.")]
            db.add(job); db.commit()
        self.issue = {"category":"grammar", "issue_type":"mistake", "segment_id":str(self.segment_id), "original_text":"I is", "corrected_text":"I am", "explanation_vi":"Lỗi hòa hợp chủ ngữ.", "explanation_en":"Subject agreement.", "severity":"medium", "confidence":0.9, "suggested_practice":"Practise subject-verb agreement."}
        self.client = Mock(model="test-model", url="https://example.invalid/chat")
        self.client.model = "test-model"
        self.client.url = "https://example.invalid/chat"
        self.client.complete.return_value = json.dumps({"issues":[self.issue]}, ensure_ascii=False)
        self.evaluator = SpeakingTextEvaluator(self.client, self.engine)

    def tearDown(self):
        self.engine.dispose()
        if self.schema:
            with self.admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            self.admin.dispose()

    def test_cache_and_bilingual_fields(self):
        first = self.evaluator.evaluate_text(self.context)
        second = self.evaluator.evaluate_text(self.context)
        self.client.complete.assert_called_once()
        finding = next(result for result in first if result.code == "grammar_accuracy").feedback[0]
        self.assertEqual((finding.start_time, finding.end_time), (0.1, 0.9))
        self.assertEqual(finding.original_text, "I is")
        self.assertEqual(finding.explanation_vi, self.issue["explanation_vi"])
        self.assertEqual(finding.issue_type, "mistake")
        self.assertEqual(first, second)

    def test_valid_sentence_returns_no_fabricated_feedback(self):
        context = {**self.context, "segments": [{**self.context["segments"][0], "text": "I am happy today."}]}
        self.client.complete.return_value = '{"issues":[]}'
        results = self.evaluator.evaluate_text(context)
        self.assertTrue(all(not result.feedback and result.score is None for result in results))
        self.assertEqual(self.client.complete.call_count, 1)

    def test_invalid_json_and_fabricated_quote_never_cached(self):
        self.client.complete.return_value = "not json"
        with self.assertRaises(LLMError):
            self.evaluator.evaluate_text(self.context)
        self.client.complete.return_value = json.dumps({"issues":[{**self.issue,"original_text":"invented"}]})
        with self.assertRaises(LLMError):
            self.evaluator.evaluate_text(self.context)
        self.assertEqual(self.client.complete.call_count, 2)

    def test_chunk_limits_preserve_previous_context(self):
        segments = [{"id":str(uuid4()), "start_ms":0,"end_ms":1000,"text":"a"*700}, {"id":str(uuid4()), "start_ms":1000,"end_ms":2000,"text":"last"}]
        batches = list(chunks(segments, 256))
        self.assertTrue(all(sum(len(s["text"]) for s in batch) <= 256 and len(batch) <= 40 for batch, _ in batches))
        self.assertTrue(batches[1][1])
        self.assertEqual("".join(s["text"] for batch, _ in batches for s in batch), "a"*700 + "last")

    def test_rate_limit_retries_are_bounded(self):
        with patch.dict(os.environ, {"SPEAKING_LLM_URL":"https://example.invalid", "SPEAKING_LLM_MODEL":"m"}):
            client = ChatCompletionsClient()
            error = HTTPError("https://example.invalid", 429, "limited", {}, BytesIO())
            with patch("app.speaking_text.urlopen", side_effect=error) as call, patch("app.speaking_text.time.sleep"):
                with self.assertRaisesRegex(LLMError, "429"):
                    client.complete([])
                self.assertEqual(call.call_count, 3)
            error.close()

    def test_rate_limit_then_success(self):
        with patch.dict(os.environ, {"SPEAKING_LLM_URL":"https://example.invalid", "SPEAKING_LLM_MODEL":"m"}):
            client = ChatCompletionsClient()
            error = HTTPError("https://example.invalid", 429, "limited", {}, BytesIO())
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = json.dumps({"choices":[{"message":{"content":'{"issues":[]}'}}]}).encode()
            with patch("app.speaking_text.urlopen", side_effect=[error, response]) as call, patch("app.speaking_text.time.sleep"):
                self.assertEqual(client.complete([]), '{"issues":[]}')
                self.assertEqual(call.call_count, 2)
            error.close()

    @unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "PostgreSQL required for advisory-lock concurrency")
    def test_concurrent_requests_share_one_provider_call(self):
        entered, release = Event(), Event()
        def complete(messages):
            entered.set()
            self.assertTrue(release.wait(10))
            return json.dumps({"issues": [self.issue]})
        self.client.complete.side_effect = complete
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.evaluator.evaluate_text, self.context)
            try:
                self.assertTrue(entered.wait(10))
                second = pool.submit(self.evaluator.evaluate_text, self.context)
            finally:
                release.set()
            first.result(timeout=10); second.result(timeout=10)
        self.client.complete.assert_called_once()

    def test_persistence_does_not_modify_transcript(self):
        job_id, assessment_id = self.job_id, uuid4()
        with Session(self.engine) as db:
            assessment = SpeakingAssessment(id=assessment_id, job_id=job_id, transcript_snapshot=self.context["segments"], part=2, question=self.context["question"], criteria=[AssessmentCriterion(code=code, source="TEXT") for code in TEXT_CODES] + [AssessmentCriterion(code="pronunciation", source="AUDIO")])
            db.add(assessment); db.commit()
        run_evaluation(str(assessment_id), "TEXT", self.engine, self.evaluator)
        with Session(self.engine) as db:
            self.assertEqual(db.get(TranscriptSegment, self.segment_id).text, "I is happy today.")
            result = db.get(SpeakingAssessment, assessment_id)
            grammar = next(c for c in result.criteria if c.code == "grammar_accuracy")
            self.assertEqual(grammar.feedback[0].explanation_vi, self.issue["explanation_vi"])
            self.assertEqual(grammar.feedback[0].suggested_practice, self.issue["suggested_practice"])
            self.assertEqual(next(c for c in result.criteria if c.code == "pronunciation").status, "QUEUED")

    @unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "PostgreSQL required for cascade enforcement")
    def test_deleting_recording_removes_cached_feedback(self):
        self.evaluator.evaluate_text(self.context)
        with Session(self.engine) as db:
            self.assertEqual(db.query(SpeakingTextCache).count(), 1)
            db.delete(db.get(TranscriptionJob, self.job_id))
            db.commit()
        with Session(self.engine) as db:
            self.assertEqual(db.query(SpeakingTextCache).count(), 0)

