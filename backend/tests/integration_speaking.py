"""Opt-in live speaking check with real audio and configured backend providers."""
import argparse
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--keep", action="store_true", help="Keep the test job temporarily for browser review")
    args = parser.parse_args()
    created = None

    def request(path, method="GET", data=None, content_type=None):
        headers = {"Content-Type": content_type} if content_type else {}
        with urlopen(Request(args.base_url + path, data=data, method=method, headers=headers), timeout=30) as response:
            body = response.read()
            return json.loads(body) if body else None

    def wait(path, terminal):
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            item = request(path)
            if item["status"] in terminal:
                return item
            time.sleep(2)
        raise TimeoutError(f"Timed out: {path}")

    try:
        boundary = uuid4().hex
        content = args.audio.read_bytes()
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{args.audio.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                + content + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\nen\r\n--{boundary}--\r\n'.encode())
        uploaded = request("/api/jobs", "POST", body, f"multipart/form-data; boundary={boundary}")
        created = uploaded[0]["job_id"]
        job = wait(f"/api/jobs/{created}", {"COMPLETED", "FAILED"})
        assert job["status"] == "COMPLETED" and job["segments"], job
        payload = json.dumps({"job_id": created, "part": 2, "question": "Describe a memorable event."}).encode()
        first = request("/api/v1/speaking-assessments", "POST", payload, "application/json")
        second = request("/api/v1/speaking-assessments", "POST", payload, "application/json")
        assert first["id"] != second["id"]
        reports = [wait(f"/api/v1/speaking-assessments/{item['id']}", {"COMPLETED", "PARTIAL", "FAILED", "NOT_ASSESSED"}) for item in (first, second)]
        for report in reports:
            assert report["transcript_snapshot"] == [{key: segment[key] for key in ("id", "start_ms", "end_ms", "text")} for segment in job["segments"]]
            fluency = next(c for c in report["criteria"] if c["source"] == "AUDIO" and c["code"] == "fluency_coherence")
            pronunciation = next(c for c in report["criteria"] if c["code"] == "pronunciation")
            assert fluency["status"] == "COMPLETED", fluency
            assert fluency["analysis"]["speech_duration_ms"] > 0
            assert fluency["analysis"]["speech_intervals"]
            assert all(0 <= f["start_ms"] < f["end_ms"] <= fluency["analysis"]["duration_ms"] for f in fluency["feedback"])
            assert pronunciation["status"] == "NOT_ASSESSED" and pronunciation["score"] is None and not pronunciation["feedback"]
        listed = request("/api/v1/speaking-assessments")
        assert {report["id"] for report in reports} <= {item["id"] for item in listed}
        print(json.dumps({"job_id": created, "transcription_segments": len(job["segments"]), "assessments": [{"id": r["id"], "status": r["status"], "criteria": {c["source"] + ":" + c["code"]: c["status"] for c in r["criteria"]}} for r in reports]}, indent=2))
    finally:
        if created and not args.keep:
            job = request(f"/api/jobs/{created}")
            if job["status"] in {"QUEUED", "PROCESSING"}:
                request(f"/api/jobs/{created}/cancel", "POST")
            request(f"/api/jobs/{created}", "DELETE")


if __name__ == "__main__":
    main()
