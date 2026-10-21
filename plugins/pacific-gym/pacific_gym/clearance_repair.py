"""Build bounded local Boolean alternatives from verified clearance witnesses.

The recipe subtracts an inflated opposing cooked hull in the edited body's
local frame. No USD or geometry is changed here; Blender must enforce all
cumulative source-shape limits, and every result requires a fresh cook/sweep.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial import ConvexHull, HalfspaceIntersection

from .clearance_sweep import Kinematics, load_case_witnesses
from .solid_volume import mesh_volume


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == ".gz" else data)


def inflated_hull(hull, inflation):
    planes = np.asarray(hull["planes"], float).copy()
    planes /= np.linalg.norm(planes[:, :3], axis=1)[:, None]
    planes[:, 3] -= inflation
    center = np.asarray(hull["vertices"], float).mean(0)
    if np.max(planes[:, :3] @ center+planes[:, 3]) >= 0:
        raise ValueError("Inflated cutter has no verified interior point")
    points = HalfspaceIntersection(planes, center).intersections
    convex = ConvexHull(points)
    faces = convex.simplices.copy()
    for index, face in enumerate(faces):
        a, b, c = points[face]
        if np.cross(b-a, c-a) @ convex.equations[index, :3] < 0:
            faces[index] = face[::-1]
    if not np.isfinite(points).all() or convex.volume <= 0:
        raise ValueError("Invalid inflated cutter")
    return points, faces


def group_pair_cutters(operations):
    """One collection Boolean per mesh pair, containing all observed cutters."""
    groups = {}
    for operation in operations:
        key = tuple(operation["pair"])
        group = groups.setdefault(key, {
            "pose": "all_observed_pair_poses", "q_deg": None, "pair": list(key),
            "observed_poses": set(), "alternatives": {}, "witness_count": 0,
            "estimated_minimum_axis_escape_m": 0.,
        })
        group["observed_poses"].add(operation["pose"])
        group["witness_count"] += 1
        group["estimated_minimum_axis_escape_m"] = max(
            group["estimated_minimum_axis_escape_m"],
            operation["estimated_minimum_axis_escape_m"])
        for alternative in operation["alternatives"]:
            row = group["alternatives"].setdefault(alternative["collider"], {
                key: alternative[key] for key in ("collider", "node", "body", "opponent")
            })
            cutters = row.setdefault("cutters", {})
            cutter = {key: alternative[key] for key in
                      ("opponent_hull", "cutter_vertices_body", "cutter_faces")}
            identity = json.dumps(cutter, sort_keys=True)
            cutters.setdefault(identity, cutter)
    result = []
    for group in groups.values():
        group["observed_poses"] = sorted(group["observed_poses"])
        group["alternatives"] = list(group["alternatives"].values())
        for alternative in group["alternatives"]:
            alternative["cutters"] = list(alternative["cutters"].values())
        result.append(group)
    return sorted(result, key=lambda row: (-row["estimated_minimum_axis_escape_m"], row["pair"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "cooked", "cook-receipt", "sweep", "geometry",
                 "source-geometry", "config", "out"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--extra-margin-m", type=float, default=0.00025)
    parser.add_argument("--group-pair-cutters", action="store_true",
                        help="One collection Boolean per pair using every observed cutter")
    parser.add_argument("--prefer-edit-node", type=int, action="append", default=[],
                        help="Prefer these source nodes among shape-qualified alternatives")
    parser.add_argument("--maximum-collider-source-distance-m", type=float,
                        help="Optional additional collider-only sampled distance cap")
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    if not np.isfinite(args.extra_margin_m) or args.extra_margin_m < 0:
        raise ValueError("The additional cutter reserve must be finite and nonnegative")
    files = {key: path for key, path in vars(args).items()
             if isinstance(path, Path) and key != "out"}
    hashes = {key: sha(path) for key, path in files.items()}
    d = {key: load(path) for key, path in files.items()}
    snapshot, cooked, sweep = d["snapshot"], d["cooked"], d["sweep"]
    candidate = snapshot["usd_sha256"]
    if any(d[key]["candidate_sha256"] != candidate for key in
           ("cooked", "cook_receipt", "sweep", "geometry")):
        raise ValueError("Candidate identity mismatch")
    if (d["cook_receipt"]["status"] != "completed"
            or d["cook_receipt"]["output_sha256"] != hashes["cooked"]
            or cooked["snapshot_sha256"] != hashes["snapshot"]
            or sweep["evidence_sha256"]["snapshot"] != hashes["snapshot"]
            or sweep["evidence_sha256"]["cooked"] != hashes["cooked"]
            or sweep["evidence_sha256"]["config"] != hashes["config"]
            or sweep["status"] != "completed"):
        raise ValueError("Incomplete or changed cook/sweep evidence")
    if d["source_geometry"]["source_sha256"] != d["config"]["source_sha256"]:
        raise ValueError("Source/config mismatch")
    model = Kinematics(snapshot)
    originals = {m["node"]: m for m in d["source_geometry"]["meshes"]}
    if set(args.prefer_edit_node)-set(originals):
        raise ValueError("Preferred edit node is absent from the immutable source")
    authored = {m["path"]: m for m in d["geometry"]["meshes"]}
    parts = {m["path"]: m for m in cooked["meshes"]}
    limits = d["config"]["clearance"]
    target = limits["target_surface_clearance_m"]
    # A cooking approximation can bridge a correctly cut source surface. Allow
    # a declared reserve for that measured error without changing the acceptance
    # clearance or any cumulative source-shape gate enforced by Blender.
    maximum_extra_margin = 2*target
    if args.extra_margin_m > maximum_extra_margin:
        raise ValueError("Cutter reserve cannot exceed twice the configured clearance")
    configured_distance = limits.get("maximum_sampled_collision_source_distance_m")
    distance_limit = args.maximum_collider_source_distance_m
    if distance_limit is None:
        distance_limit = configured_distance
    if distance_limit is not None and (not np.isfinite(distance_limit) or distance_limit <= 0):
        raise ValueError("Collider distance cap must be positive and finite")
    if configured_distance is not None and distance_limit > configured_distance:
        raise ValueError("An optional guard cannot loosen a declared collision distance cap")
    baseline, operations, seen = {}, [], set()
    for case_index in sweep["cases"]:
        case = load_case_witnesses(case_index, args.sweep, candidate)
        transforms = model.transforms(case["q_deg"])
        for violation in case["violations"]:
            relative = np.linalg.inv(transforms[violation["body_a"]]) @ transforms[violation["body_b"]]
            identity = (violation["collider_a"], violation["collider_b"],
                        violation["hull_a"], violation["hull_b"],
                        tuple(np.round(relative, 10).ravel()))
            if identity in seen:
                continue
            seen.add(identity)
            alternatives = []
            for edit_side, other_side in (("a", "b"), ("b", "a")):
                edit_path, other_path = (violation["collider_"+s] for s in (edit_side, other_side))
                mesh = authored[edit_path]
                source = originals[mesh["node"]]
                if edit_path not in baseline:
                    try:
                        source_volume = mesh_volume(source["vertices"], source["faces"])
                        current_volume = mesh_volume(mesh["vertices"], mesh["faces"])
                        qualified = source_volume["volume_qualified"] and current_volume["volume_qualified"]
                        row = {"node": mesh["node"], "source_volume_m3": source_volume["volume_m3"],
                               "current_volume_m3": current_volume["volume_m3"],
                               "volume_qualified": qualified}
                    except ValueError as error:
                        row = {"node": mesh["node"], "volume_qualified": False, "reason": str(error)}
                    row["protected"] = bool(source.get("protected_contact_geometry"))
                    baseline[edit_path] = row
                if baseline[edit_path]["protected"] or not baseline[edit_path]["volume_qualified"]:
                    continue
                if not np.allclose(mesh["mesh_world_matrix"], mesh["body_world_matrix"], atol=1e-8, rtol=0):
                    raise ValueError("Nonidentity collider/body frame needs an explicit adapter")
                other = parts[other_path]
                hull = next(h for h in other["hulls"] if h["index"] == violation["hull_"+other_side])
                points, faces = inflated_hull(hull, target+args.extra_margin_m)
                matrix = np.linalg.inv(transforms[mesh["body"]]) @ transforms[other["body"]]
                points = points @ matrix[:3, :3].T+matrix[:3, 3]
                alternatives.append({
                    "collider": edit_path, "node": mesh["node"], "body": mesh["body"],
                    "opponent": other_path, "opponent_hull": hull["index"],
                    "cutter_vertices_body": points.tolist(), "cutter_faces": faces.tolist(),
                })
            operations.append({
                "pose": case["label"], "q_deg": case["q_deg"],
                "pair": sorted([violation["collider_a"], violation["collider_b"]]),
                "witness_distance_upper_bound_m": violation["distance_upper_bound_m"],
                "estimated_minimum_axis_escape_m": min(
                    proposal["estimated_axis_escape_m"] for proposal in violation["repair_proposals"]),
                "alternatives": alternatives,
            })
    operations.sort(key=lambda row: (-row["estimated_minimum_axis_escape_m"], row["pose"], row["pair"]))
    if args.group_pair_cutters:
        if sweep.get("witness_scope") != "Every failing hull pair":
            raise ValueError("Grouped repair requires an all-witness sweep")
        operations = group_pair_cutters(operations)
    ledger = d["geometry"].get("repair_iteration_ledger")
    if ledger is None:
        if any(row["volume_qualified"] and abs(
                row["current_volume_m3"]/row["source_volume_m3"]-1) > 1e-5
               for row in baseline.values()):
            raise ValueError("Previously repaired geometry requires its cumulative iteration ledger")
        ledger = {"baseline_candidate_sha256": candidate, "pair_attempts": []}
    for row in ledger["pair_attempts"]:
        if not isinstance(row["attempts"], int) or not 0 <= row["attempts"] <= limits["maximum_iterations_per_pair"]:
            raise ValueError("Invalid inherited per-pair iteration count")
    report = {
        "schema_version": 1, "candidate_sha256": candidate,
        "source_sha256": d["config"]["source_sha256"], "inputs_sha256": hashes,
        "script_sha256": sha(Path(__file__)), "status": "recipe_requires_shape_gated_execution",
        "target_clearance_m": target, "extra_cutter_margin_m": args.extra_margin_m,
        "maximum_extra_cutter_margin_m": maximum_extra_margin,
        "maximum_collision_volume_removed_per_part": limits["maximum_collision_volume_removed_per_part"],
        "maximum_sampled_source_distance_m": distance_limit,
        "spatial_limit_application": (
            "Explicit additional collider distance guard" if args.maximum_collider_source_distance_m is not None
            else "Configured collision distance cap" if configured_distance is not None
            else "Distance reported; configuration declares a collision volume cap only"),
        "maximum_iterations_per_pair": limits["maximum_iterations_per_pair"],
        "operation_scope": "One collection Boolean per pair" if args.group_pair_cutters else "One hull cutter",
        "edit_selection_policy": {
            "preferred_source_nodes": sorted(set(args.prefer_edit_node)),
            "tie_breaker": "Minimum incremental source-volume removal",
            "shape_gates_may_not_be_bypassed": True,
        },
        "repair_iteration_ledger_before": ledger,
        "baseline": baseline, "operations": operations,
        "physics_accepted": False, "clearance_qualified": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"operations": len(operations), "candidate_sha256": candidate,
                      "paths_with_baseline_audit": len(baseline), "out": str(args.out)}))


if __name__ == "__main__":
    main()
