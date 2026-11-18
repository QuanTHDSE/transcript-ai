"""CPU audio measurements, with an explicit boundary around pronunciation."""
import json
import math
import os
import re
import subprocess
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from app.assessment_schemas import CriterionResult, Finding, Recommendation


class AudioAnalysisError(ValueError):
    pass


def silero_intervals(samples, sample_rate):
    # faster-whisper caches its bundled ONNX model in each worker process.
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    threshold = float(os.getenv("SPEAKING_VAD_THRESHOLD", "0.5"))
    if not 0 < threshold < 1:
        raise AudioAnalysisError("VAD threshold must be between zero and one")
    return get_speech_timestamps(samples, VadOptions(threshold=threshold, min_speech_duration_ms=100,
        min_silence_duration_ms=300, speech_pad_ms=0), sampling_rate=sample_rate)


def read_audio(media):
    """Bound decoded duration/memory, reject truncation, and always clean temporary PCM."""
    maximum = int(os.getenv("SPEAKING_AUDIO_MAX_SECONDS", "600"))
    if not 1 <= maximum <= 3600:
        raise AudioAnalysisError("Audio analysis limit must be 1–3600 seconds")
    timeout = int(os.getenv("MEDIA_TIMEOUT_SECONDS", "300"))
    media = Path(media).resolve(strict=True)
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams", "-show_format", "-of", "json", str(media)],
            check=True, capture_output=True, text=True, timeout=timeout)
        metadata = json.loads(probe.stdout)
        if not any(s.get("codec_type") == "audio" for s in metadata.get("streams", [])):
            raise AudioAnalysisError("Media contains no audio")
        duration = float(metadata.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration > maximum:
            raise AudioAnalysisError("Recording exceeds configured analysis duration")
        with TemporaryDirectory(prefix="transcripto-analysis-") as temporary:
            output = Path(temporary) / "audio.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe", "-i", str(media),
                "-t", str(maximum + 1), "-map", "0:a:0", "-vn", "-threads", "1", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(output)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
            with wave.open(str(output), "rb") as source:
                if not 0 < source.getnframes() <= maximum * 16000:
                    raise AudioAnalysisError("Decoded recording is empty or exceeds analysis limit")
                import numpy as np
                return np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype("float32") / 32768
    except (subprocess.SubprocessError, json.JSONDecodeError, wave.Error) as exc:
        raise AudioAnalysisError("Audio could not be decoded safely") from exc


def normalize_intervals(intervals, sample_count, rate=16000):
    merged = []
    for item in sorted(intervals, key=lambda interval: interval["start"]):
        start, end = item["start"], item["end"]
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= sample_count:
            raise AudioAnalysisError("VAD returned invalid sample intervals")
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(round(start * 1000 / rate), round(end * 1000 / rate)) for start, end in merged]


def measurements(duration_ms, intervals, segments, pause_ms=1500):
    """Deterministic measurements; transcript cues are candidates, never pronunciation."""
    speech = sum(end - start for start, end in intervals)
    cursor, silence = 0, []
    for start, end in intervals:
        if start > cursor:
            silence.append((cursor, start))
        cursor = end
    if cursor < duration_ms:
        silence.append((cursor, duration_ms))
    reliable = bool(segments) and all(isinstance(s.get("start_ms"), int) and isinstance(s.get("end_ms"), int)
        and 0 <= s["start_ms"] < s["end_ms"] <= duration_ms for s in segments)
    ordered = sorted(segments, key=lambda s: s["start_ms"]) if reliable else []
    reliable = reliable and all(a["end_ms"] <= b["start_ms"] for a,b in zip(ordered, ordered[1:]))
    words = sum(len(re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", s["text"])) for s in ordered)
    span = intervals[-1][1] - intervals[0][0] if intervals else 0
    covered = sum(max(0, min(end,s["end_ms"])-max(start,s["start_ms"])) for start,end in intervals for s in ordered)
    rate_supported = reliable and speech > 0 and covered / speech >= 0.8
    metrics = {"duration_ms": duration_ms, "speech_duration_ms": speech, "non_speech_duration_ms": duration_ms - speech,
        "speech_intervals": [{"start_ms":start,"end_ms":end} for start,end in intervals],
        "silence_intervals": [{"start_ms":start,"end_ms":end} for start,end in silence],
        "transcript_word_count": words if reliable else None,
        "transcript_speech_coverage": covered / speech if speech else None,
        "speaking_rate_status": "SUPPORTED" if rate_supported else "NOT_ASSESSED",
        "approx_words_per_minute": round(words * 60000 / span, 1) if rate_supported and span else None,
        "approx_articulation_words_per_minute": round(words * 60000 / speech, 1) if rate_supported else None,
        "timestamp_status": "SUPPORTED" if reliable else "NOT_ASSESSED",
        "word_alignment": "NOT_ASSESSED", "speaker_diarization": "NOT_ASSESSED",
        "confidence_note": "VAD supplies decisions, not calibrated finding confidence. WPM depends on transcript coverage; background voices/noise can affect measurements."}
    findings = []
    if reliable and intervals:
        for (left_start, left_end), (right_start, right_end) in zip(intervals, intervals[1:]):
            if right_start - left_end < pause_ms:
                continue
            anchor = min(ordered, key=lambda s: min(abs(s["end_ms"]-left_end), abs(s["start_ms"]-right_start)))
            findings.append(Finding(segment_id=anchor["id"], start_ms=left_end, end_ms=right_start,
                issue_type="long_pause", evidence=f"VAD non-speech interval {left_end}–{right_start} ms.",
                confidence=None, uncertain=True,
                explanation="An internal non-speech gap was detected. It may be planning, a natural boundary, noise, or a pause; it is not automatically a fluency error. Segment ID anchors the adjacent transcript, not word alignment.",
                suggested_practice="Replay this interval; if the pause is unintended, practise the idea in short thought groups."))
        for segment in ordered:
            if not any(start < segment["end_ms"] and end > segment["start_ms"] for start,end in intervals):
                continue
            text = segment["text"]
            patterns = [
                ("possible_repetition", r"\b([A-Za-z]+)\s+\1\b", "Check whether the repeated word was intentional emphasis; re-record only unintended repetitions."),
                ("possible_hesitation", r"\b(?:um|uh|erm)\b", "Practise a short silent planning pause rather than relying on fillers."),
                ("possible_self_correction", r"\b\w+\s+(?:I mean|sorry|rather)\s+\w+\b", "Practise reformulating the same idea once; self-correction can be a useful speaking skill."),
            ]
            for kind, pattern, exercise in patterns:
                match = re.search(pattern, text, re.I)
                if match:
                    findings.append(Finding(segment_id=segment["id"], start_ms=segment["start_ms"], end_ms=segment["end_ms"],
                        issue_type=kind, evidence=match.group(0), confidence=None, uncertain=True,
                        explanation="Transcript-supported candidate in an audio interval containing VAD speech. Segment timing only; word timing and speaker intent are unverified. This is not necessarily a mistake.",
                        suggested_practice=exercise))
    metrics["long_pause_count"] = sum(f.issue_type == "long_pause" for f in findings) if reliable else None
    return CriterionResult(code="fluency_coherence", analysis=metrics, feedback=findings,
        recommendations=[Recommendation(exercise=f.suggested_practice, explanation=f.explanation) for f in findings])


class AudioFluencyEvaluator:
    def __init__(self, detector=None, reader=None):
        self.detector = detector or silero_intervals
        self.reader = reader or read_audio

    def evaluate_audio(self, media, context):
        samples = self.reader(media)
        duration = round(len(samples) * 1000 / 16000)
        intervals = normalize_intervals(self.detector(samples, 16000), len(samples))
        pause = int(os.getenv("SPEAKING_LONG_PAUSE_MS", "1500"))
        if not 300 <= pause <= 30000:
            raise AudioAnalysisError("Long-pause threshold must be 300–30000 ms")
        fluency = measurements(duration, intervals, context["segments"], pause)
        fluency.analysis["vad_provider"] = "silero_vad_v6_onnx" if self.detector is silero_intervals else "configured_detector"
        pronunciation = CriterionResult(code="pronunciation", status="NOT_ASSESSED", analysis={
            "reason": "No validated pronunciation evaluator is configured. Silero detects speech activity; Whisper recognition confidence is not pronunciation accuracy.",
            "phoneme_alignment": "NOT_ASSESSED", "word_alignment": "NOT_ASSESSED", "accent_correctness": "NOT_ASSESSED",
            "pronunciation_errors": "NOT_ASSESSED", "pronunciation_confidence": None,
            "next_step": "Use a validated audio pronunciation provider or a qualified listener for targeted sound practice."})
        return [fluency, pronunciation]


def create_evaluator():
    return AudioFluencyEvaluator()
