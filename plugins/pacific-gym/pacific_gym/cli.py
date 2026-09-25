"""Small, dependency-free CLI for immutable source inspection."""

import argparse
import hashlib
import json
import os
import struct
import sys
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .env import load_dotenv
from .trace import (TABLE, canonical, cleanup_verification_rows, export_and_verify,
                    known_secrets, redact, specimen, sql_for_run)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def materialize(uri: str, expected: str, cache: Path) -> Path:
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("Expected SHA-256 must be lowercase hex")
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{expected}.glb"
    if target.exists() and sha256(target) == expected:
        return target
    parsed = urlparse(uri)
    if parsed.scheme == "s3":
        versions = parse_qs(parsed.query).get("versionId", [])
        if len(versions) != 1 or not versions[0]:
            raise ValueError("S3 source must include one immutable versionId")
        with tempfile.NamedTemporaryFile(dir=cache, suffix=".glb", delete=False) as tmp:
            temporary = Path(tmp.name)
        try:
            subprocess.run(
                ["aws", "s3api", "get-object", "--bucket", parsed.netloc,
                 "--key", parsed.path.lstrip("/"), "--version-id", versions[0],
                 str(temporary)], check=True, capture_output=True, text=True
            )
            if sha256(temporary) != expected:
                raise ValueError(f"Source hash mismatch for {uri}")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    elif parsed.scheme in ("", "file"):
        source = Path(parsed.path if parsed.scheme == "file" else uri).expanduser()
        if not source.is_file() or sha256(source) != expected:
            raise ValueError(f"Local source missing or hash mismatch: {source}")
        return source
    else:
        raise ValueError(f"Unsupported source scheme: {parsed.scheme}")
    return target


def inspect_glb(path: Path) -> dict:
    with path.open("rb") as file:
        header = file.read(12)
        if len(header) != 12:
            raise ValueError("Truncated GLB header")
        magic, version, length = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2 or length != path.stat().st_size:
            raise ValueError("Invalid GLB 2.0 header or length")
        chunk_header = file.read(8)
        if len(chunk_header) != 8:
            raise ValueError("Missing GLB JSON chunk")
        chunk_length, chunk_type = struct.unpack("<I4s", chunk_header)
        if chunk_type != b"JSON" or chunk_length > length - 20:
            raise ValueError("Invalid GLB JSON chunk")
        data = json.loads(file.read(chunk_length))
    nodes = data.get("nodes", [])
    skins = data.get("skins", [])
    meshes = data.get("meshes", [])
    animations = data.get("animations", [])
    primitives = [p for mesh in meshes for p in mesh.get("primitives", [])]
    weighted = sum(1 for p in primitives if "JOINTS_0" in p.get("attributes", {}) and "WEIGHTS_0" in p.get("attributes", {}))
    mesh_nodes = [node for node in nodes if "mesh" in node]
    skinned_nodes = [node for node in mesh_nodes if "skin" in node]
    return {
        "nodes": len(nodes), "meshes": len(meshes), "mesh_nodes": len(mesh_nodes),
        "skins": [{"name": skin.get("name"), "joints": len(skin.get("joints", []))} for skin in skins],
        "animations": [{"name": clip.get("name"), "channels": len(clip.get("channels", []))} for clip in animations],
        "weighted_primitives": weighted, "skinned_mesh_nodes": len(skinned_nodes),
        "rigid_parented_mesh_nodes": len(mesh_nodes) - len(skinned_nodes),
    }


def inspect(spec_path: Path, out_path: Path, cache: Path) -> dict:
    spec = json.loads(spec_path.read_text())
    if spec.get("schema_version") != 1 or not spec.get("assets"):
        raise ValueError("Invalid source spec")
    roles = [asset["role"] for asset in spec["assets"]]
    if len(set(roles)) != len(roles):
        raise ValueError("Duplicate asset role")
    reports = []
    for asset in spec["assets"]:
        path = materialize(asset["uri"], asset["sha256"], cache)
        reports.append({
            "role": asset["role"], "uri": asset["uri"], "sha256": sha256(path),
            "bytes": path.stat().st_size, "structure": inspect_glb(path)
        })
    result = {"schema_version": 1, "tool_version": __version__, "asset_id": spec.get("asset_id"), "assets": reports}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="pacific-gym")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("inspect", help="Verify and inspect immutable GLB sources")
    command.add_argument("--spec", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--cache", type=Path, default=Path(".pacific-gym/cache"))
    comparison = sub.add_parser("compare", help="Compare a pinned reference PNG with a candidate PNG using Ollama vision")
    comparison.add_argument("--reference", type=Path, required=True)
    comparison.add_argument("--candidate", type=Path, required=True)
    comparison.add_argument("--out", type=Path, required=True)
    comparison.add_argument("--pair-out", type=Path)
    comparison.add_argument("--model", default="hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M")
    comparison.add_argument("--host", default="http://127.0.0.1:11434")
    research = sub.add_parser("nimble-research", help="Caption a local reference and find sourced art, animation, and Isaac Sim guidance")
    research.add_argument("--reference", type=Path, required=True)
    research.add_argument("--out", type=Path, required=True)
    research.add_argument("--model", default="hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M")
    research.add_argument("--host", default="http://127.0.0.1:11434")
    research.add_argument("--clipboard-key", action="store_true", help="Read Nimble API key from macOS clipboard without logging or saving it")
    publish_frame = sub.add_parser("publish-candidate-frame", help="Mark a completed Blender PNG as a timestamped candidate frame")
    publish_frame.add_argument("--reference-manifest", type=Path, required=True)
    publish_frame.add_argument("--candidate-dir", type=Path, required=True)
    publish_frame.add_argument("--frame-id", required=True)
    publish_frame.add_argument("--timestamp", type=float, required=True)
    publish_frame.add_argument("--image", type=Path, required=True)
    judge_frames = sub.add_parser("judge-keyframes", help="Watch for exact FLUX-reference/Blender-candidate pairs and judge them locally")
    judge_frames.add_argument("--reference-manifest", type=Path, required=True)
    judge_frames.add_argument("--candidate-dir", type=Path, required=True)
    judge_frames.add_argument("--out-dir", type=Path, required=True)
    judge_frames.add_argument("--model", default="hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M")
    judge_frames.add_argument("--host", default="http://127.0.0.1:11434")
    judge_frames.add_argument("--interval", type=float, default=0.5)
    judge_frames.add_argument("--once", action="store_true", help="scan once; never infer for missing, incomplete, or mismatched pairs")
    trace_command = sub.add_parser("trace", help="Export a redacted development trace and verify RawTree read-back")
    trace_command.add_argument("--repo-root", type=Path, required=True)
    trace_command.add_argument("--out", type=Path, required=True)
    trace_command.add_argument("--api-key-file", type=Path)
    trace_command.add_argument("--database")
    start_command = sub.add_parser("run-start", help="Start one asset run in this workspace")
    start_command.add_argument("--workspace", type=Path, default=Path.cwd())
    start_command.add_argument("--reference", type=Path, required=True)
    start_command.add_argument("--source", type=Path)
    start_command.add_argument("--rig", type=Path)
    start_command.add_argument("--reference-frame", type=Path, action="append", default=[])
    start_command.add_argument("--style", default="")
    start_command.add_argument("--clipboard-key", action="store_true", help="Start the reference research job using the Nimble key on the macOS clipboard")
    start_command.add_argument("--no-auto-research", action="store_true", help="Create the run without starting research")
    research_start = sub.add_parser("run-research-start", help="Start the run's cultural and industry reference research job")
    research_start.add_argument("--manifest", type=Path, required=True)
    research_start.add_argument("--clipboard-key", action="store_true")
    research_start.add_argument("--model", default="hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M")
    research_start.add_argument("--host", default="http://127.0.0.1:11434")
    research_poll = sub.add_parser("run-research-poll", help="Poll and save the run's reference research handoff")
    research_poll.add_argument("--manifest", type=Path, required=True)
    research_poll.add_argument("--clipboard-key", action="store_true")
    status_command = sub.add_parser("run-status", help="Show the active run and acceptance state")
    status_command.add_argument("--workspace", type=Path, default=Path.cwd())
    candidate_command = sub.add_parser("candidate-add", help="Record a rendered PNG for hook comparison")
    candidate_command.add_argument("--manifest", type=Path, required=True)
    candidate_command.add_argument("--image", type=Path, required=True)
    candidate_command.add_argument("--stage", required=True)
    blocker_command = sub.add_parser("run-block", help="Record a specific blocker and stop this run")
    blocker_command.add_argument("--manifest", type=Path, required=True)
    blocker_command.add_argument("--reason", required=True)
    blocker_command.add_argument("--evidence", required=True, help="Observed command output, receipt path/hash, or environment evidence")
    complete_command = sub.add_parser("run-complete", help="Complete only after both GPU acceptance gates pass")
    complete_command.add_argument("--manifest", type=Path, required=True)
    blender_command = sub.add_parser("blender-derive", help="Run a Blender background derivative with immutable input checks")
    blender_command.add_argument("--manifest", type=Path, required=True)
    blender_command.add_argument("--executable", default="blender")
    blender_command.add_argument("--script", type=Path, required=True)
    blender_command.add_argument("--input", type=Path, action="append", required=True)
    blender_command.add_argument("--output", type=Path, action="append", required=True)
    blender_command.add_argument("--extra-arg", action="append", default=[])
    generation = sub.add_parser("generate-video", help="Submit a fresh BFL image-to-video job into the active run")
    generation.add_argument("--manifest", type=Path, required=True)
    generation.add_argument("--request", type=Path, required=True)
    generation.add_argument("--start-frame", type=Path, required=True)
    keyframes = sub.add_parser("extract-keyframes", help="Extract timestamped whole frames into the active run")
    keyframes.add_argument("--manifest", type=Path, required=True)
    keyframes.add_argument("--video", type=Path, required=True)
    keyframes.add_argument("--fps", type=int, default=2)
    isaac_command = sub.add_parser("isaac-run", help="Run an Isaac Sim scenario adapter and verify GPU walking receipts")
    isaac_command.add_argument("--manifest", type=Path, required=True)
    isaac_command.add_argument("--executable", required=True, help="Isaac Sim python.sh/kit Python executable")
    isaac_command.add_argument("--script", type=Path, required=True)
    isaac_command.add_argument("--usd", type=Path, required=True)
    isaac_command.add_argument("--receipt", type=Path, required=True)
    isaac_command.add_argument("--proof-video", type=Path, required=True)
    isaac_command.add_argument("--extra-arg", action="append", default=[])
    preflight = sub.add_parser("target-preflight", help="Verify Blender, Isaac Sim, GPU, and run paths on the target host")
    preflight.add_argument("--manifest", type=Path, required=True)
    preflight.add_argument("--blender", default="blender")
    preflight.add_argument("--isaac", required=True, help="Isaac Sim python.sh/kit Python executable")
    args = parser.parse_args()
    if args.command == "inspect":
        print(json.dumps(inspect(args.spec, args.out, args.cache), indent=2))
    elif args.command == "compare":
        from .compare import compare
        result = compare(args.reference, args.candidate, args.model, args.host, args.pair_out)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result["comparison"], indent=2))
    elif args.command == "nimble-research":
        from .nimble_research import clipboard_key, configured_key, run
        from .trace import redact
        key = ""
        try:
            key = clipboard_key() if args.clipboard_key else configured_key(Path.cwd())
            if not key or not key.isascii() or any(char.isspace() for char in key):
                raise RuntimeError("NIMBLE_API_KEY_UNAVAILABLE_OR_INVALID")
            result = run(args.reference, args.out, key, args.model, args.host)
        except Exception as error:
            message = redact(str(error), [key])[:500]
            print(f"NIMBLE_RESEARCH_FAILED: {message}", file=sys.stderr)
            return 3
        print(json.dumps({"provider": result["provider"], "request_id": result["nimble_request_id"],
                          "reference_sha256": result["input"]["sha256"],
                          "research_path": str(args.out / "nimble-research.json"),
                          "prompt_path": str(args.out / "flux-prompt-draft.txt"),
                          "report_path": str(args.out / "reference-map.html")}, indent=2))
    elif args.command == "publish-candidate-frame":
        from .keyframe_judge import publish_cli
        return publish_cli(args)
    elif args.command == "judge-keyframes":
        from .keyframe_judge import watch_cli
        return watch_cli(args)
    elif args.command == "trace":
        session_id = os.environ.get("CODEX_SESSION_ID", "")
        thread_id = os.environ.get("CODEX_THREAD_ID", "")
        if not session_id or not thread_id:
            raise RuntimeError("CODEX_SESSION_ID and CODEX_THREAD_ID are required to identify the running Codex agent")
        trace = specimen(args.repo_root, str(uuid.uuid4()), session_id, thread_id)
        safe = redact(trace, known_secrets())
        key = args.api_key_file.read_text().strip() if args.api_key_file else os.environ.get("RAWTREE_API_KEY", "")
        if key:
            try:
                result = export_and_verify(trace, key, database=args.database)
                result["cleanup"] = cleanup_verification_rows(key, result["run_id"], database=args.database)
                if result["cleanup"]["status"] != "verification_rows_removed":
                    result["status"] = "live_round_trip_cleanup_unavailable"
            except Exception as error:
                result = {
                    "status": "live_round_trip_failed",
                    "run_id": safe[0]["run_id"],
                    "table": TABLE,
                    "query": sql_for_run(safe[0]["run_id"]),
                    "row_count": None,
                    "inserted_rows_may_remain": True,
                    "error": redact(str(error), [*known_secrets(), key]),
                    "cleanup": cleanup_verification_rows(key, safe[0]["run_id"], database=args.database),
                }
        else:
            result = {
                "status": "blocked_missing_rawtree_api_key", "run_id": safe[0]["run_id"],
                "table": TABLE, "insert_request_id": None,
                "query_request_id": None, "query": sql_for_run(safe[0]["run_id"]), "returned_rows": [],
                "trace_sha256": hashlib.sha256(canonical(safe).encode()).hexdigest(),
                "row_count": 0,
                "local_checks": ["complete representative event rows", "Codex session and thread identifiers",
                                 "recursive credential redaction", "canonical JSON serialization",
                                 "strict query response comparison"],
                "data_boundary": "Local redaction and serialization only; nothing was transmitted to RawTree.",
            }
        result.update({
            "schema_version": 1,
            "slice": "rawtree-traces",
            "acceptance_command": "sh scripts/accept-rawtree-trace.sh",
            "evidence_source": "representative replay of proof/slice-01/inspect.json",
        })
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(result["status"] + " run_id=" + result["run_id"])
        return 0 if result["status"] == "live_round_trip_passed" else 2
    elif args.command == "run-start":
        from .run import goal_text, start
        result = start(args.workspace, args.reference, args.source, args.rig, args.style, args.reference_frame)
        research_state = result["reference_research"]
        if not args.no_auto_research:
            key = ""
            try:
                if args.clipboard_key:
                    from .nimble_research import clipboard_key
                    key = clipboard_key()
                else:
                    from .nimble_research import configured_key
                    key = configured_key(args.workspace)
                if key:
                    from .nimble_research import start_run_research
                    research_state = start_run_research(Path(result["manifest"]), key,
                                                        os.environ.get("PACIFIC_GYM_VISION_MODEL", "hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M"),
                                                        os.environ.get("PACIFIC_GYM_OLLAMA_HOST", "http://127.0.0.1:11434"))
            except Exception as error:
                from .run import load, save
                from .trace import redact
                latest = load(Path(result["manifest"]))
                latest["reference_research"].update(status="failed", error=redact(str(error), [key])[:500])
                save(latest)
                research_state = latest["reference_research"]
        print(json.dumps({"run_id": result["run_id"], "manifest": result["manifest"],
                          "reference_research": research_state,
                          "goal": goal_text(result)}, indent=2))
    elif args.command in {"run-research-start", "run-research-poll"}:
        from .nimble_research import (clipboard_key, configured_key, poll_run_research,
                                      start_run_research)
        from .run import load
        from .trace import redact
        key = ""
        try:
            workspace = Path(load(args.manifest)["workspace"])
            key = clipboard_key() if args.clipboard_key else configured_key(workspace)
            if not key:
                raise RuntimeError("NIMBLE_API_KEY_UNAVAILABLE_OR_INVALID")
            if args.command == "run-research-start":
                state = start_run_research(args.manifest, key, args.model, args.host)
            else:
                state = poll_run_research(args.manifest, key)
        except Exception as error:
            print(f"REFERENCE_RESEARCH_FAILED: {redact(str(error), [key])[:500]}", file=sys.stderr)
            return 3
        print(json.dumps(state, indent=2))
    elif args.command == "run-status":
        from .run import active_manifest, load
        path = active_manifest(args.workspace)
        print(json.dumps(load(path) if path else {"state": "no_active_run"}, indent=2))
    elif args.command == "candidate-add":
        from .run import add_candidate
        result = add_candidate(args.manifest, args.image, args.stage)
        print("PACIFIC_GYM_CANDIDATE=" + result["id"])
    elif args.command == "run-block":
        from .run import set_blocker
        result = set_blocker(args.manifest, args.reason, args.evidence)
        print(json.dumps({"run_id": result["run_id"], "state": result["state"],
                          "blocker": result["blocker"]}))
    elif args.command == "run-complete":
        from .run import complete
        result = complete(args.manifest)
        print(json.dumps({"run_id": result["run_id"], "state": result["state"]}))
    elif args.command == "blender-derive":
        from .integrations import blender_derive
        result = blender_derive(args.manifest, args.executable, args.script,
                                args.input, args.output, args.extra_arg)
        print(json.dumps(result, indent=2))
    elif args.command == "generate-video":
        from .run import load
        manifest = args.manifest.expanduser().resolve(strict=True)
        run_data = load(manifest)
        if run_data.get("state") != "active":
            raise ValueError("Fresh video generation requires an active run")
        run_dir = manifest.parent.resolve()
        output_dir = run_dir / "generation" / "bfl-video"
        runner = Path(__file__).resolve().parents[1] / "scripts" / "run-flux3-video.py"
        completed = subprocess.run(
            [os.environ.get("PYTHON", "python3"), str(runner), "--request", str(args.request.expanduser().resolve()),
             "--start-frame", str(args.start_frame.expanduser().resolve()), "--out", str(output_dir)],
            check=False,
        )
        if completed.returncode:
            return completed.returncode
        print(json.dumps({"output_directory": str(output_dir), "fresh_job_only": True}))
    elif args.command == "extract-keyframes":
        from .run import load
        manifest = args.manifest.expanduser().resolve(strict=True)
        run_data = load(manifest)
        if run_data.get("state") != "active":
            raise ValueError("Keyframe extraction requires an active run")
        output_dir = manifest.parent.resolve() / "keyframes" / "reference"
        runner = Path(__file__).resolve().parents[1] / "scripts" / "extract-keyframes.py"
        completed = subprocess.run(
            [os.environ.get("PYTHON", "python3"), str(runner), "--video", str(args.video.expanduser().resolve()),
             "--out", str(output_dir), "--fps", str(args.fps)], check=False,
        )
        if completed.returncode:
            return completed.returncode
        print(json.dumps({"manifest": str(output_dir / "manifest.json"), "output_directory": str(output_dir)}))
    elif args.command == "isaac-run":
        from .integrations import isaac_run
        result = isaac_run(args.manifest, args.executable, args.script, args.usd,
                           args.receipt, args.proof_video, args.extra_arg)
        print(json.dumps(result, indent=2))
    elif args.command == "target-preflight":
        from .integrations import environment_check
        print(json.dumps(environment_check(args.manifest, args.blender, args.isaac), indent=2))
    return 0
