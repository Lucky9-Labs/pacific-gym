"""Measure alternating steps from hash-bound native motion, without promoting a run."""

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from pacific_gym.articulation_audit import rotation
from pacific_gym.quasistatic_plan import Model
from pacific_gym.terrain import TriangleTerrain


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    data = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return json.loads(data)


def longest_span(rows, predicate):
    first = last = None
    best = 0.
    for row in rows:
        if predicate(row):
            first = row["time_s"] if first is None else first
            last = row["time_s"]
            best = max(best, last-first)
        else:
            first = last = None
    return best


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("receipt", "samples", "snapshot", "cooked", "source-geometry",
                 "plan", "motion-schedule", "transition-poses", "out"):
        parser.add_argument("--"+name, required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    receipt, trace, snapshot, cooked, source, plan, schedule, transitions = map(
        read, (args.receipt, args.samples, args.snapshot, args.cooked,
               args.source_geometry, args.plan, args.motion_schedule, args.transition_poses))
    evidence = receipt["evidence_sha256"]
    for key, path in (("snapshot", args.snapshot), ("cooked", args.cooked),
                      ("planned_poses", args.plan), ("motion_schedule", args.motion_schedule),
                      ("transition_poses", args.transition_poses)):
        if evidence[key] != sha(path):
            raise ValueError("Native evidence hash mismatch: "+key)
    candidate = receipt["candidate_sha256"]
    if (receipt["sample_output_sha256"] != sha(args.samples)
            or any(d["candidate_sha256"] != candidate for d in
                   (trace, cooked, plan, schedule, transitions))
            or snapshot["usd_sha256"] != candidate
            or plan["snapshot_sha256"] != sha(args.snapshot)
            or plan["cooked_sha256"] != sha(args.cooked)
            or plan["source_geometry_sha256"] != sha(args.source_geometry)
            or transitions["planner_sha256"] != sha(args.plan)
            or schedule["transition_poses_sha256"] != sha(args.transition_poses)):
        raise ValueError("Candidate and planning lineage mismatch")
    samples = trace["samples"]
    dt = trace["dt_s"]
    if (not samples or not np.isfinite(dt) or dt <= 0
            or abs(dt-receipt["physics_step_dt_s"]) > 1e-12
            or any(abs(b["time_s"]-a["time_s"]-dt) > 1e-8
                   for a, b in zip(samples, samples[1:]))):
        raise ValueError("Native samples must be continuous")
    model = Model(snapshot, source)
    model.use_cooked_feet(cooked, sha(args.cooked))
    terrain = TriangleTerrain(snapshot, source)
    structure = receipt["native_structure"]
    paths = structure["link_paths"]
    foot_indices = {side: paths.index(model.foot_paths[side]) for side in ("L", "R")}
    weight = sum(structure["masses_kg"])*receipt["authored_gravity_m_s2"]
    if weight <= 0:
        raise ValueError("Positive native body weight is required")
    forward = np.asarray(model.basis["forward"])
    feet_body_indices = {
        side: receipt["ground_sensor_body_paths"].index(model.foot_paths[side])
        for side in ("L", "R")}
    nonfoot_indices = [i for i in range(len(receipt["ground_sensor_body_paths"]))
                       if i not in feet_body_indices.values()]
    rows = []
    for sample in samples:
        row = {"time_s": sample["time_s"], "root_up_z": sample["up_z"],
               "root_sleeping": sample["root_sleeping"],
               "root_position_m": sample["root_position_m"], "feet": {}}
        for index, side in enumerate(("L", "R")):
            pose = np.asarray(sample["link_transforms_xyzw"][foot_indices[side]])
            if pose.shape != (7,) or not np.isfinite(pose).all():
                raise ValueError("Invalid native foot transform")
            world = model.foot_vertices[side]@rotation(pose[[6, 3, 4, 5]]).T+pose[:3]
            heights, _ = terrain.query(world[:, :2])
            gap = float((world[:, 2]-heights).min())
            force = np.asarray(sample["feet"][index]["force_N"])
            opposite_force = sample["feet"][1-index]["force_N"][2]
            row["feet"][side] = {
                "position_m": pose[:3].tolist(), "minimum_local_vertex_gap_m": gap,
                "vertical_force_over_weight": float(force[2]/weight),
                "supported_airborne": bool(
                    gap >= .005 and np.linalg.norm(force) <= .01*weight
                    and opposite_force >= .5*weight and sample["up_z"] >= .95
                    and not sample["root_sleeping"]),
                "loaded_ground_contact": bool(
                    abs(gap) <= .001 and force[2] >= .01*weight
                    and sample["up_z"] >= .95 and not sample["root_sleeping"])}
        row["nonfoot_ground_support_over_weight"] = sum(
            max(0., sample["body_ground_force_N"][i][2]) for i in nonfoot_indices)/weight
        rows.append(row)
    # Phase boundaries only select windows. They do not establish a step.
    def physical_time(path_time):
        for segment in schedule.get("time_warp_segments", []):
            start, end = segment["start_s"], segment["end_s"]
            if segment["curve"] != "quintic_rest_to_rest" or not start < end:
                raise ValueError("Unsupported phase timing")
            if start < path_time < end:
                target = (path_time-start)/(end-start)
                low, high = 0., 1.
                for _ in range(60):
                    u = (low+high)/2
                    value = u*u*u*(10+u*(-15+6*u))
                    if value < target:
                        low = u
                    else:
                        high = u
                return start+(end-start)*(low+high)/2
        return path_time

    phases = {}
    for knot in schedule["knots"]:
        pose = transitions["poses"][knot["pose_index"]]
        if "step" in pose:
            phases.setdefault(pose["step"], {}).setdefault(pose["phase"], []).append(
                (physical_time(knot["time_s"]), pose))
    step_results = []
    for step, group in sorted(phases.items()):
        if "lift" not in group or "touchdown" not in group:
            continue
        first_lift_time, first_lift = min(group["lift"], key=lambda item: item[0])
        last_touchdown_time, touchdown = max(group["touchdown"], key=lambda item: item[0])
        weight_end = max((t for t, _ in group.get("weight", [])), default=first_lift_time)
        stance = first_lift["stance"]
        swing = "R" if stance == "L" else "L"
        window = [r for r in rows if weight_end <= r["time_s"] <= last_touchdown_time+.5]
        before = min(rows, key=lambda r: abs(r["time_s"]-weight_end))
        landed = [r for r in window if last_touchdown_time-.1 <= r["time_s"]
                  <= last_touchdown_time+.5]
        span = longest_span(window, lambda r: r["feet"][swing]["supported_airborne"])
        landing_span = longest_span(landed, lambda r: r["feet"][swing]["loaded_ground_contact"])
        end_position = (np.median([r["feet"][swing]["position_m"] for r in landed], axis=0)
                        if landed else np.asarray(before["feet"][swing]["position_m"]))
        forward_travel = float(np.dot(
            end_position-np.asarray(before["feet"][swing]["position_m"]), forward))
        complete_window = bool(touchdown["fraction"] == 1.
                               and rows[-1]["time_s"] >= last_touchdown_time-.5*dt)
        passed = bool(complete_window and span >= .2 and landing_span >= .05
                      and forward_travel >= .01)
        step_results.append({
            "step": step, "stance": stance, "swing": swing,
            "window_start_s": weight_end, "touchdown_target_time_s": last_touchdown_time,
            "window_completed": complete_window, "supported_airborne_span_s": span,
            "loaded_landing_span_s": landing_span,
            "measured_swing_foot_forward_travel_m": forward_travel,
            "step_criteria_pass": passed})
    cycles = 0
    for i in range(0, len(step_results)-1, 2):
        left, right = step_results[i:i+2]
        if (left["step"] != i or right["step"] != i+1
                or not left["step_criteria_pass"] or not right["step_criteria_pass"]
                or left["swing"] == right["swing"]):
            break
        cycles += 1
    initial_root = np.asarray(rows[0]["root_position_m"])
    final_root = np.median([r["root_position_m"] for r in rows
                           if r["time_s"] >= rows[-1]["time_s"]-.5], axis=0)
    net_forward = float(np.dot(final_root-initial_root, forward))
    native_clean = bool(
        receipt["status"] == "completed" and receipt.get("motion_probe_pass", False)
        and not receipt["fall_detected"] and not receipt["invalid_physics_state"]
        and receipt["runtime_root_resets"] == trace["root_resets"] == 0
        and trace["external_support_applied"] is False
        and structure["root_kinematic"] is False
        and structure["authored_self_collision_enabled"] is True
        and receipt["runtime_shape_count_matches_cook"]
        and not any(r["root_sleeping"] for r in rows)
        and min(r["root_up_z"] for r in rows) >= .95
        and max(r["nonfoot_ground_support_over_weight"] for r in rows) <= .01
        and not any(s.get("self_contacts") for s in samples)
        and receipt["maximum_joint_limit_excess_rad"] < np.radians(.1))
    result = {
        "candidate_sha256": candidate, "walking_accepted": False,
        "native_walking_criteria_pass": bool(
            native_clean and cycles >= 3 and net_forward >= .1 and step_results
            and all(s["step_criteria_pass"] for s in step_results)),
        "native_clean": native_clean, "completed_cycles": cycles,
        "net_forward_displacement_m": net_forward,
        "minimum_root_up_z": min(r["root_up_z"] for r in rows),
        "steps": step_results, "sample_count": len(rows),
        "evidence_sha256": {k: sha(v) for k, v in vars(args).items() if k != "out"},
        "script_sha256": sha(Path(__file__)),
        "model_sha256": sha(Path(sys.modules[Model.__module__].__file__)),
        "terrain_module_sha256": sha(Path(sys.modules[TriangleTerrain.__module__].__file__)),
        "numeric_library_versions": {
            "numpy": np.__version__, "scipy": sys.modules["scipy"].__version__},
        "criteria": {"minimum_cycles": 3, "minimum_root_forward_travel_m": .1,
                     "minimum_supported_airborne_span_s": .2,
                     "minimum_loaded_landing_span_s": .05,
                     "minimum_swing_forward_travel_m": .01, "minimum_root_up_z": .95},
        "notes": [
            "Cycles require measured alternating airborne, landing and forward-foot travel.",
            "Local terrain gaps sample cooked vertices; no continuous surface certificate.",
            "Phase timing only selects measurement windows.",
            "Native video and final isaac-run verification are still required."]}
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
