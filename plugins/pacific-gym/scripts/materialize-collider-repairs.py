"""Materialize a hash-pinned bounded repair result as a new USD candidate."""

import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdGeom, UsdPhysics, Vt


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("usd", "geometry", "repair-report", "out", "receipt"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--usd-sha256", required=True)
    parser.add_argument("--repair-report-sha256", required=True)
    args = parser.parse_args()
    if args.out.exists() or args.receipt.exists():
        raise FileExistsError("Use fresh USD and receipt paths")
    hashes = {k: sha(getattr(args, k)) for k in ("usd", "geometry", "repair_report")}
    data = json.loads(gzip.decompress(args.geometry.read_bytes()))
    report = json.loads(args.repair_report.read_text())
    if (hashes["usd"] != args.usd_sha256 or hashes["repair_report"] != args.repair_report_sha256
            or report["status"] != "completed" or report["base_candidate_sha256"] != hashes["usd"]
            or data["base_candidate_sha256"] != hashes["usd"]
            or report["output_geometry_sha256"] != hashes["geometry"]):
        raise ValueError("Candidate/repair identity mismatch")
    allowed = set(report["changed_colliders"])
    applied = {op["selected_collider"] for op in report["operations"]
               if op["status"] == "local_boolean_applied_requires_recook"}
    if not allowed or allowed != applied or allowed != set(data["changed_colliders"]):
        raise ValueError("No edits, or changed colliders differ from accepted operations")
    for op in report["operations"]:
        if op.get("selected_collider"):
            row = next(row for row in op["alternatives"] if row["collider"] == op["selected_collider"])
            if row["status"] != "shape_gates_passed":
                raise ValueError("Selected operation did not pass source shape gates")
    helper_path = Path(__file__).with_name("assemble-simplified-articulation.py")
    spec = importlib.util.spec_from_file_location("assembly_checks", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    original = Usd.Stage.Open(str(args.usd))
    if not original or len([layer for layer in original.GetUsedLayers()
                            if layer != original.GetSessionLayer()]) != 1:
        raise ValueError("Expected self-contained USD")
    stage = Usd.Stage.Open(original.Flatten())
    if "repair_iteration_ledger_after" in report:
        ledger = report["repair_iteration_ledger_after"]
        if ledger != data.get("repair_iteration_ledger"):
            raise ValueError("Repair report and geometry iteration ledgers differ")
        prior = stage.GetRootLayer().customLayerData.get("pacificGymRepairIterationLedger")
        if prior and json.loads(prior) != report["repair_iteration_ledger_before"]:
            raise ValueError("USD inherited iteration ledger differs from repair report")
        metadata = dict(stage.GetRootLayer().customLayerData)
        metadata["pacificGymRepairIterationLedger"] = json.dumps(ledger, sort_keys=True)
        stage.GetRootLayer().customLayerData = metadata
    signature = helper.preserved_signature(stage, allowed)
    cache, expected = UsdGeom.XformCache(), {}
    for part in data["meshes"]:
        if part["path"] not in allowed:
            continue
        prim = stage.GetPrimAtPath(part["path"])
        if not prim or not prim.IsA(UsdGeom.Mesh) or not prim.HasAPI(UsdPhysics.CollisionAPI):
            raise ValueError("Accepted path is not a collider mesh")
        matrix = np.asarray(cache.GetLocalToWorldTransform(prim)).T
        if not np.allclose(matrix, part["mesh_world_matrix"], rtol=0, atol=1e-10):
            raise ValueError("Repair local coordinate frame changed")
        points = np.asarray(part["vertices"], float)
        if not len(points) or not np.isfinite(points).all():
            raise ValueError("Invalid repaired points")
        mesh = UsdGeom.Mesh(prim)
        mesh.GetPointsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*map(float, p)) for p in points]))
        mesh.GetFaceVertexCountsAttr().Set([len(f) for f in part["faces"]])
        mesh.GetFaceVertexIndicesAttr().Set([i for f in part["faces"] for i in f])
        mesh.GetNormalsAttr().Clear()
        mesh.GetExtentAttr().Set(UsdGeom.PointBased(mesh).ComputeExtent(mesh.GetPointsAttr().Get()))
        expected[part["path"]] = (points, part["faces"])
    if set(expected) != allowed or helper.preserved_signature(stage, allowed) != signature:
        raise ValueError("Repair changed an undeclared property or omitted a collider")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    stage.GetRootLayer().Export(str(args.out))
    restored = Usd.Stage.Open(str(args.out))
    if restored.GetRootLayer().customLayerData != stage.GetRootLayer().customLayerData:
        raise ValueError("Serialization changed repair lineage metadata")
    if helper.preserved_signature(restored, allowed) != signature:
        raise ValueError("Serialization changed an undeclared property")
    errors = []
    for path, (points, faces) in expected.items():
        mesh = UsdGeom.Mesh(restored.GetPrimAtPath(path))
        if (list(mesh.GetFaceVertexCountsAttr().Get()) != [len(f) for f in faces]
                or list(mesh.GetFaceVertexIndicesAttr().Get()) != [i for f in faces for i in f]):
            raise ValueError("Serialized topology changed")
        error = float(np.linalg.norm(np.asarray(mesh.GetPointsAttr().Get())-points, axis=1).max())
        if error > 1e-6:
            raise ValueError("Serialized repaired points changed")
        errors.append(error)
    if any(sha(getattr(args, k)) != value for k, value in hashes.items()):
        raise ValueError("An input changed during materialization")
    receipt = {
        "schema_version": 1, "status": "bounded_repairs_materialized_for_recook",
        "candidate_sha256": sha(args.out), "inputs_sha256": hashes,
        "script_sha256": sha(Path(__file__)), "helper_sha256": sha(helper_path),
        "changed_colliders": sorted(allowed), "preserved_properties_sha256": signature,
        "max_serialized_local_vertex_error_m": max(errors),
        "visuals_joints_masses_filters_and_other_colliders_preserved": True,
        "all_recipe_operations_resolved": report["all_operations_resolved_by_source_boolean"],
        "physics_accepted": False, "clearance_qualified": False,
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, allow_nan=False)+"\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
