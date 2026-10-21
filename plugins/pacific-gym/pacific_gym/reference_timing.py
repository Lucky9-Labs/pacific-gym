"""Exact source-video frame identity; never infer source time from output FPS."""

import math
import re
from fractions import Fraction


def time_base(value):
    if (not isinstance(value, list) or len(value) != 2
            or any(type(n) is not int or n <= 0 for n in value)):
        raise ValueError("Time base must be two positive integers")
    return Fraction(*value)


def select_source_frames(probe, fps):
    """Choose the first decoded frame at/after each sampling tick, without repeats."""
    if type(fps) is not int or fps < 1:
        raise ValueError("Frame sampling rate must be a positive integer")
    streams = probe.get("streams", [])
    frames = probe.get("frames", [])
    if len(streams) != 1 or not frames:
        raise ValueError("Exactly one selected video stream with decoded frames is required")
    stream = streams[0]
    parts = stream["time_base"].split("/")
    base = [int(part) for part in parts]
    tick = time_base(base)
    if type(stream.get("index")) is not int or stream["index"] < 0:
        raise ValueError("Invalid source stream index")
    selected, previous, origin, next_time = [], None, None, None
    for index, frame in enumerate(frames):
        pts = frame.get("pts")
        if type(pts) is not int or (previous is not None and pts < previous):
            raise ValueError("Source frames require real, nondecreasing integer PTS")
        previous = pts
        if origin is None:
            origin, next_time = pts, pts * tick
        instant = pts * tick
        if instant < next_time:
            continue
        selected.append({"decoded_frame_index": index, "pts": pts,
                         "width": frame["width"], "height": frame["height"]})
        bucket = (instant - origin * tick) * fps
        next_time = origin * tick + Fraction(bucket.numerator // bucket.denominator + 1, fps)
    return stream["index"], base, origin, selected


def verify_extracted_timestamps(log, selected, base):
    """Cross-check FFmpeg's emitted-frame timestamps against the ffprobe selection."""
    bases = re.findall(r"config in time_base:\s*(\d+)/(\d+)", log)
    times = re.findall(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:", log)
    if not bases or len(times) != len(selected):
        raise ValueError("Missing or incomplete emitted-frame timestamp evidence")
    output_bases = {Fraction(int(a), int(b)) for a, b in bases}
    if len(output_bases) != 1:
        raise ValueError("Changing filter time bases are unsupported")
    output_base = output_bases.pop()
    source_base = time_base(base)
    for index, ((number, pts), source) in enumerate(zip(times, selected)):
        if int(number) != index or int(pts) * output_base != source["pts"] * source_base:
            raise ValueError("Extracted frame does not match the selected source PTS")


def source_identity(manifest, frame):
    """Validate v2 timing while allowing legacy manifests without provenance claims."""
    version = manifest.get("schema_version", 1)
    if type(version) is not int:
        raise ValueError("Reference manifest version must be an integer")
    if version == 1:
        if "source_frame" in frame:
            raise ValueError("Source identity requires a version 2 reference manifest")
        return None
    if version != 2:
        raise ValueError("Unsupported reference manifest version")
    identity = frame.get("source_frame")
    fields = {"video_sha256", "stream_index", "decoded_frame_index", "pts", "time_base"}
    if not isinstance(identity, dict) or set(identity) != fields:
        raise ValueError("Reference frame has incomplete source identity")
    digest = manifest.get("source_video_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid source video hash")
    if identity["video_sha256"] != digest:
        raise ValueError("Reference frame belongs to a different source video")
    for name in ("stream_index", "decoded_frame_index", "pts"):
        if type(identity[name]) is not int:
            raise ValueError("Source frame indices and PTS must be integers")
    if identity["stream_index"] < 0 or identity["decoded_frame_index"] < 0:
        raise ValueError("Source frame indices must be nonnegative")
    base = time_base(identity["time_base"])
    origin = manifest.get("timestamp_origin_pts")
    if (type(origin) is not int or type(manifest.get("source_stream_index")) is not int
            or identity["time_base"] != manifest.get("source_time_base")
            or identity["stream_index"] != manifest.get("source_stream_index")):
        raise ValueError("Source stream timing does not match the manifest")
    expected = float((identity["pts"] - origin) * base)
    timestamp = frame.get("timestamp_seconds")
    if (type(timestamp) not in (int, float) or not math.isfinite(timestamp)
            or expected < 0 or not math.isclose(timestamp, expected, rel_tol=0, abs_tol=1e-9)):
        raise ValueError("Reference timestamp disagrees with source PTS")
    return dict(identity)
