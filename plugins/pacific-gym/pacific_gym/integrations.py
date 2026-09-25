"""Hash-preserving subprocess adapters for Blender and Isaac Sim."""

import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .cli import sha256
from .run import load, record_check


def environment_check(manifest: Path, blender: str, isaac: str) -> dict:
    """Verify target runtimes and the relocated run are usable before doing work."""
    data = load(manifest)
    run_dir = Path(data["manifest"]).parent.resolve()
    if platform.system() not in {"Linux", "Windows"}:
        raise ValueError(f"Isaac Sim GPU runtime requires Linux or Windows; current host is {platform.system()}")
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        raise ValueError("nvidia-smi is missing; Isaac Sim GPU prerequisites are not available")
    gpu = subprocess.run([nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
                         check=False, capture_output=True, text=True, timeout=30)
    names = [line.strip() for line in gpu.stdout.splitlines() if line.strip()]
    if gpu.returncode or not names:
        raise ValueError(f"NVIDIA GPU startup probe failed: {gpu.stderr[-1000:]}")
    if not run_dir.is_dir() or not os.access(run_dir, os.W_OK):
        raise ValueError(f"Relocated run directory is not writable: {run_dir}")
    missing_inputs = [item["path"] for item in data.get("inputs", [])
                      if not Path(item["path"]).is_file()]
    if missing_inputs:
        raise ValueError(f"Run manifest references missing target inputs: {missing_inputs}")
    resolved = {}
    for name, value in (("Blender", blender), ("Isaac Sim", isaac)):
        candidate = Path(value).expanduser()
        executable = shutil.which(value) if not candidate.is_absolute() else str(candidate)
        if not executable or not Path(executable).is_file():
            raise ValueError(f"{name} executable is missing on this target: {value}")
        executable = str(Path(executable).resolve(strict=True))
        if os.name != "nt" and not os.access(executable, os.X_OK):
            raise ValueError(f"{name} executable is not executable: {executable}")
        command = ([executable, "--version"] if name == "Blender" else
                   [executable, "-c", "from isaacsim import SimulationApp; app=SimulationApp({'headless': True}); app.close()"])
        try:
            # Isaac's headless SimulationApp construction exercises Kit startup,
            # extensions, and GPU initialization rather than just Python itself.
            probe = subprocess.run(command, check=False, capture_output=True, text=True,
                                   timeout=120 if name == "Blender" else 600)
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError(f"{name} is present but failed its startup probe: {error}") from error
        output = (probe.stdout + "\n" + probe.stderr).strip()
        if probe.returncode != 0 or (name == "Blender" and not output):
            raise ValueError(f"{name} startup probe failed (exit {probe.returncode}): {output[-1000:]}")
        resolved[name.lower().replace(" ", "_")] = {
            "executable": executable,
            "version": output.splitlines()[0] if output else "headless SimulationApp startup passed",
        }
    return {"manifest": str(Path(manifest).expanduser().resolve(strict=True)),
            "run_directory": str(run_dir), "runtimes": resolved,
            "gpu_names": names, "ready": True}


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _receipt(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def blender_derive(manifest: Path, executable: str, script: Path,
                   inputs: list[Path], outputs: list[Path], extra_args: list[str]) -> dict:
    data = load(manifest)
    run_dir = Path(data["manifest"]).parent.resolve()
    derivatives = (run_dir / "derivatives").resolve()
    if not inputs or not outputs:
        raise ValueError("Blender derive requires at least one input and output")
    script = script.expanduser().resolve(strict=True)
    before = []
    for source in inputs:
        source = source.expanduser().resolve(strict=True)
        if not source.is_file() or any(source == out.expanduser().resolve() for out in outputs):
            raise ValueError(f"Invalid or in-place Blender input: {source}")
        before.append({"path": str(source), "sha256": sha256(source), "bytes": source.stat().st_size})
    staged_inputs = []
    stage_dir = run_dir / "inputs"
    stage_dir.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(before):
        staged = stage_dir / f"blender-{index}-{Path(item['path']).name}"
        staged.unlink(missing_ok=True)
        shutil.copyfile(item["path"], staged)
        staged.chmod(0o444)
        if sha256(staged) != item["sha256"]:
            raise ValueError("Blender staging copy does not match the immutable source")
        staged_inputs.append(staged)
    resolved_outputs = [path.expanduser().resolve() for path in outputs]
    if len(set(resolved_outputs)) != len(resolved_outputs) or any(not _inside(path, derivatives) for path in resolved_outputs):
        raise ValueError("Blender outputs must be distinct paths under this run's derivatives directory")
    for path in resolved_outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
    command = [executable, "--background", "--python-exit-code", "1", "--python", str(script), "--",
               "--inputs", *[str(path) for path in staged_inputs], "--outputs",
               *[str(path) for path in resolved_outputs], *extra_args]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    after_inputs = [{**item, "sha256_after": sha256(Path(item["path"]))}
                    for item in before]
    staged_after = [{"path": str(path), "sha256": sha256(path)} for path in staged_inputs]
    if (any(item["sha256"] != item["sha256_after"] for item in after_inputs)
            or any(item["sha256"] != before[index]["sha256"] for index, item in enumerate(staged_after))):
        raise ValueError("Blender modified an immutable input or staging copy; run was stopped")
    if completed.returncode:
        raise RuntimeError(f"Blender exited {completed.returncode}: {completed.stderr[-2000:]}")
    missing = [str(path) for path in resolved_outputs if not path.is_file()]
    if missing:
        raise ValueError(f"Blender did not create declared outputs: {missing}")
    try:
        version = subprocess.run([executable, "--version"], check=True, capture_output=True, text=True).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        version = "unknown"
    receipt = {
        "schema_version": 1, "kind": "blender_derive", "tool_version": version,
        "command": command, "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": after_inputs,
        "staged_inputs": staged_after,
        "outputs": [{"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}
                    for path in resolved_outputs],
        "exit_code": completed.returncode, "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }
    receipt_path = run_dir / "receipts" / f"blender-{len(data['checks']) + 1}.json"
    _receipt(receipt_path, receipt)
    receipt["receipt_path"] = str(receipt_path)
    receipt["receipt_sha256"] = sha256(receipt_path)
    data["checks"].append(receipt)
    from .run import save
    save(data)
    return receipt


def isaac_run(manifest: Path, executable: str, script: Path, usd: Path,
              receipt_path: Path, proof_path: Path, extra_args: list[str]) -> dict:
    data = load(manifest)
    run_dir = Path(data["manifest"]).parent.resolve()
    host_os = platform.system()
    if host_os not in {"Linux", "Windows"}:
        raise ValueError(f"Isaac Sim walking requires a supported Linux or Windows GPU host; current host is {host_os}")
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        raise ValueError("No nvidia-smi executable is available; a supported NVIDIA GPU host is required")
    gpu_probe = subprocess.run([nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
                               check=False, capture_output=True, text=True)
    if gpu_probe.returncode:
        raise ValueError(f"nvidia-smi GPU query failed: {gpu_probe.stderr[-1000:]}")
    detected_gpus = [line.strip() for line in gpu_probe.stdout.splitlines() if line.strip()]
    if not detected_gpus:
        raise ValueError("nvidia-smi did not report a GPU")
    usd = usd.expanduser().resolve(strict=True)
    script = script.expanduser().resolve(strict=True)
    receipt_path = receipt_path.expanduser().resolve()
    proof_path = proof_path.expanduser().resolve()
    if not usd.is_file() or usd.suffix.lower() not in {".usd", ".usda", ".usdc"}:
        raise ValueError("Isaac Sim input must be an existing USD file")
    if proof_path.suffix.lower() not in {".mp4", ".webm"} or proof_path == receipt_path:
        raise ValueError("Isaac Sim walking proof must be a distinct MP4 or WebM file")
    if not _inside(receipt_path, run_dir) or not _inside(proof_path, run_dir):
        raise ValueError("Isaac Sim receipt and video proof must stay inside this run")
    input_hash = sha256(usd)
    staged_usd = run_dir / "inputs" / f"isaac-{usd.name}"
    staged_usd.parent.mkdir(parents=True, exist_ok=True)
    staged_usd.unlink(missing_ok=True)
    shutil.copyfile(usd, staged_usd)
    staged_usd.chmod(0o444)
    if sha256(staged_usd) != input_hash:
        raise ValueError("Isaac Sim staging copy does not match the immutable USD source")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    proof_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.unlink(missing_ok=True)
    command = [executable, str(script), "--usd", str(staged_usd), "--receipt", str(receipt_path),
               "--proof", str(proof_path), *extra_args]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Isaac Sim exited {result.returncode}: {result.stderr[-3000:]}")
    if sha256(usd) != input_hash or sha256(staged_usd) != input_hash:
        raise ValueError("Isaac Sim integration modified an immutable USD input or staging copy")
    if not receipt_path.is_file() or not proof_path.is_file():
        raise ValueError("Isaac Sim must create both a structured receipt and video proof")
    if proof_path.stat().st_size == 0:
        raise ValueError("Isaac Sim walking proof video is empty")
    raw = json.loads(receipt_path.read_text())
    walking = raw.get("walking", {})
    gpu = raw.get("gpu", {})
    valid = (
        raw.get("schema_version") == 1
        and raw.get("usd_input_sha256") == input_hash
        and raw.get("usd_articulation_valid") is True
        and raw.get("host_os") == host_os
        and isinstance(gpu.get("name"), str) and gpu["name"].strip() in detected_gpus
        and walking.get("stable_forward") is True
        and walking.get("fall_detected") is False
        and walking.get("invalid_physics_state") is False
        and isinstance(walking.get("completed_cycles"), int) and walking["completed_cycles"] >= 3
        and isinstance(walking.get("net_forward_displacement_m"), (int, float))
        and walking["net_forward_displacement_m"] > 0
        and raw.get("proof_video_sha256") == sha256(proof_path)
    )
    if not valid:
        raise ValueError("Isaac Sim receipt failed the structural, GPU walking, or video-hash acceptance gate")
    check = {
        "schema_version": 1, "kind": "isaac_sim", "command": command,
        "host_os": host_os, "host_arch": platform.machine(), "gpu": gpu,
        "gpu_probe": {"command": [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
                      "reported_names": detected_gpus},
        "usd_input": {"path": str(usd), "sha256": input_hash},
        "staged_usd": {"path": str(staged_usd), "sha256": sha256(staged_usd)},
        "usd_articulation_valid": True, "stable_forward_walking_observed_on_gpu": True,
        "walking": walking, "proof_video": {"path": str(proof_path), "sha256": sha256(proof_path)},
        "integration_receipt": {"path": str(receipt_path), "sha256": sha256(receipt_path)},
        "stdout_tail": result.stdout[-2000:], "stderr_tail": result.stderr[-2000:],
    }
    record_check(manifest, check)
    return check
