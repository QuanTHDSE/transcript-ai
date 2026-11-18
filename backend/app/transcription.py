"""Media preparation and replaceable speech recognition provider."""
import json
import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol


class TransientError(Exception):
    pass


@dataclass(frozen=True)
class Segment:
    start_ms: int
    end_ms: int
    text: str


class Provider(Protocol):
    def transcribe(self, audio: Path, language: str | None) -> list[Segment]: ...


@lru_cache(maxsize=1)
def get_model():
    from faster_whisper import WhisperModel
    return WhisperModel(os.getenv("WHISPER_MODEL", "tiny"), device="cpu", compute_type="int8",
                        cpu_threads=int(os.getenv("WHISPER_CPU_THREADS", "2")), num_workers=1)


class WhisperProvider:
    def transcribe(self, audio: Path, language: str | None) -> list[Segment]:
        segments, _ = get_model().transcribe(str(audio), language=language, task="transcribe",
                                           vad_filter=True, beam_size=1, condition_on_previous_text=False)
        result = []
        for item in segments:
            start, end = round(item.start * 1000), round(item.end * 1000)
            if item.text.strip() and end > start >= 0:
                result.append(Segment(start, end, item.text.strip()))
        return result


def transcribe_media(source: Path, language: str = "auto", provider: Provider | None = None, on_stage=None) -> list[Segment]:
    if language not in {"auto", "en", "vi"}:
        raise ValueError("Language must be auto, en, or vi")
    source = source.resolve(strict=True)
    timeout = int(os.getenv("MEDIA_TIMEOUT_SECONDS", "300"))
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams", "-of", "json", str(source)],
                               check=True, capture_output=True, text=True, timeout=timeout)
        if not any(s.get("codec_type") == "audio" for s in json.loads(probe.stdout).get("streams", [])):
            raise ValueError("Media contains no audio stream")
        with TemporaryDirectory(prefix="transcripto-") as temporary:
            if on_stage:
                on_stage("extracting_audio")
            audio = Path(temporary) / "audio.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe", "-i", str(source),
                            "-map", "0:a:0", "-vn", "-threads", "1", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
            if on_stage:
                on_stage("transcribing")
            return (provider or WhisperProvider()).transcribe(audio, None if language == "auto" else language)
    except subprocess.TimeoutExpired as exc:
        raise TransientError("Media processing timed out") from exc
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValueError("Media could not be decoded") from exc
