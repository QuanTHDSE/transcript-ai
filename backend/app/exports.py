"""Pure transcript formatting functions shared by download endpoints and tests."""
import json


def timestamp(milliseconds: int, separator: str = ",") -> str:
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, int) or milliseconds < 0:
        raise ValueError("Timestamp must be a nonnegative integer number of milliseconds")
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def render(segments, format: str) -> tuple[str, str]:
    if format not in {"txt", "srt", "vtt", "json"}:
        raise ValueError("Unsupported export format")
    rows = []
    for segment in segments:
        start, end = segment.start_ms, segment.end_ms
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
            raise ValueError("Transcript contains invalid segment timestamps")
        rows.append((segment.ordinal, start, end, segment.text))
    rows.sort(key=lambda item: item[0])
    if format == "txt":
        return "\n".join(text for _, _, _, text in rows).rstrip() + "\n", "text/plain; charset=utf-8"
    if format == "json":
        return json.dumps([{"ordinal": n, "start_ms": start, "end_ms": end, "text": text} for n, start, end, text in rows], ensure_ascii=False, indent=2) + "\n", "application/json; charset=utf-8"
    separator = "." if format == "vtt" else ","
    cues = []
    for number, (_, start, end, text) in enumerate(rows, start=1):
        timing = f"{timestamp(start, separator)} --> {timestamp(end, separator)}"
        cues.append(f"{number}\n{timing}\n{text}" if format == "srt" else f"{timing}\n{text}")
    content = ("WEBVTT\n\n" if format == "vtt" else "") + "\n\n".join(cues) + "\n"
    return content, "text/vtt; charset=utf-8" if format == "vtt" else "application/x-subrip; charset=utf-8"
