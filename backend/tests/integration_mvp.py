"""Opt-in live HTTP check. Requires a running stack and real MP3/MP4 speech clips."""
import argparse
import json
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mp3", type=Path, required=True)
    parser.add_argument("--mp4", type=Path, required=True)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    created = []
    evidence = {}

    def request(path, method="GET", data=None, headers=None):
        with urlopen(Request(args.base_url + path, data=data, method=method, headers=headers or {}), timeout=30) as response:
            return response.status, response.read(), dict(response.headers)

    def api(path, method="GET", payload=None):
        _, body, _ = request(path, method, json.dumps(payload).encode() if payload is not None else None, {"Content-Type": "application/json"})
        return json.loads(body) if body else None

    def upload(files):
        boundary = uuid4().hex
        body = bytearray()
        for name, content in files:
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
            body.extend(content)
            body.extend(b"\r\n")
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\nen\r\n--{boundary}--\r\n'.encode())
        _, data, _ = request("/api/jobs", "POST", bytes(body), {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        results = json.loads(data)
        assert all("job_id" in item for item in results), results
        ids = [item["job_id"] for item in results]
        created.extend(ids)
        return ids

    def wait(identity):
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            job = api(f"/api/jobs/{identity}")
            if job["status"] in {"COMPLETED", "FAILED", "CANCELED"}:
                return job
            time.sleep(2)
        raise TimeoutError(f"Job {identity} did not finish: {job}")

    try:
        assert api("/health")["status"] == "ok"
        mp3 = ("speech.mp3", args.mp3.read_bytes())
        mp4 = ("speech.mp4", args.mp4.read_bytes())
        first = upload([mp3])[0]
        second = upload([mp4])[0]
        batch = upload([mp3, mp4, ("invalid.wav", b"invalid media")])
        jobs = [wait(identity) for identity in [first, second, *batch]]
        for job in jobs[:-1]:
            assert job["status"] == "COMPLETED", job
            assert job["segments"] and any(s["text"].strip() for s in job["segments"]), job
            assert all(0 <= s["start_ms"] < s["end_ms"] for s in job["segments"])
        assert jobs[-1]["status"] == "FAILED", jobs[-1]
        evidence["real_transcriptions"] = [{"id": j["id"], "segments": len(j["segments"]), "speech": j["segments"][0]["text"]} for j in jobs[:-1]]
        for identity in (first, second):
            status, content, _ = request(f"/api/jobs/{identity}/media", headers={"Range": "bytes=0-31"})
            assert status == 206 and len(content) == 32
        segment = jobs[0]["segments"][0]
        edited = segment["text"] + " [integration edit]"
        saved = api(f"/api/jobs/{first}/segments/{segment['id']}", "PATCH", {"text": edited})
        assert saved["start_ms"] == segment["start_ms"] and saved["end_ms"] == segment["end_ms"]
        assert api(f"/api/jobs/{first}")["segments"][0]["text"] == edited
        for format in ("txt", "srt"):
            _, content, _ = request(f"/api/jobs/{first}/export?format={format}")
            text = content.decode()
            assert edited in text
            if format == "srt":
                assert re.search(r"\d{2,}:\d{2}:\d{2},\d{3} --> \d{2,}:\d{2}:\d{2},\d{3}", text)
        api(f"/api/jobs/{batch[-1]}/retry", "POST")
        assert wait(batch[-1])["status"] == "FAILED"
        evidence["checks"] = ["single MP3", "single MP4", "mixed batch isolation", "real speech and timestamps", "original media range responses", "saved edit", "TXT/SRT edited exports", "retry failed job"]
    finally:
        for identity in created:
            try:
                job = api(f"/api/jobs/{identity}")
                if job["status"] in {"QUEUED", "PROCESSING"}:
                    api(f"/api/jobs/{identity}/cancel", "POST")
                status, _, _ = request(f"/api/jobs/{identity}", "DELETE")
                assert status == 204
            except Exception as exc:
                print(f"Cleanup failed for {identity}: {exc}")
                raise
    evidence["deleted_test_jobs"] = len(created)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
