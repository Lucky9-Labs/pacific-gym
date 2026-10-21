"""Summarize a native motion trace without treating a probe as gait acceptance."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("receipt", "samples", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--after-seconds", required=True, type=float,
                        help="Explicit end of the startup/drop interval to exclude from lift metrics")
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    receipt = json.loads(args.receipt.read_text())
    trace = json.loads(gzip.decompress(args.samples.read_bytes()))
    if (receipt["sample_output_sha256"] != sha(args.samples)
            or receipt["candidate_sha256"] != trace["candidate_sha256"]
            or receipt["kind"] != "native_gpu_bounded_motion_diagnostic"):
        raise ValueError("Motion receipt and trace do not match")
    samples = trace["samples"]
    dt = trace["dt_s"]
    if not samples or any(abs(b["time_s"]-a["time_s"]-dt) > 1e-8
                          for a, b in zip(samples, samples[1:])):
        raise ValueError("Missing or nonuniform native samples")
    if not np.isfinite(args.after_seconds) or not 0 <= args.after_seconds < samples[-1]["time_s"]:
        raise ValueError("Invalid lift-analysis start time")
    motion_samples = [s for s in samples if s["time_s"] >= args.after_seconds]
    weight = sum(receipt["native_structure"]["masses_kg"]) * receipt["authored_gravity_m_s2"]
    result = {
        "candidate_sha256": trace["candidate_sha256"],
        "receipt_sha256": sha(args.receipt),
        "samples_sha256": sha(args.samples),
        "walking_accepted": False,
        "physics_accepted": False,
        "scope": "Measured diagnostic events, not a walking acceptance decision",
        "swing_observation_thresholds": {
            "minimum_cooked_foot_height_above_highest_terrain_point_m": .005,
            "maximum_swing_force_fraction_body_weight": .01,
            "minimum_other_foot_vertical_support_fraction": .5,
            "root_must_be_awake": True,
            "minimum_root_up_z": .95,
        },
        "lift_analysis_start_s": args.after_seconds,
        "terrain_height_note": (
            "The reference height is the highest terrain point, not the local surface. "
            "Positive height conservatively proves clearance; negative height is not penetration."),
        "fall_detected": receipt["fall_detected"],
        "observed_duration_s": samples[-1]["time_s"],
        "minimum_root_up_z": min(s["up_z"] for s in samples),
        "root_displacement_m": (np.array(samples[-1]["root_position_m"])
                                - samples[0]["root_position_m"]).tolist(),
        "feet": {},
    }
    for side, index in (("L", 0), ("R", 1)):
        intervals, active = [], []
        for sample in motion_samples:
            force = np.linalg.norm(sample["feet"][index]["force_N"])
            support = sample["feet"][1-index]["force_N"][2]
            clearance = sample["foot_geometry"][side]["cooked_lowest_above_ground_m"]
            lifted = (not sample["root_sleeping"] and sample["up_z"] >= .95 and clearance >= .005
                      and force <= .01*weight and support >= .5*weight)
            if lifted:
                active.append(sample)
            elif active:
                intervals.append(active)
                active = []
        if active:
            intervals.append(active)
        result["feet"][side] = {
            "maximum_cooked_height_above_highest_terrain_point_m": max(
                s["foot_geometry"][side]["cooked_lowest_above_ground_m"] for s in motion_samples),
            "maximum_reported_minimum_contact_separation_m": max(
                (s["feet"][index]["minimum_separation_m"] for s in motion_samples
                 if s["feet"][index]["minimum_separation_m"] is not None), default=None),
            "supported_airborne_intervals": [{
                "first_sample_s": group[0]["time_s"],
                "last_sample_s": group[-1]["time_s"],
                "sample_count": len(group),
                "observed_span_s": group[-1]["time_s"]-group[0]["time_s"],
                "foot_displacement_m": (
                    np.array(group[-1]["foot_geometry"][side]["position_m"])
                    - group[0]["foot_geometry"][side]["position_m"]).tolist(),
            } for group in intervals],
        }
    error = np.array([np.array(s["actual_dof_rad"])-s["commanded_dof_rad"] for s in samples])
    result["maximum_absolute_tracking_error_deg"] = dict(zip(
        trace["dof_names"], np.degrees(np.max(abs(error), axis=0)).tolist()))
    nonfoot = [i for i, path in enumerate(receipt["ground_sensor_body_paths"])
               if not path.endswith(("/footL", "/footR"))]
    result["maximum_nonfoot_ground_force_N"] = max(
        [float(np.linalg.norm(s["body_ground_force_N"][i])) for s in samples for i in nonfoot],
        default=0.)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
