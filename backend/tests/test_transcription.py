import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.models import Base, TranscriptionJob, TranscriptSegment
from app.tasks import process_job
from app.transcription import Segment, WhisperProvider, transcribe_media


class PipelineTests(unittest.TestCase):
    def test_model_timestamps_language_and_silence(self):
        model = Mock()
        model.transcribe.return_value = (iter([SimpleNamespace(start=0.2, end=1.5, text=" Xin chào ")]), None)
        with patch("app.transcription.get_model", return_value=model):
            self.assertEqual(WhisperProvider().transcribe(Path("x.wav"), "vi"), [Segment(200, 1500, "Xin chào")])
            self.assertEqual(model.transcribe.call_args.kwargs["task"], "transcribe")
            self.assertEqual(model.transcribe.call_args.kwargs["language"], "vi")
            model.transcribe.return_value = (iter([]), None)
            self.assertEqual(WhisperProvider().transcribe(Path("x.wav"), "en"), [])

    def test_decode_and_cleanup(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "video.mp4"
            source.touch()
            provider = Mock()
            provider.transcribe.return_value = []
            with patch("app.transcription.subprocess.run", return_value=SimpleNamespace(stdout='{"streams":[{"codec_type":"audio"}]}')) as run:
                self.assertEqual(transcribe_media(source, provider=provider), [])
                audio = provider.transcribe.call_args.args[0]
                self.assertFalse(audio.parent.exists())
                self.assertIsNone(provider.transcribe.call_args.args[1])
                self.assertIn("16000", run.call_args.args[0])
            with patch("app.transcription.subprocess.run", return_value=SimpleNamespace(stdout='{"streams":[]}')):
                with self.assertRaises(ValueError):
                    transcribe_media(source)
            with patch("app.transcription.subprocess.run", return_value=SimpleNamespace(stdout='{"streams":[{"codec_type":"audio"}]}')):
                provider.transcribe.side_effect = RuntimeError("model failed")
                with self.assertRaises(RuntimeError):
                    transcribe_media(source, provider=provider)
                self.assertFalse(provider.transcribe.call_args.args[0].parent.exists())

    def test_persistence_failure_and_redelivery(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            job = TranscriptionJob(original_filename="x.wav", stored_filename="x.wav", media_type="audio/wav", size_bytes=10)
            session.add(job)
            session.commit()
            identity = str(job.id)
        with patch("app.tasks.transcribe_media", return_value=[Segment(0, 1000, "Hello")]) as transcribe:
            process_job(identity, engine=engine)
            process_job(identity, engine=engine)
            transcribe.assert_called_once()
        with Session(engine) as session:
            self.assertEqual(session.scalars(select(TranscriptionJob)).one().status, "COMPLETED")
            self.assertEqual(session.scalars(select(TranscriptSegment)).one().text, "Hello")
            session.scalars(select(TranscriptionJob)).one().status = "QUEUED"
            session.commit()
        with patch("app.tasks.transcribe_media", side_effect=ValueError("invalid media")):
            with self.assertLogs("app.tasks", level="ERROR"):
                process_job(identity, engine=engine)
        with Session(engine) as session:
            self.assertEqual(session.scalars(select(TranscriptionJob)).one().status, "FAILED")

    @unittest.skipUnless(os.getenv("TRANSCRIPTION_TEST_AUDIO"), "Set TRANSCRIPTION_TEST_AUDIO to a short speech sample")
    def test_real_audio(self):
        segments = transcribe_media(Path(os.environ["TRANSCRIPTION_TEST_AUDIO"]))
        self.assertTrue(segments)
        expected = os.getenv("TRANSCRIPTION_EXPECTED_TEXT")
        if expected:
            self.assertIn(expected.casefold(), " ".join(s.text for s in segments).casefold())


if __name__ == "__main__":
    unittest.main()
