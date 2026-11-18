import os
import unittest
from pathlib import Path
from uuid import uuid4
from tempfile import TemporaryDirectory
from unittest.mock import patch
import wave

from app.speaking_audio import AudioFluencyEvaluator, AudioAnalysisError, measurements, normalize_intervals, read_audio


class AudioAnalysisTests(unittest.TestCase):
    def segment(self, text="I I think um it is red I mean blue", start=0, end=10000):
        return {"id":str(uuid4()), "start_ms":start, "end_ms":end, "text":text}

    def test_speech_duration_rate_pauses_and_candidates(self):
        result = measurements(10000, [(0, 2000), (5000, 10000)], [self.segment()])
        self.assertEqual(result.analysis["speech_duration_ms"],7000)
        self.assertEqual(result.analysis["non_speech_duration_ms"],3000)
        self.assertEqual(result.analysis["long_pause_count"],1)
        self.assertGreater(result.analysis["approx_words_per_minute"],0)
        self.assertEqual({f.issue_type for f in result.feedback}, {"long_pause","possible_repetition","possible_hesitation","possible_self_correction"})
        pause = next(f for f in result.feedback if f.issue_type=="long_pause")
        self.assertEqual((pause.start_time,pause.end_time),(2,5))
        self.assertTrue(all(f.uncertain and f.confidence is None for f in result.feedback))

    def test_leading_trailing_silence_and_unreliable_timestamps(self):
        result=measurements(10000,[(2000,7000)],[self.segment("Natural conversational speech")])
        self.assertEqual(result.analysis["long_pause_count"],0)
        self.assertFalse(result.feedback)
        invalid=measurements(1000,[(0,500)],[self.segment(end=2000)])
        self.assertIsNone(invalid.analysis["approx_words_per_minute"])
        self.assertEqual(invalid.analysis["timestamp_status"],"NOT_ASSESSED")
        self.assertFalse(invalid.feedback)

    def test_silence_and_no_pronunciation_inference(self):
        evaluator=AudioFluencyEvaluator(reader=lambda _: [0]*16000,detector=lambda *_: [])
        results=evaluator.evaluate_audio(Path("unused"),{"segments":[self.segment("unusual spelling",end=1000)]})
        self.assertEqual(results[0].analysis["speech_duration_ms"],0)
        self.assertIsNone(results[0].analysis["approx_words_per_minute"])
        self.assertEqual(results[1].status,"NOT_ASSESSED")
        self.assertIsNone(results[1].score)
        self.assertFalse(results[1].feedback)

    def test_interval_validation_and_merge(self):
        self.assertEqual(normalize_intervals([{"start":0,"end":16000},{"start":8000,"end":32000}],32000),[(0,2000)])
        with self.assertRaises(AudioAnalysisError):
            normalize_intervals([{"start":-1,"end":1}],16000)

    def test_invalid_media_cleanup(self):
        import subprocess
        with TemporaryDirectory() as directory:
            source=Path(directory)/"bad.mp3"
            source.touch()
            with patch("app.speaking_audio.subprocess.run",side_effect=subprocess.CalledProcessError(1,"ffprobe")):
                with self.assertRaises(AudioAnalysisError):
                    read_audio(source)

    def test_overlong_recording_rejected_before_decode(self):
        from types import SimpleNamespace
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"SPEAKING_AUDIO_MAX_SECONDS": "600"}):
            source=Path(directory)/"long.mp3"
            source.touch()
            with patch("app.speaking_audio.subprocess.run",return_value=SimpleNamespace(stdout='{"streams":[{"codec_type":"audio"}],"format":{"duration":"601"}}')) as execute:
                with self.assertRaisesRegex(AudioAnalysisError,"exceeds configured analysis duration"):
                    read_audio(source)
                self.assertEqual(execute.call_count,1)

    def test_temporary_audio_removed_when_decode_fails(self):
        import subprocess
        from types import SimpleNamespace
        with TemporaryDirectory() as directory:
            source=Path(directory)/"bad.mp3"
            source.touch()
            temporary_paths=[]
            def execute(command, **kwargs):
                if command[0]=="ffprobe":
                    return SimpleNamespace(stdout='{"streams":[{"codec_type":"audio"}],"format":{"duration":"1"}}')
                temporary_paths.append(Path(command[-1]).parent)
                raise subprocess.CalledProcessError(1,"ffmpeg")
            with patch("app.speaking_audio.subprocess.run",side_effect=execute):
                with self.assertRaises(AudioAnalysisError):
                    read_audio(source)
            self.assertTrue(temporary_paths)
            self.assertFalse(temporary_paths[0].exists())

    @unittest.skipUnless(os.getenv("SPEAKING_AUDIO_TEST_FILE"),"Set SPEAKING_AUDIO_TEST_FILE to real speech audio")
    def test_real_vad_and_silent_audio(self):
        evaluator=AudioFluencyEvaluator()
        samples=read_audio(Path(os.environ["SPEAKING_AUDIO_TEST_FILE"]))
        duration=round(len(samples)*1000/16000)
        result=evaluator.evaluate_audio(Path(os.environ["SPEAKING_AUDIO_TEST_FILE"]),{"segments":[self.segment("Speech sample",end=duration)]})
        self.assertGreater(result[0].analysis["speech_duration_ms"],0)
        self.assertLessEqual(result[0].analysis["speech_duration_ms"],duration)
        self.assertEqual(result[1].status,"NOT_ASSESSED")
        with TemporaryDirectory() as directory:
            source=Path(directory)/"silence.wav"
            with wave.open(str(source),"wb") as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000); output.writeframes(b"\0"*64000)
            silence=evaluator.evaluate_audio(source,{"segments":[]})
            self.assertEqual(silence[0].analysis["speech_duration_ms"],0)
            self.assertIsNone(silence[0].analysis["approx_words_per_minute"])
