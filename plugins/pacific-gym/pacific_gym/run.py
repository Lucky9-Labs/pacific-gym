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


def _input(role: str, path: Path) -> dict:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"{role} must be a file: {path}")
    return {"role": role, "path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def start(workspace: Path, reference: Path, source: Path | None = None,
          rig: Path | None = None, style: str = "") -> dict:
    workspace = workspace.resolve(strict=True)
    previous = active_manifest(workspace)
    if previous and load(previous)["state"] == "active":
        raise ValueError(f"An active Pacific Gym run already exists: {previous}")
    inputs = [_input("reference", reference)]
    if source:
        inputs.append(_input("visual_source", source))
    if rig:
        inputs.append(_input("rig_reference", rig))
    run_id = str(uuid.uuid4())
    directory = workspace / ".pacific-gym" / "runs" / run_id
    manifest = directory / "run.json"
    result = {
        "schema_version": 1, "run_id": run_id, "workspace": str(workspace),
        "manifest": str(manifest), "created_at_utc": _now(), "updated_at_utc": _now(),
        "state": "active", "inputs": inputs, "style": style, "candidates": [],
        "checks": [], "blocker": None,
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


def set_blocker(path: Path, reason: str) -> dict:
    if not reason.strip():
        raise ValueError("Blocker reason is required")
    data = load(path)
    data["state"] = "blocked"
    data["blocker"] = reason.strip()
    save(data)
    return data


def goal_text(data: dict) -> str:
    reference = next(item for item in data["inputs"] if item["role"] == "reference")
    return (
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
