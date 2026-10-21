"""Compare a native single-stance probe with its cooked geometry and planned FK."""

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from pacific_gym.quasistatic_plan import Model
from pacific_gym.terrain import TriangleTerrain


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rotation(q):
    x, y, z, w = q
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("receipt", "samples", "snapshot", "cooked", "source-geometry", "plan", "out"):
        parser.add_argument("--"+name, required=True, type=Path)
    parser.add_argument("--motion-schedule", type=Path)
    parser.add_argument("--transition-poses", type=Path)
    parser.add_argument("--after-seconds", type=float, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    receipt, snapshot, source, plan = (
        json.loads(p.read_text()) for p in
        (args.receipt, args.snapshot, args.source_geometry, args.plan))
    trace, cooked = (json.loads(gzip.decompress(p.read_bytes()))
                     for p in (args.samples, args.cooked))
    evidence = receipt["evidence_sha256"]
    if (receipt["sample_output_sha256"] != sha(args.samples)
            or evidence["snapshot"] != sha(args.snapshot)
            or evidence["cooked"] != sha(args.cooked)
            or evidence["planned_poses"] != sha(args.plan)
            or plan["source_geometry_sha256"] != sha(args.source_geometry)
            or len({receipt["candidate_sha256"], trace["candidate_sha256"],
                    snapshot["usd_sha256"], cooked["candidate_sha256"],
                    plan["candidate_sha256"]}) != 1):
        raise ValueError("Native evidence and planning geometry hashes do not match")
    if bool(args.motion_schedule) != bool(args.transition_poses):
        raise ValueError("Schedule and transition poses must be supplied together")
    if args.motion_schedule:
        schedule = json.loads(args.motion_schedule.read_text())
        transitions = json.loads(args.transition_poses.read_text())
        if (evidence["motion_schedule"] != sha(args.motion_schedule)
                or evidence["transition_poses"] != sha(args.transition_poses)
                or schedule["transition_poses_sha256"] != sha(args.transition_poses)
                or transitions["planner_sha256"] != sha(args.plan)
                or schedule["candidate_sha256"] != receipt["candidate_sha256"]
                or transitions["candidate_sha256"] != receipt["candidate_sha256"]):
            raise ValueError("Scheduled prefix does not bind the native trace and plan")
        selected = {k["pose_index"] for k in schedule["knots"]}
        stances = {transitions["poses"][i]["stance"] for i in selected}
    else:
        stances = {p["stance"] for p in plan["poses"]}
    if len(stances) != 1 or not stances <= {"L", "R"}:
        raise ValueError("This diagnostic requires one fixed planned stance")
    stance = stances.pop()
    model = Model(snapshot, source)
    model.use_cooked_feet(cooked, sha(args.cooked))
    model.terrain = TriangleTerrain(snapshot, source)
    for side in ("L", "R"):
        model.set_terrain_placement(side, plan["initial_terrain_placements"][side])
    indices = [trace["dof_names"].index(n) for n in model.names]
    paths = receipt["native_structure"]["link_paths"]
    samples = trace["samples"]
    if (not np.isfinite(args.after_seconds) or
            not 0 <= args.after_seconds < samples[-1]["time_s"]):
        raise ValueError("Invalid end of startup interval")
    if any(abs(b["time_s"]-a["time_s"]-trace["dt_s"]) > 1e-8
           for a, b in zip(samples, samples[1:])):
        raise ValueError("Native samples are not continuous")
    weight = sum(receipt["native_structure"]["masses_kg"])*receipt["authored_gravity_m_s2"]
    rows, pair_maxima = [], {}
    for sample in samples:
        if sample["time_s"] < args.after_seconds:
            continue
        native = {}
        for path, pose in zip(paths, sample["link_transforms_xyzw"]):
            matrix = np.eye(4)
            matrix[:3, :3], matrix[:3, 3] = rotation(pose[3:]), pose[:3]
            native[path] = matrix
        commanded = model.transforms(np.asarray(sample["commanded_dof_rad"])[indices], stance)
        actual_q = model.transforms(np.asarray(sample["actual_dof_rad"])[indices], stance)
        row = {"time_s": sample["time_s"], "feet": {},
               "root_native_minus_commanded_fk_m":
                   (native[model.root][:3, 3]-commanded[model.root][:3, 3]).tolist(),
               "root_joint_tracking_component_m":
                   (actual_q[model.root][:3, 3]-commanded[model.root][:3, 3]).tolist(),
               "root_stance_anchor_and_constraint_component_m":
                   (native[model.root][:3, 3]-actual_q[model.root][:3, 3]).tolist()}
        for key in ("native_center_of_mass_world_m", "measured_contact_support_margins_m"):
            if key in sample:
                row[key] = sample[key]
        for index, side in enumerate(("L", "R")):
            matrix = native[model.foot_paths[side]]
            world = model.foot_vertices[side]@matrix[:3, :3].T+matrix[:3, 3]
            terrain_z, _ = model.terrain.query(world[:, :2])
            gap = float((world[:, 2]-terrain_z).min())
            fixed_stance_gaps = {}
            for label, transforms in (("commanded", commanded), ("actual_joint", actual_q)):
                pose = transforms[model.foot_paths[side]]
                vertices = model.foot_vertices[side]@pose[:3, :3].T+pose[:3, 3]
                heights, _ = model.terrain.query(vertices[:, :2])
                fixed_stance_gaps[label+"_fixed_stance_local_vertex_gap_m"] = float(
                    (vertices[:, 2]-heights).min())
            row["feet"][side] = {
                "minimum_cooked_vertex_local_terrain_gap_m": gap,
                **fixed_stance_gaps,
                "origin_minus_initial_placement_m":
                    (matrix[:3, 3]-model.foot_rest[side][:3, 3]).tolist(),
                "supported_airborne": bool(
                    gap >= .005 and not sample["root_sleeping"] and sample["up_z"] >= .95
                    and np.linalg.norm(sample["feet"][index]["force_N"]) <= .01*weight
                    and sample["feet"][1-index]["force_N"][2] >= .5*weight)}
        for pair in sample.get("self_contacts", []):
            key = "|".join(sorted((pair["body_a"], pair["body_b"])))
            record = pair_maxima.setdefault(key, {
                "maximum_force_N": 0., "minimum_separation_m": None,
                "maximum_normal_impulse_Ns": 0.})
            record["maximum_force_N"] = max(record["maximum_force_N"], float(np.linalg.norm(pair["force_N"])))
            record["maximum_normal_impulse_Ns"] = max(record["maximum_normal_impulse_Ns"], pair["maximum_normal_impulse_Ns"])
            record["minimum_separation_m"] = min(
                pair["minimum_separation_m"],
                pair["minimum_separation_m"] if record["minimum_separation_m"] is None
                else record["minimum_separation_m"])
        rows.append(row)
    feet = {}
    for side in ("L", "R"):
        intervals, active = [], []
        for row in rows:
            if row["feet"][side]["supported_airborne"]:
                active.append(row["time_s"])
            elif active:
                intervals.append(active)
                active = []
        if active:
            intervals.append(active)
        feet[side] = {
            "minimum_local_vertex_gap_m": min(r["feet"][side]["minimum_cooked_vertex_local_terrain_gap_m"] for r in rows),
            "maximum_local_vertex_gap_m": max(r["feet"][side]["minimum_cooked_vertex_local_terrain_gap_m"] for r in rows),
            "maximum_foot_origin_horizontal_displacement_m": max(
                float(np.linalg.norm(r["feet"][side]["origin_minus_initial_placement_m"][:2])) for r in rows),
            "supported_airborne_intervals": [
                {"first_s": t[0], "last_s": t[-1], "span_s": t[-1]-t[0], "samples": len(t)}
                for t in intervals]}
    result = {
        "candidate_sha256": receipt["candidate_sha256"], "physics_accepted": False,
        "walking_accepted": False, "stance": stance, "analysis_start_s": args.after_seconds,
        "evidence_sha256": {k: sha(p) for k, p in vars(args).items()
                            if isinstance(p, Path) and k != "out"},
        "script_sha256": sha(Path(__file__)), "feet": feet, "self_contact_pair_maxima": pair_maxima,
        "model_sha256": sha(Path(sys.modules[Model.__module__].__file__)),
        "terrain_module_sha256": sha(Path(sys.modules[TriangleTerrain.__module__].__file__)),
        "notes": [
            "Local terrain gap is sampled at native cooked vertices; no continuous surface or trajectory certificate.",
            "Foot-origin displacement can include rocking and is not a direct measurement of contact slip.",
            "Root error is decomposed into joint tracking and the remaining stance-anchor/constraint component.",
            "Airborne requires >=5 mm local vertex gap, <=1% body-weight swing force, >=50% opposite support, awake root and up >=0.95.",
            "This diagnostic does not establish a walking cycle or reference conformity."],
        "maximum_native_target_readback_error_rad": max(
            float(np.max(abs(np.asarray(s["native_position_targets_rad"])-s["commanded_dof_rad"])))
            for s in samples), "samples": rows}
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}, indent=2))


if __name__ == "__main__":
    main()
