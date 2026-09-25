"""Extract timestamped whole-frame PNGs without cropping the video."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


parser = argparse.ArgumentParser()
parser.add_argument("--video", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--fps", type=int, default=2)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
subprocess.run([
    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
    "-i", str(args.video), "-vf", f"fps={args.fps}",
    str(args.out / "frame-%03d.png"),
], check=True)
frames = []
dimensions = None
for index, path in enumerate(sorted(args.out.glob("frame-*.png"))):
    with Image.open(path) as image:
        if dimensions is None:
            dimensions = list(image.size)
        elif list(image.size) != dimensions:
            raise RuntimeError("Inconsistent frame dimensions")
    frames.append({
        "timestamp_seconds": index / args.fps,
        "path": path.name,
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
    })
if not frames:
    raise RuntimeError("No video frames extracted")
receipt = {
    "schema_version": 1,
    "source_video": str(args.video),
    "source_video_sha256": sha256(args.video),
    "sample_fps": args.fps,
    "dimensions": dimensions,
    "crop": None,
    "frames": frames,
}
(args.out / "manifest.json").write_text(json.dumps(receipt, indent=2) + "\n")
print("FULL_VIDEO_FRAMES_PASS", len(frames), dimensions)
