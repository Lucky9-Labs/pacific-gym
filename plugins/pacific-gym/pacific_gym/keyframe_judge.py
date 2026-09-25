"""Pair-gated local visual judgments for FLUX references and Blender frames.

Only a complete, hash-verified candidate with the same frame ID and exact
timestamp as an immutable reference is sent to local Ollama. The result is
advisory visual correspondence, not physics or gait acceptance.
"""

import base64
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .compare import get_json, post_json

DEFAULT_MODEL = "hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M"
PINNED_MODEL_DIGEST = "4e3daebe4fb42e458b26090e6b5e6b38d34ddd1ff9f0673565d83acb3a2c5682"
TIME_TOLERANCE_SECONDS = 1e-6

PROMPT = """You are a cautious visual judge comparing TWO synchronized animation frames.
Image 1 is the ACTUAL TARGET: a sampled FLUX reference frame. Image 2 is the
SYNTHETIC CANDIDATE: the Blender-rendered articulated robot at the SAME frame id
and timestamp. Judge only visible pose correspondence at this timestamp: overall
body orientation/height, relative limb bends and phase, foot-ground contact, and
character identity. Do not infer motion between these two images, physics quality,
or unseen joint angles. A similar-looking mech is not automatically a pose match.
If camera/framing differs enough to prevent pose comparison, or evidence is
ambiguous, use verdict "uncertain" and say why. Return only JSON with exactly:
{"verdict":"match|partial|mismatch|uncertain","evidence":"specific visible comparison","discrepancies":["..."],"next_steer":"one concise Blender animation edit instruction or inspect request","confidence":"high|medium|low"}
No numeric score. Do not claim that one image is walking or stable by itself."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_references(manifest_path: Path) -> list[dict]:
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text())
    dimensions = manifest.get("dimensions")
    if not isinstance(dimensions, list) or len(dimensions) != 2 or not all(isinstance(x, int) and x > 0 for x in dimensions):
        raise ValueError("Reference manifest must declare positive image dimensions")
    if not isinstance(manifest.get("frames"), list) or not manifest["frames"]:
        raise ValueError("Reference manifest has no frames")
    result = []
    seen = set()
    for frame in manifest["frames"]:
        name = Path(frame["path"]).name
        frame_id = Path(name).stem
        timestamp = frame.get("timestamp_seconds")
        expected = frame.get("sha256")
        if frame_id in seen or not re.fullmatch(r"frame-[0-9]{3,}", frame_id):
            raise ValueError(f"Invalid or duplicate frame ID: {frame_id}")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError(f"Invalid timestamp for {frame_id}")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"Invalid SHA-256 for {frame_id}")
        path = (manifest_path.parent / name).resolve()
        if path.parent != manifest_path.parent or not path.is_file() or _sha256(path) != expected:
            raise ValueError(f"Missing or hash-mismatched immutable reference: {path}")
        with Image.open(path) as image:
            if image.size != tuple(dimensions):
                raise ValueError(f"Unexpected reference dimensions for {frame_id}: {image.size}")
        result.append({"frame_id": frame_id, "timestamp_seconds": float(timestamp), "path": path,
                       "sha256": expected, "dimensions": tuple(dimensions)})
        seen.add(frame_id)
    return result


def publish_candidate(manifest_path: Path, candidate_dir: Path, frame_id: str, timestamp_seconds: float,
                      image_path: Path, *, test_fixture: bool = False, fixture_note: str | None = None) -> Path:
    refs = {item["frame_id"]: item for item in load_references(manifest_path)}
    if frame_id not in refs:
        raise ValueError(f"Frame ID is not in the reference manifest: {frame_id}")
    ref = refs[frame_id]
    if not math.isfinite(float(timestamp_seconds)) or abs(float(timestamp_seconds) - ref["timestamp_seconds"]) > TIME_TOLERANCE_SECONDS:
        raise ValueError(f"Candidate timestamp does not match {frame_id}: expected {ref['timestamp_seconds']}")
    candidate_dir = Path(candidate_dir).resolve()
    image_path = Path(image_path).resolve()
    if image_path.parent != candidate_dir or image_path.name != f"{frame_id}.png":
        raise ValueError(f"Candidate image must be {candidate_dir / (frame_id + '.png')}")
    if not image_path.is_file() or image_path.stat().st_size == 0:
        raise ValueError(f"Candidate image is missing or empty: {image_path}")
    with Image.open(image_path) as image:
        image.verify()
    with Image.open(image_path) as image:
        if image.size != ref["dimensions"]:
            raise ValueError(f"Candidate dimensions {image.size} do not match reference {ref['dimensions']}")
    sidecar = {"schema_version": 1, "frame_id": frame_id,
               "timestamp_seconds": ref["timestamp_seconds"], "image": image_path.name,
               "sha256": _sha256(image_path), "complete": True}
    if test_fixture:
        sidecar["test_fixture"] = True
        sidecar["fixture_note"] = fixture_note or "Test fixture only; not animation evidence"
    sidecar_path = image_path.with_suffix(".json")
    temporary = sidecar_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(sidecar, indent=2) + "\n")
    os.replace(temporary, sidecar_path)
    return sidecar_path


def ready_candidate(ref: dict, candidate_dir: Path) -> dict | None:
    candidate_dir = Path(candidate_dir).resolve()
    image_path = candidate_dir / f"{ref['frame_id']}.png"
    sidecar_path = candidate_dir / f"{ref['frame_id']}.json"
    if not image_path.is_file() or not sidecar_path.is_file():
        return None
    try:
        sidecar = json.loads(sidecar_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if sidecar.get("schema_version") != 1 or sidecar.get("complete") is not True:
        return None
    if sidecar.get("frame_id") != ref["frame_id"] or sidecar.get("image") != image_path.name:
        return None
    try:
        candidate_timestamp = float(sidecar.get("timestamp_seconds", -999))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(candidate_timestamp) or abs(candidate_timestamp - ref["timestamp_seconds"]) > TIME_TOLERANCE_SECONDS:
        return None
    if not image_path.stat().st_size or sidecar.get("sha256") != _sha256(image_path):
        return None
    try:
        with Image.open(image_path) as image:
            image.verify()
        with Image.open(image_path) as image:
            if image.size != ref["dimensions"] or _right_edge_clipped(image):
                return None
    except Exception:
        return None
    return {"path": image_path, "sha256": sidecar["sha256"],
            "test_fixture": bool(sidecar.get("test_fixture", False)),
            "fixture_note": sidecar.get("fixture_note")}


def _right_edge_clipped(image: Image.Image) -> bool:
    """Reject a render with foreground pixels pressed against the right edge."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    if width < 3 or height < 3:
        return False
    pixels = rgb.load()
    corners = [pixels[0, 0], pixels[width - 1, 0],
               pixels[0, height - 1], pixels[width - 1, height - 1]]
    background = tuple(sorted(c[channel] for c in corners)[len(corners) // 2]
                       for channel in range(3))
    # Ignore the bottom edge, which commonly contains the ground plane. A
    # non-background run on the rightmost two columns indicates a clipped subject.
    for y in range(1, height - 1):
        for x in (width - 1, width - 2):
            if sum(abs(pixels[x, y][channel] - background[channel]) for channel in range(3)) > 48:
                return True
    return False


def verify_model(model: str, host: str) -> dict:
    tags = get_json(f"{host.rstrip('/')}/api/tags")
    installed = next((item for item in tags.get("models", []) if item.get("name") == model), None)
    if installed is None:
        raise RuntimeError(f"Local Ollama model is not installed: {model}")
    if model == DEFAULT_MODEL and installed.get("digest") != PINNED_MODEL_DIGEST:
        raise RuntimeError(f"Pinned LiquidAI digest mismatch: expected {PINNED_MODEL_DIGEST}, got {installed.get('digest')}")
    info = post_json(f"{host.rstrip('/')}/api/show", {"model": model}, timeout=15)
    if "vision" not in info.get("capabilities", []):
        raise RuntimeError(f"Ollama model does not support vision: {model}")
    return {"tag": model, "digest": installed.get("digest"),
            "capabilities": info.get("capabilities", []),
            "ollama_version": get_json(f"{host.rstrip('/')}/api/version").get("version")}


def _make_pair(reference: Path, candidate: Path, out_path: Path) -> bytes:
    with Image.open(reference) as source, Image.open(candidate) as target:
        left = source.convert("RGB")
        right = target.convert("RGB")
    if left.size != right.size:
        raise ValueError("Reference and candidate dimensions differ")
    width, height = left.size
    pair = Image.new("RGB", (2 * width, height + 84), "#18212a")
    pair.paste(left, (0, 84))
    pair.paste(right, (width, 84))
    draw = ImageDraw.Draw(pair)
    font = ImageFont.load_default(size=36)
    draw.text((20, 20), "ACTUAL TARGET — FLUX", font=font, fill="white")
    draw.text((width + 20, 20), "SYNTHETIC CANDIDATE — BLENDER", font=font, fill="white")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pair.save(out_path, format="PNG")
    return out_path.read_bytes()


def judge_pair(ref: dict, candidate: dict, out_dir: Path, model: str, host: str, model_info: dict) -> dict:
    out_dir = Path(out_dir).resolve()
    result_path = out_dir / f"{ref['frame_id']}.json"
    pair_path = out_dir / f"{ref['frame_id']}-pair.png"
    pair_bytes = _make_pair(ref["path"], candidate["path"], pair_path)
    response = post_json(f"{host.rstrip('/')}/api/chat", {
        "model": model, "stream": False, "format": "json",
        "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 350},
        "messages": [{"role": "user", "content": PROMPT,
                      "images": [base64.b64encode(pair_bytes).decode("ascii")]}],
    }, timeout=300)
    parsed = json.loads(response.get("message", {}).get("content", ""))
    if set(parsed) != {"verdict", "evidence", "discrepancies", "next_steer", "confidence"}:
        raise ValueError(f"Unexpected paired-judge fields: {list(parsed)}")
    if parsed["verdict"] not in {"match", "partial", "mismatch", "uncertain"}:
        raise ValueError("Invalid paired-judge verdict")
    if parsed["confidence"] not in {"high", "medium", "low"}:
        raise ValueError("Invalid paired-judge confidence")
    if not isinstance(parsed["evidence"], str) or not parsed["evidence"].strip():
        raise ValueError("Paired-judge evidence must be non-empty text")
    if not isinstance(parsed["discrepancies"], list) or any(not isinstance(x, str) for x in parsed["discrepancies"]):
        raise ValueError("Paired-judge discrepancies must be a list of strings")
    if not isinstance(parsed["next_steer"], str) or not parsed["next_steer"].strip():
        raise ValueError("Paired-judge next_steer must be non-empty text")
    result = {
        "schema_version": 1,
        "status": "pipeline_self_test" if candidate.get("test_fixture") else "paired_judgment",
        "frame_id": ref["frame_id"], "timestamp_seconds": ref["timestamp_seconds"],
        "pair": {
            "actual_target": {"role": "flux_reference_frame", "path": str(ref["path"]), "sha256": ref["sha256"]},
            "synthetic_candidate": {"role": "blender_rendered_animation_frame", "path": str(candidate["path"]),
                                    "sha256": candidate["sha256"], "test_fixture": candidate.get("test_fixture", False),
                                    "fixture_note": candidate.get("fixture_note")},
            "comparison_image": {"path": str(pair_path), "sha256": hashlib.sha256(pair_bytes).hexdigest()},
        },
        "judge_model": model_info, "judge": parsed, "ollama_done_reason": response.get("done_reason"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    temporary = result_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    os.replace(temporary, result_path)
    return result


def _existing_judgment(path: Path, ref: dict) -> dict | None:
    if not path.is_file():
        return None
    try:
        result = json.loads(path.read_text())
        pair = result["pair"]
        valid = (result.get("schema_version") == 1 and
                 result.get("frame_id") == ref["frame_id"] and
                 result.get("timestamp_seconds") == ref["timestamp_seconds"] and
                 pair["actual_target"].get("sha256") == ref["sha256"] and
                 result.get("status") in {"paired_judgment", "pipeline_self_test"})
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        valid = False
        result = None
    if not valid:
        raise ValueError(f"Existing judgment conflicts with current reference {ref['frame_id']}; use a fresh output directory")
    return result


def run_judge(reference_manifest: Path, candidate_dir: Path, out_dir: Path, model: str,
              host: str, *, once: bool, poll_seconds: float = 0.5) -> dict:
    refs = load_references(reference_manifest)
    model_info = verify_model(model, host)
    out_dir = Path(out_dir).resolve()
    emitted = 0
    if not once:
        print(json.dumps({"status": "watching_for_complete_pairs", "reference_frames": len(refs),
                          "model": model, "candidate_dir": str(Path(candidate_dir).resolve()),
                          "result_directory": str(out_dir)}), flush=True)
    while True:
        previous = {ref["frame_id"]: _existing_judgment(out_dir / f"{ref['frame_id']}.json", ref)
                    for ref in refs}
        candidates = {ref["frame_id"]: ready_candidate(ref, candidate_dir) for ref in refs}
        # Treat one animation review as a synchronized batch. Do not let an
        # early frame become a standalone judgment while later timestamps are
        # still rendering or awaiting framing repair.
        batch_ready = all(previous[ref["frame_id"]] is not None or
                          candidates[ref["frame_id"]] is not None for ref in refs)
        if batch_ready:
            for ref in refs:
                candidate = candidates[ref["frame_id"]]
                old_judgment = previous[ref["frame_id"]]
                if old_judgment is not None:
                    if candidate is not None and old_judgment["pair"]["synthetic_candidate"].get("sha256") != candidate["sha256"]:
                        raise ValueError(f"Existing judgment conflicts with current pair {ref['frame_id']}; use a fresh output directory")
                    continue
                result = judge_pair(ref, candidate, out_dir, model, host, model_info)
                emitted += 1
                if not once:
                    print(json.dumps({"judge_ready": result["frame_id"],
                                      "timestamp_seconds": result["timestamp_seconds"],
                                      "status": result["status"], "judge": result["judge"],
                                      "result_path": str(out_dir / f"{result['frame_id']}.json")}), flush=True)
        already = sum((out_dir / f"{ref['frame_id']}.json").is_file() for ref in refs)
        state = {"status": "scan_complete" if once else "watching", "judge_results_emitted": emitted,
                 "completed_pair_judgments": already,
                 "pairs_waiting": len(refs) - already,
                 "batch_ready": batch_ready,
                 "result_directory": str(out_dir)}
        if once:
            return state
        time.sleep(poll_seconds)


def publish_cli(args) -> int:
    path = publish_candidate(args.reference_manifest, args.candidate_dir, args.frame_id,
                             args.timestamp, args.image)
    print(json.dumps({"published_sidecar": str(path)}))
    return 0


def watch_cli(args) -> int:
    if args.interval <= 0:
        raise ValueError("--interval must be positive")
    result = run_judge(args.reference_manifest, args.candidate_dir, args.out_dir, args.model,
                       args.host, once=args.once, poll_seconds=args.interval)
    print(json.dumps(result))
    return 0
