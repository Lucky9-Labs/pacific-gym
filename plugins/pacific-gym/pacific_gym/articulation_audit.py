"""Offline requirement audit of an exported articulation and its motion receipt.

Usage: python -m pacific_gym.articulation_audit --snapshot ... --config ...
       --source-geometry ... [--motion ...] --out ...

This module reads evidence only. It never promotes a run or substitutes an
offline result for an Isaac receipt. NumPy is required.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def rotation(quaternion):
    q = np.asarray(quaternion, dtype=float)
    if q.shape != (4,) or not np.isfinite(q).all() or abs(np.linalg.norm(q) - 1) > 1e-5:
        raise ValueError("Joint frame has an invalid quaternion")
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([
        [1 - 2*(y*y + z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
    ])


def unit(vector):
    v = np.asarray(vector, dtype=float)
    n = np.linalg.norm(v)
    if not np.isfinite(n) or n < 1e-8:
        raise ValueError("Cannot infer a direction from a zero or nonfinite vector")
    return v / n


def frame(position, quaternion):
    matrix = np.eye(4)
    matrix[:3, :3] = rotation(quaternion)
    matrix[:3, 3] = position
    return matrix


def source_basis(source):
    bones = source["bones"]
    up = np.array([0., 0., 1.])
    lateral = unit(np.array(bones["thigh.L"]["head"]) - bones["thigh.R"]["head"])
    toward_toes = sum((np.array(bones["toe."+side]["head"]) -
                      bones["heel."+side]["head"] for side in ("L", "R")))
    toward_toes -= up * np.dot(up, toward_toes)
    forward = unit(toward_toes)
    return {
        "basis": "inferred from source thigh L-R and heel-to-toe positions in Z-up",
        "lateral": lateral.tolist(), "forward": forward.tolist(), "up": up.tolist(),
        "gltf_y_up_to_stage_z_up": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
    }


def audit_motion(receipt, snapshot, basis):
    if receipt["candidate_sha256"] != snapshot["usd_sha256"]:
        raise ValueError("Motion receipt belongs to a different USD")
    revolute = {j["name"]: j for j in snapshot["joints"] if "axis" in j}
    runtime_names = [name for name in receipt["runtime_structure"]["joint_names"]
                     if name in revolute]
    if len(runtime_names) != len(revolute) or len(set(runtime_names)) != len(runtime_names):
        raise ValueError("Runtime DOF names do not match the exported articulation")
    indices = receipt["joint_indices"]
    if sorted(indices) != list(range(len(runtime_names))):
        raise ValueError("Target DOF indices must be a complete, unique mapping")
    target_names = [runtime_names[index] for index in indices]
    violations = {name: {"samples": 0, "maximum_excess_deg": 0.} for name in target_names}
    actual_violations = {name: {"samples": 0, "maximum_excess_deg": 0.} for name in runtime_names}
    samples = receipt["samples"]
    if len(samples) < 2:
        raise ValueError("Motion receipt has fewer than two samples")
    times = np.array([s["time_s"] for s in samples], dtype=float)
    if not np.isfinite(times).all() or not np.all(np.diff(times) > 0):
        raise ValueError("Motion sample times must be finite and increasing")
    # These are observed contact-free intervals, never timer-derived gait cycles.
    intervals = []
    current = None
    for i, sample in enumerate(samples):
        targets = np.degrees(sample["target_rad"])
        if len(targets) != len(target_names) or not np.isfinite(targets).all():
            raise ValueError("Nonfinite or incomplete joint target sample")
        for name, angle in zip(target_names, targets):
            lower, upper = revolute[name]["limits_deg"]
            excess = max(lower - angle, angle - upper, 0)
            if excess > 1e-4:
                violations[name]["samples"] += 1
                violations[name]["maximum_excess_deg"] = max(
                    violations[name]["maximum_excess_deg"], float(excess))
        actual = np.degrees(sample["actual_dof_rad"])
        if len(actual) != len(runtime_names) or not np.isfinite(actual).all():
            raise ValueError("Nonfinite or incomplete measured joint sample")
        for name, angle in zip(runtime_names, actual):
            lower, upper = revolute[name]["limits_deg"]
            excess = max(lower-angle, angle-upper, 0)
            if excess > 1e-4:
                actual_violations[name]["samples"] += 1
                actual_violations[name]["maximum_excess_deg"] = max(
                    actual_violations[name]["maximum_excess_deg"], float(excess))
        counts = [foot["count"] for foot in sample["feet"]]
        side = "L" if counts[0] == 0 and counts[1] > 0 else (
            "R" if counts[1] == 0 and counts[0] > 0 else None)
        if current and side != current["swing_side"]:
            current["end_s"] = float(times[i])
            current["duration_s"] = current["end_s"] - current["start_s"]
            intervals.append(current)
            current = None
        if side and current is None:
            current = {"swing_side": side, "start_s": float(times[i])}
    if current:
        current["end_s"] = float(times[-1])
        current["duration_s"] = current["end_s"] - current["start_s"]
        intervals.append(current)
    sustained = [item for item in intervals if item["duration_s"] >= .1]
    total_mass = sum(body["mass_kg"] for body in snapshot["bodies"])
    # Historical scripts use a 1/120 s force divisor while recording at ~60 Hz.
    # This comparison diagnoses a scale problem; it does not rescale evidence.
    settled = [s for s in samples if 1.0 <= s["time_s"] < 1.9]
    ratio = None
    if settled:
        ratio = float(np.median([
            sum(foot["ground_force_N"][2] for foot in s["feet"])
            for s in settled]) / (total_mass * 9.81))
    displacement = np.array(samples[-1]["root_m"]) - samples[0]["root_m"]
    return {
        "target_joint_names": target_names,
        "target_limit_violations": {n: v for n, v in violations.items() if v["samples"]},
        "measured_limit_violations": {n: v for n, v in actual_violations.items() if v["samples"]},
        "sample_interval_s_median": float(np.median(np.diff(times))),
        "settled_reported_normal_force_over_weight": ratio,
        "force_scaling_qualified": False,
        "force_scaling_note": "Requires impulses and exact physics-step dt; offline ratio is diagnostic.",
        "contact_free_intervals_at_least_100ms": sustained,
        "timer_count_reported_by_old_script": receipt.get("walk_cycles_measured"),
        "measured_walking_cycles": None,
        "walking_cycles_note": "Contact intervals alone lack foot travel, touchdown and full support-cycle evidence.",
        "net_forward_root_displacement_m": float(np.dot(displacement, basis["forward"])),
        "fall_reported": receipt.get("fall"), "invalid_reported": receipt.get("invalid"),
        "walking_accepted": False,
    }


def audit(snapshot, config, source, motion=None):
    if source["source_sha256"] != config["source_sha256"]:
        raise ValueError("Source geometry and configuration hashes disagree")
    if snapshot["up_axis"] != "Z" or snapshot["meters_per_unit"] != 1:
        raise ValueError("This source exporter requires a meter-scale Z-up stage")
    basis = source_basis(source)
    bodies = {b["path"]: b for b in snapshot["bodies"]}
    checks = []
    coverage = []
    families = {}
    for joint in snapshot["joints"]:
        a, b = (bodies[joint["body"+str(index)][0]] for index in (0, 1))
        world0 = np.array(a["world_matrix"]) @ frame(
            joint["local_pos0"], joint["local_rot0_wxyz"])
        world1 = np.array(b["world_matrix"]) @ frame(
            joint["local_pos1"], joint["local_rot1_wxyz"])
        row = {
            "name": joint["name"],
            "rest_pivot_mismatch_m": float(np.linalg.norm(world0[:3, 3]-world1[:3, 3])),
            "rest_rotation_matrix_max_error": float(np.max(abs(world0[:3, :3]-world1[:3, :3]))),
        }
        if "axis" in joint:
            axis = world0[:3, :3] @ np.eye(3)["XYZ".index(joint["axis"])]
            family, side = joint["name"].rsplit("_", 1)
            # Explicit semantic alias; it does not imply source physics constraints.
            family = {"foot_roll": "ankle_roll"}.get(family, family)
            families[(family, side)] = joint
            target = "lateral" if family in ("hip_pitch", "knee", "ankle_pitch") else (
                "up" if family == "hip_yaw" else "forward")
            foot = np.array(source["bones"]["lower_shin."+side]["head"])
            row.update(world_axis=axis.tolist(), expected_direction=target,
                       absolute_axis_alignment=float(abs(np.dot(unit(axis), basis[target]))),
                       foot_origin_velocity_per_radian_m=np.cross(
                           axis, foot-world0[:3, 3]).tolist())
        checks.append(row)
    for family, required in config["clearance"]["joint_sweep_degrees"].items():
        for side in ("L", "R"):
            joint = families.get((family, side))
            authored = joint["limits_deg"] if joint else None
            coverage.append({
                "family": family, "side": side, "required_deg": required,
                "authored_deg": authored,
                "covered": bool(authored and authored[0] <= required[0]
                                and authored[1] >= required[1]),
            })
    visuals = [m for m in snapshot["meshes"] if "/visual/" in m["path"]]
    triangles = sum(m["triangles"] for m in visuals)
    cap = config["max_total_triangles"] * (
        1 + config["max_total_triangles_rounding_tolerance_fraction"])
    mass = sum(body["mass_kg"] for body in bodies.values())
    failures = []
    if triangles > cap:
        failures.append("Visual triangle budget exceeded")
    if len(visuals) != config["source_mesh_nodes"]:
        failures.append("Source visual mesh count changed")
    if not all(row["covered"] for row in coverage):
        failures.append("Authored joints do not cover configured sweep families/ranges")
    if abs(mass-config["mass_model"]["total_mass_kg"]) > 1e-4:
        failures.append("Configured total mass not preserved")
    if any(not math.isfinite(b["mass_kg"]) or
           b["mass_kg"] < config["mass_model"]["minimum_link_mass_kg"]-1e-7
           for b in bodies.values()):
        failures.append("Nonfinite or below-minimum link mass")
    collision_flags = [body["self_collision"] for body in bodies.values()
                       if body["self_collision"] is not None]
    if (config["clearance"]["require_preserved_self_collision"]
            and (not collision_flags or not all(flag is True for flag in collision_flags))):
        failures.append("Articulation self-collision is not enabled")
    if any(body["kinematic"] for body in bodies.values()):
        failures.append("Articulation contains a kinematic rigid body")
    if any(not all(math.isfinite(x) and x > 0 for x in b["diagonal_inertia"])
           for b in bodies.values()):
        failures.append("Nonpositive or nonfinite diagonal inertia")
    if any(row["rest_pivot_mismatch_m"] > config["max_world_rest_translation_error_m"]
           or row["rest_rotation_matrix_max_error"] > 1e-6 for row in checks):
        failures.append("Joint frames do not close at rest")
    result = {
        "schema_version": 1, "candidate_sha256": snapshot["usd_sha256"],
        "source_sha256": source["source_sha256"], "source_basis": basis,
        "visual_meshes": len(visuals), "visual_triangles": triangles,
        "triangle_budget_with_rounding": cap, "mass_kg": mass,
        "joint_frames": checks, "configured_sweep_coverage": coverage,
        "requirements_failed": failures,
        "not_established": [
            "Configured clearance over all required sampled poses",
            "Cumulative collision shape limits relative to original source",
            "Cooked hull identity including transforms and all cooking settings",
            "Contact-calibrated forward walking on this exact candidate",
        ],
        "physics_accepted": False,
    }
    if motion:
        result["motion"] = audit_motion(motion, snapshot, basis)
        if result["motion"]["target_limit_violations"]:
            failures.append("Controller commands exceed authored joint limits")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "config", "source-geometry", "out"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--motion", type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    paths = {name: getattr(args, name.replace("-", "_"))
             for name in ("snapshot", "config", "source-geometry", "motion")}
    data = {name: json.loads(path.read_text()) for name, path in paths.items() if path}
    result = audit(data["snapshot"], data["config"], data["source-geometry"], data.get("motion"))
    result["evidence_sha256"] = {
        name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items() if path}
    result["auditor_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"report": str(args.out), "requirements_failed": result["requirements_failed"]}))


if __name__ == "__main__":
    main()
