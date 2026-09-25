"""Extract whole-frame timestamped PNGs from one immutable video input."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract(video: Path, out: Path, fps: int = 2) -> dict:
    video = Path(video).expanduser().resolve(strict=True)
    out = Path(out).expanduser().resolve()
    if not video.is_file() or not video.stat().st_size:
        raise ValueError(f"Video is missing or empty: {video}")
    if fps < 1:
        raise ValueError("Frame sampling rate must be a positive integer")
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"Refusing to replace existing keyframes: {out}")
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                    "-vf", f"fps={fps}", str(out / "frame-%03d.png")], check=True)
    frames = []
    dimensions = None
    for index, path in enumerate(sorted(out.glob("frame-*.png"))):
        with Image.open(path) as image:
            if dimensions is None:
                dimensions = list(image.size)
            elif list(image.size) != dimensions:
                raise RuntimeError("Inconsistent frame dimensions")
        frames.append({"timestamp_seconds": index / fps, "path": path.name,
                       "sha256": sha256(path), "bytes": path.stat().st_size})
    if not frames:
        raise RuntimeError("No video frames extracted")
    result = {"schema_version": 1, "source_video": str(video),
              "source_video_sha256": sha256(video), "sample_fps": fps,
              "dimensions": dimensions, "crop": None, "frames": frames}
    (out / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=2)
    args = parser.parse_args()
    result = extract(args.video, args.out, args.fps)
    print(f"FULL_VIDEO_FRAMES_PASS {len(result['frames'])} {result['dimensions']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
