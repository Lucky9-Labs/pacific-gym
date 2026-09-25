"""Workspace-scoped state for a Pacific Gym asset run."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .cli import sha256


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def active_path(workspace: Path) -> Path:
    return workspace.resolve() / ".pacific-gym" / "active.json"


def active_manifest(workspace: Path) -> Path | None:
    pointer = active_path(workspace)
    if not pointer.is_file():
        return None
    data = json.loads(pointer.read_text())
    manifest = Path(data["manifest"]).resolve()
    if not manifest.is_relative_to(workspace.resolve() / ".pacific-gym" / "runs"):
        raise ValueError("Active manifest is outside this workspace's run directory")
    return manifest


def for_workspace(workspace: Path) -> tuple[Path, dict] | None:
    manifest = active_manifest(workspace)
    if not manifest:
        return None
    return manifest, load(manifest)


def _input(role: str, path: Path) -> dict:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"{role} must be a file: {path}")
    return {"role": role, "path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def start(workspace: Path, reference: Path, source: Path | None = None,
          rig: Path | None = None, style: str = "", reference_frames: list[Path] | None = None) -> dict:
    workspace = workspace.resolve(strict=True)
    previous = active_manifest(workspace)
    if previous and load(previous)["state"] == "active":
        raise ValueError(f"An active Pacific Gym run already exists: {previous}")
    inputs = [_input("reference", reference)]
    if source:
        inputs.append(_input("visual_source", source))
    if rig:
        inputs.append(_input("rig_reference", rig))
    for frame in reference_frames or []:
        entry = _input("reference_frame", frame)
        if Path(entry["path"]).suffix.lower() != ".png":
            raise ValueError("Pinned comparison reference frames must be PNG files")
        inputs.append(entry)
    run_id = str(uuid.uuid4())
    directory = workspace / ".pacific-gym" / "runs" / run_id
    manifest = directory / "run.json"
    result = {
        "schema_version": 1, "run_id": run_id, "workspace": str(workspace),
        "manifest": str(manifest), "created_at_utc": _now(), "updated_at_utc": _now(),
        "state": "active", "inputs": inputs, "style": style, "candidates": [],
        "checks": [], "feedback": [], "blocker": None,
        "acceptance": {
            "usd_articulation_valid": False,
            "stable_forward_walking_observed_on_gpu": False,
        },
    }
    _write(manifest, result)
    _write(active_path(workspace), {"run_id": run_id, "manifest": str(manifest)})
    return result


def load(path: Path) -> dict:
    path = path.expanduser().resolve(strict=True)
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1 or data.get("manifest") != str(path):
        raise ValueError(f"Invalid Pacific Gym run manifest: {path}")
    return data


def save(data: dict) -> None:
    data["updated_at_utc"] = _now()
    _write(Path(data["manifest"]), data)


def add_candidate(path: Path, image: Path, stage: str) -> dict:
    data = load(path)
    if data["state"] != "active":
        raise ValueError("Run is not active")
    entry = _input("candidate", image)
    if Path(entry["path"]).suffix.lower() != ".png":
        raise ValueError("Candidate comparison requires a PNG render")
    entry.update({"id": str(uuid.uuid4()), "stage": stage, "created_at_utc": _now(),
                  "comparison": None})
    data["candidates"].append(entry)
    save(data)
    return entry


def add_feedback(path: Path, candidate_id: str, receipt: dict) -> dict:
    data = load(path)
    candidate = next((item for item in data["candidates"] if item["id"] == candidate_id), None)
    if candidate is None:
        raise ValueError(f"Unknown candidate id: {candidate_id}")
    if candidate["sha256"] != receipt["inputs"][1]["sha256"]:
        raise ValueError("Comparison receipt does not match candidate bytes")
    candidate["comparison"] = receipt
    data["feedback"].append({"candidate_id": candidate_id, "receipt": receipt})
    save(data)
    return candidate


def set_blocker(path: Path, reason: str, evidence: str) -> dict:
    if len(reason.strip()) < 20 or len(evidence.strip()) < 20:
        raise ValueError("A specific blocker reason and supporting evidence are required")
    data = load(path)
    if data["state"] != "active":
        raise ValueError("Run is not active")
    data["state"] = "blocked"
    data["blocker"] = {"reason": reason.strip(), "evidence": evidence.strip()}
    save(data)
    return data


def record_check(path: Path, check: dict) -> dict:
    data = load(path)
    if data["state"] != "active":
        raise ValueError("Run is not active")
    data["checks"].append(check)
    if check.get("kind") == "isaac_sim":
        data["acceptance"]["usd_articulation_valid"] = check.get("usd_articulation_valid") is True
        data["acceptance"]["stable_forward_walking_observed_on_gpu"] = (
            check.get("stable_forward_walking_observed_on_gpu") is True
        )
    save(data)
    return data


def verified_isaac_check(data: dict) -> dict | None:
    acceptance = data.get("acceptance", {})
    if (acceptance.get("usd_articulation_valid") is not True
            or acceptance.get("stable_forward_walking_observed_on_gpu") is not True):
        return None
    for check in reversed(data.get("checks", [])):
        walking = check.get("walking", {})
        gpu = check.get("gpu", {})
        gpu_probe = check.get("gpu_probe", {})
        if not (
            check.get("kind") == "isaac_sim"
            and check.get("usd_articulation_valid") is True
            and check.get("stable_forward_walking_observed_on_gpu") is True
            and check.get("host_os") in {"Linux", "Windows"}
            and isinstance(gpu.get("name"), str) and gpu["name"].strip()
            and gpu["name"] in gpu_probe.get("reported_names", [])
            and walking.get("stable_forward") is True
            and walking.get("fall_detected") is False
            and walking.get("invalid_physics_state") is False
            and isinstance(walking.get("completed_cycles"), int) and walking["completed_cycles"] >= 3
            and isinstance(walking.get("net_forward_displacement_m"), (int, float))
            and walking["net_forward_displacement_m"] > 0
        ):
            continue
        artifacts = [check.get("usd_input"), check.get("staged_usd"),
                     check.get("proof_video"), check.get("integration_receipt")]
        try:
            if all(item and Path(item["path"]).is_file()
                   and sha256(Path(item["path"])) == item["sha256"] for item in artifacts):
                return check
        except (KeyError, OSError, TypeError):
            continue
    return None


def complete(path: Path) -> dict:
    data = load(path)
    if data["state"] != "active":
        raise ValueError("Run is not active")
    if verified_isaac_check(data) is None:
        raise ValueError("Completion requires valid USD articulation and repeated stable forward walking on a supported GPU host")
    data["state"] = "complete"
    save(data)
    return data


def goal_text(data: dict) -> str:
    reference = next(item for item in data["inputs"] if item["role"] == "reference")
    return (
        "Create an explicit Codex task Goal for this user-requested run, with either GPU walking proof "
        "or an evidence-backed blocker as the only completion outcomes. "
        f"Use the Pacific Gym isaac-ready skill to process reference {reference['path']} "
        f"(SHA-256 {reference['sha256']}) in run {data['run_id']}. Preserve every input byte. "
        f"Create versioned derivatives and record evidence in {data['manifest']}. "
        "Continue through visual comparison, Blender derivatives, USD validation, and a real "
        "Isaac Sim run on a supported GPU host. Complete only after the USD articulation validates "
        "and the simulation visibly demonstrates repeated stable forward walking with no fall or "
        "invalid physics state. If required tools, access, inputs, or a defensible route are absent, "
        "record a specific blocker with evidence and stop. Do not claim walking from static renders "
        "or a local USD check."
    )
