"""Preflight or submit one resumable BFL FLUX 3 Video gait reference run."""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


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
        detail = error.read(1500).decode("utf-8", "replace")
        raise RuntimeError(f"BFL HTTP {error.code}: {detail}") from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, default=Path("proof/slice-03/request.json"))
    parser.add_argument("--out", type=Path, default=Path(".pacific-gym/flux3"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--clipboard-key", action="store_true", help="Read BFL key from macOS clipboard without logging it")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    spec = json.loads((root / args.request).read_text())
    start = root / spec["start_frame"]
    render = json.loads((start.parent / "render.json").read_text())
    if sha256(start) != render["views"][start.stem]["sha256"]:
        raise RuntimeError("Start frame hash differs from Blender render receipt")
    if render["source_sha256"] != "65c6ef7de40c529c75c82dad8ab0f5ac13638882b1ef1eddee9a49a89ee42af1":
        raise RuntimeError("Start frame does not trace to pinned visual LOD0")
    if not spec["prompt"].strip() or spec["mode"] != "i2v":
        raise RuntimeError("Expected nonempty image-to-video request")
    out = root / args.out
    out.mkdir(parents=True, exist_ok=True)
    receipt = {
        "endpoint": spec["endpoint"], "model": spec["model"], "mode": spec["mode"],
        "prompt": spec["prompt"], "settings": spec["settings"],
        "start_frame": spec["start_frame"], "start_frame_sha256": sha256(start),
        "source_glb_sha256": render["source_sha256"],
    }
    write_json(out / "preflight.json", receipt)
    print("FLUX3_PREFLIGHT_PASS")
    if not args.live:
        return 0
    archived_proof = root / "proof/slice-03/proof.json"
    if archived_proof.exists():
        proof = json.loads(archived_proof.read_text())
        for candidate in (proof.get("bfl_request", {}), proof.get("heft_candidate", {}), proof.get("industrial_candidate", {})):
            archived_video = root / candidate.get("video", "")
            if (candidate.get("spec_sha256") == sha256(root / args.request)
                    and archived_video.is_file()
                    and sha256(archived_video) == candidate.get("video_sha256")):
                print("FLUX3_ARCHIVED_VIDEO_READY", archived_video)
                return 0
    key = (subprocess.run(["pbpaste"], capture_output=True, text=True, check=True).stdout.strip()
           if args.clipboard_key else os.getenv("BFL_API_KEY"))
    if not key:
        print("BFL_API_KEY_UNAVAILABLE", file=sys.stderr)
        return 3
    state_path = out / "submission.json"
    if state_path.exists():
        submission = json.loads(state_path.read_text())
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
