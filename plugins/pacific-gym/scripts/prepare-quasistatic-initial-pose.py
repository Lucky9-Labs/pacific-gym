"""Prepare an immutable, zero-velocity configuration before physics attachment."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from pacific_gym import quasistatic_plan, terrain


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "poses", "snapshot", "cooked", "source-geometry", "out"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--initial-clearance-m", type=float, default=.002)
    args = parser.parse_args()
    if args.out.exists() or not 0 <= args.initial_clearance_m <= .005:
        raise ValueError("A fresh output and initial clearance of 0–5 mm are required")
    plan = json.loads(args.plan.read_text())
    poses = json.loads(args.poses.read_text())
    snapshot = json.loads(args.snapshot.read_text())
    source = json.loads(args.source_geometry.read_text())
    with gzip.open(args.cooked, "rt") as stream:
        cooked = json.load(stream)
    if (snapshot["usd_sha256"] != plan["candidate_sha256"]
            or cooked["candidate_sha256"] != plan["candidate_sha256"]
            or poses["candidate_sha256"] != plan["candidate_sha256"]
            or poses["planner_sha256"] != sha(args.plan)
            or plan["snapshot_sha256"] != sha(args.snapshot)
            or plan["cooked_sha256"] != sha(args.cooked)
            or plan["source_geometry_sha256"] != sha(args.source_geometry)
            or plan["model_sha256"] != sha(Path(quasistatic_plan.__file__))
            or plan["terrain_module_sha256"] != sha(Path(terrain.__file__))
            or poses["poses"][0]["q_deg"] != plan["reference_q_deg"]):
        raise ValueError("Candidate, source, plan or frozen planning-module identity mismatch")
    if not all(p["optimizer_success"] and p["bounded_kinematic_pose_found"]
               and p["hard_constraints_satisfied"] for p in plan["poses"]):
        raise ValueError("The plan contains an unconverged or infeasible pose")
    model = quasistatic_plan.Model(snapshot, source)
    model.use_cooked_feet(cooked, sha(args.cooked))
    model.terrain = terrain.TriangleTerrain(snapshot, source)
    for side in ("L", "R"):
        model.set_terrain_placement(side, plan["initial_terrain_placements"][side])
    q = plan["reference_q_deg"]
    transforms = model.transforms(
        np.radians([q[name] for name in model.names]), plan["neutral_pose"]["stance"])
    gaps = {}
    for side in ("L", "R"):
        matrix = transforms[model.foot_paths[side]]
        world = model.foot_vertices[side]@matrix[:3, :3].T+matrix[:3, 3]
        heights, _ = model.terrain.query(world[:, :2])
        gaps[side] = float((world[:, 2]-heights).min())
    raise_z = max(0., args.initial_clearance_m-min(gaps.values()))
    if raise_z > .005:
        raise ValueError("Reference configuration needs more than 5 mm vertical correction")
    for matrix in transforms.values():
        matrix[2, 3] += raise_z
    result = {
        "candidate_sha256": plan["candidate_sha256"],
        "planned_poses_sha256": sha(args.plan),
        "transition_poses_sha256": sha(args.poses),
        "snapshot_sha256": sha(args.snapshot),
        "cooked_sha256": sha(args.cooked),
        "source_geometry_sha256": sha(args.source_geometry),
        "script_sha256": sha(Path(__file__)),
        "model_sha256": plan["model_sha256"],
        "terrain_module_sha256": plan["terrain_module_sha256"],
        "pose_index": 0, "q_deg": q, "root_path": model.root,
        "body_world_matrices": {path: matrix.tolist()
                                for path, matrix in transforms.items()},
        "initial_vertical_clearance_translation_m": raise_z,
        "cooked_foot_local_terrain_gaps_before_translation_m": gaps,
        "phase": "before_simulation_attach_and_first_step",
        "linear_velocity_m_s": [0, 0, 0],
        "angular_velocity_deg_s": [0, 0, 0],
        "joint_velocity_deg_s": {name: 0 for name in q},
        "runtime_root_resets": 0, "physics_accepted": False,
    }
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"initial_pose_sha256": sha(args.out),
                      "initial_vertical_clearance_translation_m": raise_z}))


if __name__ == "__main__":
    main()
