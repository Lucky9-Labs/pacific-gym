"""Small, dependency-free CLI for immutable source inspection."""

import argparse
import hashlib
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__


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
    parser = argparse.ArgumentParser(prog="pacific-gym")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("inspect", help="Verify and inspect immutable GLB sources")
    command.add_argument("--spec", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--cache", type=Path, default=Path(".pacific-gym/cache"))
    args = parser.parse_args()
    if args.command == "inspect":
        print(json.dumps(inspect(args.spec, args.out, args.cache), indent=2))
    return 0
