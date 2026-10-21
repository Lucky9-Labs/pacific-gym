"""Restore source surfaces for colliders rejected by a pinned spatial audit.

This creates a new clearance-search baseline, not an accepted physics asset.
Source world points are explicitly transformed to each USD mesh's local frame.
Coincident closed shells are deduplicated without changing their solid shape.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdGeom, UsdPhysics, Vt

from pacific_gym.solid_volume import closed_components, mesh_volume, same_shell


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("usd", "source-geometry", "shape-audit", "config", "out", "receipt"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--usd-sha256", required=True)
    parser.add_argument("--shape-audit-sha256", required=True)
    args = parser.parse_args()
    if args.out.exists() or args.receipt.exists():
        raise FileExistsError("Use new candidate and receipt paths")
    hashes = {name: sha(getattr(args, name)) for name in
              ("usd", "source_geometry", "shape_audit", "config")}
    source = json.loads(args.source_geometry.read_text())
    audit = json.loads(args.shape_audit.read_text())
    config = json.loads(args.config.read_text())
    if hashes["usd"] != args.usd_sha256 or hashes["shape_audit"] != args.shape_audit_sha256:
        raise ValueError("Pinned candidate/audit changed")
    if (audit["candidate_sha256"] != args.usd_sha256 or audit["status"] != "completed"
            or source["source_sha256"] != config["source_sha256"]
            or audit["source_sha256"] != source["source_sha256"]
            or hashes["source_geometry"] not in audit["input_sha256"]
            or hashes["config"] not in audit["input_sha256"]):
        raise ValueError("Audit/source/config identity mismatch")
    # Reuse the same property-preservation check as visual assembly.
    helper_path = Path(__file__).with_name("assemble-simplified-articulation.py")
    spec = importlib.util.spec_from_file_location("assembly_checks", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    original = Usd.Stage.Open(str(args.usd))
    if not original or len([layer for layer in original.GetUsedLayers()
                            if layer != original.GetSessionLayer()]) != 1:
        raise ValueError("Expected a self-contained USD")
    stage = Usd.Stage.Open(original.Flatten())
    cache = UsdGeom.XformCache()
    originals = {m["node"]: m for m in source["meshes"]}
    rejected = [row for row in audit["rows"] if not row["sampled_forward_distance_pass"]]
    allowed = {row["path"] for row in rejected}
    signature = helper.preserved_signature(stage, allowed)
    rows, expected = [], {}
    for row in rejected:
        src = originals[row["node"]]
        if src.get("protected_contact_geometry") or src["category"] != "robot":
            raise ValueError("A protected collider requires separate review")
        qualified = mesh_volume(src["vertices"], src["faces"])
        if not qualified["volume_qualified"]:
            raise ValueError("Source solid volume is unresolved")
        components = closed_components(src["vertices"], src["faces"])
        unique = []
        for component in components:
            if not any(same_shell(previous, component, 1e-6)[0] for previous in unique):
                unique.append(component)
        points, faces, offset = [], [], 0
        for component in unique:
            points.extend(component["points"])
            faces.extend(component["outward_faces"]+offset)
            offset += len(component["points"])
        points, faces = np.asarray(points), np.asarray(faces, int)
        prim = stage.GetPrimAtPath(row["path"])
        if not prim or not prim.IsA(UsdGeom.Mesh) or not prim.HasAPI(UsdPhysics.CollisionAPI):
            raise ValueError("Audit path is not an authored mesh collider")
        matrix = np.asarray(cache.GetLocalToWorldTransform(prim)).T
        inverse = np.linalg.inv(matrix)
        local = points @ inverse[:3, :3].T+inverse[:3, 3]
        mesh = UsdGeom.Mesh(prim)
        mesh.GetPointsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*map(float, p)) for p in local]))
        mesh.GetFaceVertexCountsAttr().Set([3]*len(faces))
        mesh.GetFaceVertexIndicesAttr().Set(faces.ravel().tolist())
        mesh.GetNormalsAttr().Clear()
        mesh.GetExtentAttr().Set(UsdGeom.PointBased(mesh).ComputeExtent(mesh.GetPointsAttr().Get()))
        expected[row["path"]] = (points, faces)
        rows.append({"node": row["node"], "path": row["path"],
                     "previous_sampled_source_error_m": row["source_to_collider"]["maximum_distance_m"],
                     "source_components": len(components), "unique_source_components": len(unique),
                     "source_solid_volume_m3": qualified["volume_m3"],
                     "source_volume_removed_fraction": 0.,
                     "source_vertices": len(points), "source_triangles": len(faces)})
    if helper.preserved_signature(stage, allowed) != signature:
        raise ValueError("Restoration changed a preserved property")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    stage.GetRootLayer().Export(str(args.out))
    restored = Usd.Stage.Open(str(args.out))
    if helper.preserved_signature(restored, allowed) != signature:
        raise ValueError("Serialization changed a preserved property")
    restored_cache, errors = UsdGeom.XformCache(), []
    for path, (world, faces) in expected.items():
        mesh = UsdGeom.Mesh(restored.GetPrimAtPath(path))
        matrix = np.asarray(restored_cache.GetLocalToWorldTransform(mesh.GetPrim())).T
        points = np.asarray(mesh.GetPointsAttr().Get(), float)
        if (list(mesh.GetFaceVertexCountsAttr().Get()) != [3]*len(faces)
                or not np.array_equal(mesh.GetFaceVertexIndicesAttr().Get(), faces.ravel())):
            raise ValueError("Serialization changed source topology")
        error = float(np.linalg.norm(points @ matrix[:3, :3].T+matrix[:3, 3]-world, axis=1).max())
        if error > config["max_world_rest_translation_error_m"]:
            raise ValueError("Source world-space readback failed")
        errors.append(error)
    if any(sha(getattr(args, name)) != value for name, value in hashes.items()):
        raise ValueError("An input changed during restoration")
    receipt = {
        "schema_version": 1, "status": "source_restored_for_new_clearance_search",
        "candidate_sha256": sha(args.out), "inputs_sha256": hashes,
        "source_sha256": source["source_sha256"], "restored_meshes": rows,
        "max_world_vertex_readback_error_m": max(errors, default=0),
        "preserved_properties_sha256": signature,
        "all_visuals_joint_frames_masses_filters_and_untouched_colliders_preserved": True,
        "script_sha256": sha(Path(__file__)), "helper_sha256": sha(helper_path),
        "solid_volume_module_sha256": sha(Path(__import__(
            "pacific_gym.solid_volume", fromlist=["x"]).__file__)),
        "clearance_qualified": False, "physics_accepted": False,
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, allow_nan=False)+"\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
