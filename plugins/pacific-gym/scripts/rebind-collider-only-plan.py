"""Reuse a kinematic plan only after proving collider-only candidate equivalence."""

import argparse
import copy
import gzip
import hashlib
import json
from pathlib import Path

from pacific_gym.terrain import TriangleTerrain, digest


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix == ".gz"
                      else path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "poses", "old-snapshot", "old-cooked", "new-snapshot",
                 "new-cooked", "source-geometry", "materialization", "out", "out-poses"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--settings-receipt", type=Path, action="append", default=[],
                        help="Ordered, audited collider-cooking changes after materialization")
    parser.add_argument("--capacity-receipt", type=Path, action="append", default=[],
                        help="Audited GPU aggregate-pair buffer increases after cooking settings")
    args = parser.parse_args()
    if args.out.exists() or args.out_poses.exists():
        raise FileExistsError("Use fresh plan and pose-set outputs")
    plan, poses, old, new, old_cook, new_cook, source, materialization = map(
        read, (args.plan, args.poses, args.old_snapshot, args.new_snapshot,
               args.old_cooked, args.new_cooked, args.source_geometry, args.materialization))
    old_hash, new_hash = old["usd_sha256"], new["usd_sha256"]
    chain_hash = materialization["candidate_sha256"]
    for path in args.settings_receipt:
        settings = read(path)
        if (settings["status"] != "settings_changed_requires_fresh_cook"
                or settings["base_candidate_sha256"] != chain_hash
                or not settings["all_other_usd_content_preserved"]
                or not settings["changes"]
                or any(not change["attribute"].startswith("physxConvexDecompositionCollision:")
                       or change["path"].startswith(("/World/Robot/footL/", "/World/Robot/footR/"))
                       for change in settings["changes"])):
            raise ValueError("Cooking-settings lineage is not an audited non-foot change")
        chain_hash = settings["candidate_sha256"]
    for path in args.capacity_receipt:
        capacity = read(path)
        previous = capacity["before"]["value"]
        if (capacity["status"] != "capacity_changed_requires_native_validation"
                or capacity["base_candidate_sha256"] != chain_hash
                or not capacity["all_other_usd_content_preserved"]
                or capacity["attribute"] != "physxScene:gpuFoundLostAggregatePairsCapacity"
                or type(capacity["after"]) is not int
                or not 1024 <= capacity["after"] <= 2**24
                or previous is not None and capacity["after"] <= previous):
            raise ValueError("Capacity lineage is not an audited aggregate-pair buffer increase")
        chain_hash = capacity["candidate_sha256"]
    if (old_hash == new_hash
            or plan["candidate_sha256"] != old_hash
            or poses["candidate_sha256"] != old_hash
            or poses["planner_sha256"] != sha(args.plan)
            or old_cook["candidate_sha256"] != old_hash
            or new_cook["candidate_sha256"] != new_hash
            or chain_hash != new_hash
            or materialization["inputs_sha256"]["usd"] != old_hash
            or not materialization["visuals_joints_masses_filters_and_other_colliders_preserved"]
            or not materialization["all_recipe_operations_resolved"]
            or plan["snapshot_sha256"] != sha(args.old_snapshot)
            or plan["cooked_sha256"] != sha(args.old_cooked)
            or plan["source_geometry_sha256"] != sha(args.source_geometry)):
        raise ValueError("Input candidate or plan lineage does not match")
    if (old["bodies"] != new["bodies"] or old["joints"] != new["joints"]
            or old_cook["vertex_frame"] != "rigid-body-local"
            or new_cook["vertex_frame"] != "rigid-body-local"):
        raise ValueError("Kinematic frames, masses, limits or vertex frames changed")
    protected = {"/World/Robot/footL", "/World/Robot/footR"}
    old_feet = {m["path"]: m for m in old_cook["meshes"] if m["body"] in protected}
    new_feet = {m["path"]: m for m in new_cook["meshes"] if m["body"] in protected}
    if not old_feet or old_feet != new_feet:
        raise ValueError("Exact native cooked foot geometry or settings changed")
    old_terrain, new_terrain = TriangleTerrain(old, source), TriangleTerrain(new, source)
    if (old_terrain.geometry_sha256 != new_terrain.geometry_sha256
            or plan["terrain_world_geometry_sha256"] != new_terrain.geometry_sha256):
        raise ValueError("World-space terrain geometry changed")
    if (plan.get("status") not in {
            "constrained_trajectory_complete_requires_clearance",
            "kinematically_feasible_requires_clearance_and_runtime",
            "feasible_first_step_only"} or not plan["poses"] or not all(
            p["optimizer_success"] and p["bounded_kinematic_pose_found"]
            and p["hard_constraints_satisfied"] for p in plan["poses"])):
        raise ValueError("Cannot reuse an incomplete or infeasible plan")
    replacements = {"candidate_sha256": (old_hash, new_hash),
                    "snapshot_sha256": (sha(args.old_snapshot), sha(args.new_snapshot)),
                    "cooked_sha256": (sha(args.old_cooked), sha(args.new_cooked)),
                    "cooked_feet_sha256": (sha(args.old_cooked), sha(args.new_cooked))}

    def rebind(value):
        if isinstance(value, list):
            return [rebind(v) for v in value]
        if not isinstance(value, dict):
            return value
        result = {k: rebind(v) for k, v in value.items()}
        for key, (before, after) in replacements.items():
            if key in result:
                if result[key] != before:
                    raise ValueError("Unexpected nested evidence hash: "+key)
                result[key] = after
        if "query_sha256" in result:
            if value["query_sha256"] != digest({k: v for k, v in value.items() if k != "query_sha256"}):
                raise ValueError("An original terrain placement has an invalid digest")
            result["query_sha256"] = digest({k: v for k, v in result.items() if k != "query_sha256"})
        return result

    rebound = rebind(plan)
    rebound["collider_only_rebind"] = {
        "script_sha256": sha(Path(__file__)), "source_plan_sha256": sha(args.plan),
        "source_candidate_sha256": old_hash,
        "materialization_sha256": sha(args.materialization),
        "settings_receipt_sha256": [sha(p) for p in args.settings_receipt],
        "capacity_receipt_sha256": [sha(p) for p in args.capacity_receipt],
        "bodies_joints_masses_limits_identical": True,
        "exact_native_cooked_feet_identical": True,
        "terrain_world_geometry_identical": True,
        "fresh_full_body_clearance_required": True}
    rebound["physics_accepted"] = False
    rebound["walking_accepted"] = False
    args.out.write_text(json.dumps(rebound, indent=2, allow_nan=False)+"\n")
    rebound_poses = copy.deepcopy(poses)
    rebound_poses.update(candidate_sha256=new_hash, planner_sha256=sha(args.out),
                         source_pose_set_sha256=sha(args.poses),
                         physics_accepted=False)
    args.out_poses.write_text(json.dumps(rebound_poses, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"candidate_sha256": new_hash, "plan_sha256": sha(args.out),
                      "poses_sha256": sha(args.out_poses), "poses": len(poses["poses"]),
                      "physics_accepted": False}))


if __name__ == "__main__":
    main()
