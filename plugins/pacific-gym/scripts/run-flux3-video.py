"""Preflight or submit one resumable BFL FLUX 3 Video gait reference run."""

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
from pacific_gym.env import load_dotenv


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def request_json(url, key, payload=None):
    method = "POST" if payload is not None else "GET"
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method, headers={
        "x-key": key, "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read(1500).decode("utf-8", "replace").replace(key, "[REDACTED]")
        raise RuntimeError(f"BFL HTTP {error.code}: {detail}") from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True, help="Generation JSON with endpoint, mode, prompt, and settings")
    parser.add_argument("--start-frame", type=Path, required=True, help="Fresh image-to-video start frame")
    parser.add_argument("--out", type=Path, required=True, help="Run-local output directory")
    args = parser.parse_args()
    load_dotenv()
    request_path = args.request.expanduser().resolve(strict=True)
    spec = json.loads(request_path.read_text())
    start = args.start_frame.expanduser().resolve(strict=True)
    if not start.is_file() or not start.stat().st_size:
        raise RuntimeError("Start frame is missing or empty")
    if not spec["prompt"].strip() or spec["mode"] != "i2v":
        raise RuntimeError("Expected nonempty image-to-video request")
    endpoint = urllib.parse.urlparse(spec.get("endpoint", ""))
    if endpoint.scheme != "https" or not endpoint.hostname:
        raise RuntimeError("Generation endpoint must be an HTTPS URL")
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    request_digest = sha256(request_path)
    start_digest = sha256(start)
    receipt = {
        "endpoint": spec["endpoint"], "model": spec["model"], "mode": spec["mode"],
        "prompt": spec["prompt"], "settings": spec["settings"],
        "request_sha256": request_digest,
        "start_frame": str(start), "start_frame_sha256": start_digest,
    }
    write_json(out / "preflight.json", receipt)
    key = os.getenv("BFL_API_KEY")
    if not key:
        print("BFL_API_KEY_UNAVAILABLE", file=sys.stderr)
        return 3
    state_path = out / "submission.json"
    if state_path.exists():
        submission = json.loads(state_path.read_text())
        if (submission.get("pacific_gym_request_sha256") != request_digest
                or submission.get("pacific_gym_start_frame_sha256") != start_digest):
            raise RuntimeError("Existing run-local BFL job belongs to a different request or start frame")
        print("RESUMING_BFL_JOB", submission["id"])
    else:
        payload = {
            "mode": "i2v", "prompt": spec["prompt"],
            "keyframes": base64.b64encode(start.read_bytes()).decode("ascii"),
            **spec["settings"],
        }
        submission = request_json(spec["endpoint"], key, payload)
        if not submission.get("id") or not submission.get("polling_url"):
            raise RuntimeError("BFL response did not include id and polling_url")
        submission["pacific_gym_request_sha256"] = request_digest
        submission["pacific_gym_start_frame_sha256"] = start_digest
        write_json(state_path, submission)
        print("SUBMITTED_BFL_JOB", submission["id"])
    polling_url = urllib.parse.urlparse(submission["polling_url"])
    if polling_url.scheme != "https" or not polling_url.hostname:
        raise RuntimeError("BFL polling URL is not HTTPS")
    deadline = time.monotonic() + 15 * 60
    while time.monotonic() < deadline:
        result = request_json(submission["polling_url"], key)
        status = result.get("status")
        if status == "Ready":
            sample = result.get("result", {}).get("sample")
            if not sample or urllib.parse.urlparse(sample).scheme != "https":
                raise RuntimeError("Ready response lacks HTTPS video URL")
            video = out / "strokah-flux3-gait.mp4"
            with urllib.request.urlopen(sample, timeout=180) as response, video.open("wb") as target:
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
            write_json(out / "result.json", {
                **receipt, "job_id": submission["id"], "status": status,
                "cost_credits": submission.get("cost"), "video_sha256": sha256(video),
                "video_bytes": video.stat().st_size,
            })
            print("FLUX3_VIDEO_READY", video)
            return 0
        if status in ("Error", "Request Moderated", "Content Moderated"):
            write_json(out / "failure.json", {"job_id": submission["id"], "status": status})
            raise RuntimeError(f"BFL job ended: {status}")
        time.sleep(5)
    print("BFL_JOB_PENDING", submission["id"], file=sys.stderr)
    return 4


if __name__ == "__main__":
    sys.exit(main())
