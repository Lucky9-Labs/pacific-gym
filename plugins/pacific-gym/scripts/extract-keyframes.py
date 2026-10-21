"""Extract whole-frame timestamped PNGs from one immutable video input."""

import argparse
import hashlib
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacific_gym.reference_timing import select_source_frames, verify_extracted_timestamps


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract(video: Path, out: Path, fps: int = 2) -> dict:
    video = Path(video).expanduser().resolve(strict=True)
    out = Path(out).expanduser().resolve()
    if not video.is_file() or not video.stat().st_size:
        raise ValueError(f"Video is missing or empty: {video}")
    if type(fps) is not int or fps < 1:
        raise ValueError("Frame sampling rate must be a positive integer")
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"Refusing to replace existing keyframes: {out}")
    out.mkdir(parents=True, exist_ok=True)
    source_hash = sha256(video)
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "stream=index,time_base:frame=pts,width,height", "-of", "json", str(video),
    ]))
    stream, base, origin, selected = select_source_frames(probe, fps)
    # Explicit decoded-frame indices avoid FPS resampling, duplication and invented times.
    expression = "+".join(f"eq(n\\,{item['decoded_frame_index']})" for item in selected)
    filter_path = out / "extraction-filter.txt"
    filter_path.write_text(f"select='{expression}',showinfo\n")
    completed = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "info", "-n", "-copyts", "-noautorotate",
        "-i", str(video), "-map", f"0:{stream}", "-filter_script:v", str(filter_path),
        "-fps_mode", "passthrough", str(out / "frame-%03d.png"),
    ], capture_output=True, text=True)
    (out / "extraction.log").write_text(completed.stderr)
    completed.check_returncode()
    verify_extracted_timestamps(completed.stderr, selected, base)
    if sha256(video) != source_hash:
        raise ValueError("Source video changed during extraction")
    paths = sorted(out.glob("frame-*.png"), key=lambda p: int(p.stem.split("-")[1]))
    if len(paths) != len(selected):
        raise ValueError("Extracted image count differs from selected source frames")
    frames = []
    dimensions = None
    for path, source in zip(paths, selected):
        with Image.open(path) as image:
            if image.size != (source["width"], source["height"]):
                raise ValueError("Extracted dimensions differ from decoded source frame")
            if dimensions is None:
                dimensions = list(image.size)
            elif list(image.size) != dimensions:
                raise RuntimeError("Inconsistent frame dimensions")
        identity = {"video_sha256": source_hash, "stream_index": stream,
                    "decoded_frame_index": source["decoded_frame_index"],
                    "pts": source["pts"], "time_base": base}
        frames.append({"timestamp_seconds": float((source["pts"] - origin) * Fraction(*base)),
                       "source_frame": identity, "path": path.name,
                       "sha256": sha256(path), "bytes": path.stat().st_size})
    if not frames:
        raise RuntimeError("No video frames extracted")
    result = {"schema_version": 2, "source_video": str(video),
              "source_video_sha256": source_hash, "sample_fps": fps,
              "source_stream_index": stream, "source_time_base": base,
              "timestamp_origin_pts": origin,
              "sampling": "first_source_frame_at_or_after_tick_without_duplication",
              "autorotation_applied": False,
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
