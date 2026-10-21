"""Merge complete, disjoint native cook batches for one exact USD snapshot."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "batches", "out", "receipt"):
        parser.add_argument("--"+name, required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists() or args.receipt.exists():
        raise FileExistsError("Use fresh aggregate output paths")
    snapshot = json.loads(args.snapshot.read_text())
    expected = {m["path"]: m["collision_state_sha256"] for m in snapshot["meshes"]
                if m["collision"] and m["path"].startswith("/World/Robot/")}
    manifest = json.loads(args.batches.read_text())
    parts, evidence, cooker_hashes, experience_hashes = {}, [], set(), set()
    for batch in manifest["batches"]:
        paths = {key: args.batches.parent/batch[key] for key in ("data", "receipt")}
        if any(sha(paths[key]) != batch[key+"_sha256"] for key in paths):
            raise ValueError("Batch manifest hash mismatch")
        receipt = json.loads(paths["receipt"].read_text())
        data = json.loads(gzip.decompress(paths["data"].read_bytes()))
        if (receipt["status"] != "completed" or receipt.get("physics_timeline_played") is not False
                or receipt.get("viewport_updates_enabled") is not False
                or receipt["output_sha256"] != sha(paths["data"])
                or receipt["candidate_sha256"] != snapshot["usd_sha256"]
                or data["candidate_sha256"] != snapshot["usd_sha256"]
                or data["snapshot_sha256"] != sha(args.snapshot)
                or receipt["snapshot_sha256"] != sha(args.snapshot)
                or receipt["memory_guard"]["minimum_free_gib"] < 1
                or data["vertex_frame"] != "rigid-body-local"
                or not data["identity_mesh_to_body_transform_verified"]
                or data["total_candidate_meshes"] != len(expected)):
            raise ValueError("Invalid or inconsistent completed batch")
        selected = receipt["selected_mesh_paths"]
        if (len(selected) != len(set(selected)) or data["selected_mesh_paths"] != selected
                or {m["path"] for m in data["meshes"]} != set(selected)
                or len(data["meshes"]) != len(selected)
                or receipt["mesh_count"] != len(selected)):
            raise ValueError("Batch output does not match its selected collider set")
        for part in data["meshes"]:
            path = part["path"]
            if (path in parts or path not in expected
                    or part["snapshot_collision_fingerprint"] != expected[path]
                    or not part["hulls"]):
                raise ValueError("Duplicate, unexpected, changed or empty collider")
            parts[path] = part
        cooker_hashes.add(receipt["script_sha256"])
        experience_hashes.add(receipt.get("experience_sha256"))
        evidence.append(batch)
    if set(parts) != set(expected) or len(cooker_hashes) != 1 or len(experience_hashes) != 1:
        raise ValueError("Missing colliders or inconsistent cooker versions")
    data = {
        "schema_version": 1, "candidate_sha256": snapshot["usd_sha256"],
        "snapshot_sha256": sha(args.snapshot), "vertex_frame": "rigid-body-local",
        "identity_mesh_to_body_transform_verified": True, "partial_batch": False,
        "meshes": [parts[path] for path in expected],
        "native_batch_evidence": evidence, "batch_manifest_sha256": sha(args.batches),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(gzip.compress(json.dumps(data, sort_keys=True, allow_nan=False).encode(),
                                     mtime=0))
    receipt = {
        "schema_version": 1, "kind": "physx_convex_cooking_merged_batches",
        "status": "completed", "candidate_sha256": snapshot["usd_sha256"],
        "snapshot_sha256": sha(args.snapshot), "output_sha256": sha(args.out),
        "mesh_count": len(parts), "hull_count": sum(len(p["hulls"]) for p in parts.values()),
        "script_sha256": sha(Path(__file__)), "native_cooker_sha256": cooker_hashes.pop(),
        "experience_sha256": experience_hashes.pop(),
        "native_batch_evidence": evidence, "batch_manifest_sha256": sha(args.batches),
        "physics_timeline_played": False, "physics_accepted": False,
    }
    args.receipt.write_text(json.dumps(receipt, indent=2)+"\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
