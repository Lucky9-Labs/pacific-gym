"""Run guarded native physics, measure walking, and encode its verified frames.

This is an isaac-run scenario adapter. It cannot accept existing traces in lieu
of a new native run. Options and evidence paths come from a frozen run-local
configuration; the ordinary native validator retains its resource/usage gates.
"""

import argparse
import gzip
import hashlib
import json
import os
import platform
import struct
import subprocess
import sys
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("usd", "receipt", "proof", "configuration"):
        parser.add_argument("--"+name, required=True, type=Path)
    parser.add_argument("--configuration-sha256", required=True)
    args = parser.parse_args()
    if platform.system() != "Windows" or platform.node().casefold() != "l-gpc":
        raise ValueError("Native Windows L-GPC is required")
    if sha(args.configuration) != args.configuration_sha256:
        raise ValueError("Frozen scenario configuration changed")
    config = read(args.configuration)
    options = config["native_options"]
    root = Path(options["manifest"]).resolve(strict=True).parent
    if (args.receipt.exists() or args.proof.exists()
            or args.proof.suffix.lower() != ".mp4"
            or any(not p.resolve().is_relative_to(root)
                   for p in (args.receipt, args.proof, args.configuration))):
        raise ValueError("Fresh run-local receipt and MP4 outputs are required")
    candidate = sha(args.usd)
    if candidate != options["usd-sha256"]:
        raise ValueError("isaac-run staged USD differs from the configured candidate")
    required = {"keep-awake", "repeated-cycle-probe", "capture-dir",
                "motion-schedule", "settle-receipt", "cooked", "initial-pose"}
    if not required <= options.keys() or any(
            options.get(k) is not True for k in ("keep-awake", "repeated-cycle-probe")):
        raise ValueError("A captured, calibrated repeated-cycle probe is required")
    input_keys = (
        "snapshot", "initial-pose", "planned-poses", "transition-poses",
        "isolated-sweep", "combined-sweep", "cook-receipt", "cooked",
        "motion-schedule", "settle-receipt", "experience", "capture-module",
        "preflight", "resource-preflight", "usage-gate")
    expected_inputs = {Path(options[key]).resolve(strict=True) for key in input_keys}
    expected_inputs.add(Path(config["source_geometry"]).resolve(strict=True))
    input_hashes = {Path(item["path"]).resolve(strict=True): item["sha256"]
                    for item in config["pinned_inputs"]}
    if expected_inputs != set(input_hashes) or any(
            sha(path) != digest for path, digest in input_hashes.items()):
        raise ValueError("Every native and analysis input must match its pinned hash")
    pinned = {}
    for name in ("native_adapter", "walking_analyzer", "encoder"):
        path = Path(config[name]["path"]).resolve(strict=True)
        if not path.is_relative_to(root) or sha(path) != config[name]["sha256"]:
            raise ValueError("Pinned run-local tool mismatch: "+name)
        pinned[name] = path
    package = Path(config["planning_package"]["path"]).resolve(strict=True)
    if not package.is_relative_to(root):
        raise ValueError("Use a frozen run-local analysis package")
    expected_modules = {"pacific_gym/__init__.py", "pacific_gym/quasistatic_plan.py",
                        "pacific_gym/terrain.py", "pacific_gym/articulation_audit.py"}
    if not expected_modules <= {item["path"] for item in config["planning_package"]["files"]}:
        raise ValueError("Every planning module must be pinned")
    for item in config["planning_package"]["files"]:
        path = (package/item["path"]).resolve(strict=True)
        if not path.is_relative_to(package) or sha(path) != item["sha256"]:
            raise ValueError("Frozen planning package changed")
        input_hashes[path] = item["sha256"]
    native_receipt = Path(options["receipt"])
    samples = Path(options["out"])
    analysis = Path(config["analysis_output"])
    capture_dir = Path(options["capture-dir"])
    for path in (native_receipt, samples, analysis, capture_dir):
        if path.exists() or not path.resolve().is_relative_to(root):
            raise ValueError("Native evidence must use fresh run-local outputs")
    log = args.receipt.with_suffix(".scenario.log")
    if log.exists():
        raise FileExistsError(log)
    child_options = {**options, "usd": str(args.usd)}
    command = [sys.executable, str(pinned["native_adapter"])]
    for key, value in child_options.items():
        if value is True:
            command.append("--"+key)
        elif value is not False and value is not None:
            command.extend(("--"+key, str(value)))
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    with log.open("x", encoding="utf-8") as stream:
        subprocess.run(command, env=environment, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)
        native = read(native_receipt)
        if (native["status"] != "completed" or not native["motion_probe_pass"]
                or not native.get("repeated_cycle_probe")
                or native["candidate_sha256"] != candidate
                or native["script_sha256"] != config["native_adapter"]["sha256"]
                or native["sample_output_sha256"] != sha(samples)):
            raise ValueError("New native repeated-cycle run did not qualify")
        analysis_options = {
            "receipt": native_receipt, "samples": samples,
            "snapshot": options["snapshot"], "cooked": options["cooked"],
            "source-geometry": config["source_geometry"],
            "plan": options["planned-poses"], "motion-schedule": options["motion-schedule"],
            "transition-poses": options["transition-poses"], "out": analysis}
        analysis_command = [sys.executable, str(pinned["walking_analyzer"])]
        for key, value in analysis_options.items():
            analysis_command.extend(("--"+key, str(value)))
        dependency_paths = [Path(p).resolve(strict=True)
                            for p in config.get("analysis_dependency_paths", [])]
        if any(not p.is_dir() for p in dependency_paths):
            raise ValueError("Analysis dependency paths must be existing directories")
        analysis_environment = {
            **environment, "PYTHONPATH": os.pathsep.join(
                [str(package), *map(str, dependency_paths), environment.get("PYTHONPATH", "")])}
        subprocess.run(analysis_command, env=analysis_environment, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)
        measured = read(analysis)
        if (not measured["native_walking_criteria_pass"]
                or measured["candidate_sha256"] != candidate
                or measured["evidence_sha256"]["receipt"] != sha(native_receipt)
                or measured["evidence_sha256"]["samples"] != sha(samples)
                or measured["script_sha256"] != config["walking_analyzer"]["sha256"]):
            raise ValueError("Measured native walking criteria failed")
        capture_path = capture_dir/"capture.json"
        capture = read(capture_path)
        metadata = native["native_capture"]
        frames = capture["frames"]
        fps = metadata["frame_rate"]
        duration = options["seconds"]
        dt = native["physics_step_dt_s"]
        if (metadata["status"] != "captured" or metadata["manifest_sha256"] != sha(capture_path)
                or capture["candidate_sha256"] != candidate or not frames
                or len(frames) != metadata["frame_count"]
                or len(frames) != round(duration*fps)):
            raise ValueError("Complete captured native timeline is required")
        trace = json.loads(gzip.decompress(samples.read_bytes()))
        observations = {round(s["time_s"]/dt): s for s in trace["samples"]}
        if len(observations) != len(trace["samples"]):
            raise ValueError("Duplicate native sample time")
        for i, frame in enumerate(frames):
            if (frame["path"] != f"frame-{i:06d}.png"
                    or sha(capture_dir/frame["path"]) != frame["sha256"]
                    or not frame["native_state_unchanged_by_render"]
                    or abs(frame["simulation_time_s"]-(dt+i/fps)) > 1e-8
                    or frame["physics_step"] != round(1+i/fps/dt)):
                raise ValueError("Native frame identity, state or timing mismatch")
            if i:
                sample = observations[frame["physics_step"]]
                for field, values in (
                        ("native_link_poses_sha256",
                         [v for pose in sample["link_transforms_xyzw"] for v in pose]),
                        ("native_joint_positions_sha256", sample["actual_dof_rad"])):
                    packed = struct.pack("<"+str(len(values))+"f", *values)
                    if frame[field] != hashlib.sha256(packed).hexdigest():
                        raise ValueError("Captured state differs from the measured native trace")
        encoder = str(pinned["encoder"])
        subprocess.run([
            encoder, "-nostdin", "-hide_banner", "-n", "-framerate", str(fps),
            "-i", str(capture_dir/"frame-%06d.png"), "-c:v", "libx264",
            "-crf", "18", "-pix_fmt", "yuv420p", "-threads", "2", "-an", str(args.proof)],
            stdout=stream, stderr=subprocess.STDOUT, check=True)
        subprocess.run([encoder, "-nostdin", "-hide_banner", "-v", "error",
                        "-i", str(args.proof), "-f", "null", "-"],
                       stdout=stream, stderr=subprocess.STDOUT, check=True)
    if (sha(args.usd) != candidate
            or sha(args.configuration) != args.configuration_sha256
            or any(sha(path) != digest for path, digest in input_hashes.items())
            or any(sha(path) != config[name]["sha256"] for name, path in pinned.items())):
        raise ValueError("Pinned scenario inputs changed during execution")
    result = {
        "schema_version": 1, "usd_input_sha256": candidate,
        "usd_articulation_valid": True, "host_os": platform.system(),
        "gpu": {"name": native["gpu_name"]},
        "walking": {
            "stable_forward": True, "fall_detected": native["fall_detected"],
            "invalid_physics_state": native["invalid_physics_state"],
            "completed_cycles": measured["completed_cycles"],
            "net_forward_displacement_m": measured["net_forward_displacement_m"],
            "minimum_root_up_z": measured["minimum_root_up_z"],
            "measurement_receipt_sha256": sha(analysis)},
        "proof_video_sha256": sha(args.proof),
        "evidence_sha256": {
            "configuration": args.configuration_sha256, "native_receipt": sha(native_receipt),
            "native_samples": sha(samples), "capture_manifest": sha(capture_path),
            "walking_analysis": sha(analysis), "scenario_script": sha(Path(__file__)),
            **{name: sha(path) for name, path in pinned.items()}},
        "video_decode_passed": True,
        "scope": "New guarded native simulation and measured walking with complete native video"}
    args.receipt.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
