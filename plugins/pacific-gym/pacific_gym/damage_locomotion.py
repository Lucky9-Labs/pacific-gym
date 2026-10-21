"""Offline damage-specific criteria report, independent of intact walking acceptance.

No simulator is imported or launched. This consumes a future native damage
producer's explicit contract; existing intact traces cannot substitute for it.
File hashes bind supplied evidence, but do not authenticate native provenance.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path


def need(condition, message):
    if not condition:
        raise ValueError(message)


def number(value, name, minimum=None):
    need(type(value) in (int, float) and math.isfinite(value), f"Finite {name} required")
    need(minimum is None or value >= minimum, f"{name} below minimum")
    return value


def vector(value, name):
    need(isinstance(value, list) and len(value) == 3, f"{name} must have three components")
    return [number(x, name) for x in value]


def digest(value):
    need(isinstance(value, str) and len(value) == 64
         and all(c in "0123456789abcdef" for c in value), "Invalid SHA-256")
    return value


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, "Duplicate JSON field: " + key)
            out[key] = value
        return out
    return json.loads(Path(path).read_text(), object_pairs_hook=pairs,
                      parse_constant=lambda _: need(False, "Nonfinite JSON"))


def morphology(value):
    """Validate declared physical state, including absence rather than ghost links."""
    links, joints = value["links"], value["joints"]
    need(isinstance(links, dict) and links and isinstance(joints, dict), "Named morphology required")
    present, remaining, affected = {}, {}, 0
    for name, link in links.items():
        need(isinstance(name, str) and name, "Nonempty link identity required")
        if link["state"] == "removed":
            need(set(link) == {"state"}, "Removed links must not retain mass, geometry or collision")
            affected += 1
            continue
        need(link["state"] == "present", "Unknown link state")
        need(set(link) == {"state", "mass_kg", "com_m", "inertia_kg_m2",
                           "geometry_sha256", "collision_enabled"}, "Incomplete physical link state")
        need(number(link["mass_kg"], "link mass", 0) > 0, "Present links require positive mass")
        vector(link["com_m"], "centre of mass")
        inertia = link["inertia_kg_m2"]
        need(isinstance(inertia, list) and len(inertia) == 3, "Full inertia tensor required")
        a, b, c = [vector(row, "inertia row") for row in inertia]
        need(a[1] == b[0] and a[2] == c[0] and b[2] == c[1], "Inertia must be symmetric")
        determinant = a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
        need(a[0] > 0 and a[0]*b[1]-a[1]*b[0] > 0 and determinant > 0,
             "Inertia must be positive definite")
        digest(link["geometry_sha256"])
        need(type(link["collision_enabled"]) is bool, "Explicit collision state required")
        present[name] = link
    need(present, "Damage morphology has no remaining body")
    need(value["root_link"] in present, "The tracked root must remain present")
    for name, joint in joints.items():
        need(isinstance(name, str) and name, "Nonempty joint identity required")
        state = joint["state"]
        need(state in {"actuated", "locked", "free", "removed"}, "Unknown actuator state")
        need(joint["parent_link"] in links and joint["child_link"] in links, "Unknown joint endpoint")
        need(joint["parent_link"] != joint["child_link"], "Joint endpoints must be different links")
        if state == "removed":
            need(set(joint) == {"state", "parent_link", "child_link"}, "Removed joint retains actuator state")
            affected += 1
            continue
        required = {"state", "parent_link", "child_link", "lower_rad", "upper_rad", "max_effort_Nm"}
        if state == "locked":
            required.add("locked_position_rad")
        need(set(joint) == required, "Incomplete joint state")
        need(joint["parent_link"] in present and joint["child_link"] in present,
             "Remaining joint attaches to a removed link")
        lower, upper = number(joint["lower_rad"], "lower limit"), number(joint["upper_rad"], "upper limit")
        need(lower <= upper, "Inverted joint limits")
        effort = number(joint["max_effort_Nm"], "actuator limit", 0)
        if state == "actuated":
            need(effort > 0, "Actuated joint requires positive authority")
        else:
            need(effort == 0, "Failed actuators must have zero authority")
            affected += 1
        if state == "locked":
            position = number(joint["locked_position_rad"], "lock position")
            need(lower <= position <= upper, "Lock position exceeds authored limits")
        remaining[name] = joint
    need(affected > 0, "Intact morphology belongs to the intact walking gate")
    return present, remaining


def evaluate(spec, receipt, trace):
    need(all(type(item.get("schema_version")) is int and item["schema_version"] == 1
             for item in (spec, receipt, trace)),
         "Unsupported damage evidence version")
    need(spec["kind"] == "damage_locomotion_spec"
         and receipt["kind"] == "native_gpu_damage_locomotion"
         and trace["kind"] == "damage_locomotion_trace", "Damage-specific evidence required")
    for key in ("baseline_candidate_sha256", "damaged_candidate_sha256", "reference_manifest_sha256"):
        digest(spec[key])
        need(receipt[key] == spec[key], "Damage lineage mismatch: " + key)
    need(trace["candidate_sha256"] == spec["damaged_candidate_sha256"], "Trace candidate mismatch")
    links, joints = morphology(spec["morphology"])
    morphology(receipt["observed_morphology"])
    need(receipt["observed_morphology"] == spec["morphology"], "Native morphology read-back differs")
    need(receipt["native_root_link"] == trace["root_link"] == spec["morphology"]["root_link"],
         "Damage trace root identity differs")
    for key, expected in (("native_link_names", links), ("native_joint_names", joints)):
        names = receipt[key]
        need(isinstance(names, list) and len(names) == len(set(names))
             and set(names) == set(expected), "Native topology mismatch: " + key)
    event = receipt["damage_event"]
    need(type(spec["event"]["seed"]) is int and spec["event"]["seed"] >= 0, "Damage seed required")
    need(type(event["seed"]) is int, "Native event seed must be an integer")
    need(event["seed"] == spec["event"]["seed"] and event["kind"] == spec["event"]["kind"]
         and event["target"] == spec["event"]["target"], "Damage event differs from specification")
    if event["kind"] == "link_removed":
        need(spec["morphology"]["links"][event["target"]]["state"] == "removed", "Link was not removed")
    else:
        states = {"joint_locked": "locked", "actuator_disabled": "free"}
        need(event["kind"] in states, "Unsupported damage event")
        need(spec["morphology"]["joints"][event["target"]]["state"] == states[event["kind"]],
             "Failure type differs from physical joint state")
    event_time = number(event["time_s"], "event time", 0)
    need(0 <= number(event["reference_phase"], "reference phase") < 1, "Invalid event phase")
    criteria = spec["criteria"]
    for key in ("minimum_post_damage_duration_s", "minimum_forward_m", "stable_window_s", "maximum_sample_gap_s"):
        need(number(criteria[key], key, 0) > 0, "Positive criterion required: " + key)
    for key in ("maximum_recovery_s", "maximum_contact_slip_m_s", "locked_angle_tolerance_rad"):
        number(criteria[key], key, 0)
    need(-1 <= number(criteria["minimum_up_z"], "upright threshold") <= 1, "Invalid upright threshold")
    need(0 < number(criteria["minimum_support_fraction"], "support threshold") <= 1, "Invalid support threshold")
    forward = vector(spec["forward_axis"], "forward axis")
    need(abs(sum(v*v for v in forward)-1) <= 1e-8, "Forward axis must be unit length")
    contacts = spec["allowed_contact_links"]
    need(isinstance(contacts, list) and contacts and len(contacts) == len(set(contacts))
         and set(contacts) <= set(links), "Contacts must belong to remaining links")
    need(all(links[name]["collision_enabled"] for name in contacts), "Contact link has no collider")
    tracked, masked = {}, {}
    need(isinstance(spec["reference_channels"], dict) and spec["reference_channels"], "Reference channels required")
    for name, channel in spec["reference_channels"].items():
        need(channel["link"] in spec["morphology"]["links"], "Unknown tracking link")
        joint = channel["joint"]
        need(joint is None or joint in spec["morphology"]["joints"], "Unknown tracking joint")
        if joint is not None:
            need(channel["link"] == spec["morphology"]["joints"][joint]["child_link"],
                 "Joint tracking channels must identify the driven child link")
        need(channel["unit"] in ("m", "rad"), "Explicit reference error unit required")
        number(channel["maximum_error"], "reference error threshold", 0)
        if channel["link"] not in links:
            masked[name] = "link_removed"
        elif joint is not None and (joint not in joints or joints[joint]["state"] != "actuated"):
            masked[name] = "joint_" + spec["morphology"]["joints"][joint]["state"]
        else:
            tracked[name] = channel
    need(tracked, "No remaining reference channels to evaluate")
    need(any(channel["link"] == spec["morphology"]["root_link"] and channel["joint"] is None
             and channel["unit"] == "m" for channel in tracked.values()),
         "An unmasked root-position reference channel is required")
    samples = trace["samples"]
    need(isinstance(samples, list) and len(samples) >= 4, "Pre/post damage samples required")
    dt = number(trace["dt_s"], "physics sample interval", 0)
    need(0 < dt <= criteria["maximum_sample_gap_s"], "Unsupported or insufficient sampling cadence")
    for i, sample in enumerate(samples):
        number(sample["time_s"], "sample time", 0)
        need(type(sample["step"]) is int and sample["step"] >= 0, "Physics step required")
        if i:
            prior = samples[i-1]
            need(sample["step"] == prior["step"]+1 and abs(sample["time_s"]-prior["time_s"]-dt) < 1e-8,
                 "Missing or discontinuous damage samples")
        vector(sample["root_position_m"], "root position")
        need(-1 <= number(sample["up_z"], "root up") <= 1, "Invalid root orientation")
        need(sample["morphology_state"] == ("intact" if sample["time_s"] < event_time else "damaged"),
             "Sample morphology does not match event time")
        for key in ("fall_detected", "invalid_physics_state", "external_support_applied"):
            need(type(sample[key]) is bool, "Explicit physics flag required: " + key)
        for key in ("root_resets", "forbidden_collision_count"):
            need(type(sample[key]) is int and sample[key] >= 0, "Nonnegative event count required")
    before = [s for s in samples if s["time_s"] < event_time]
    after = [s for s in samples if s["time_s"] >= event_time]
    need(len(before) >= 2 and len(after) >= 2 and after[0]["time_s"] == event_time,
         "Damage must occur at a recorded interior physics step")
    gravity = number(receipt["gravity_m_s2"], "gravity", 0)
    need(gravity > 0, "Gravity must be enabled")
    weight = sum(link["mass_kg"] for link in links.values()) * gravity
    errors = {name: 0. for name in tracked}
    joint_excess, effort_excess, lock_error, maximum_slip = 0., 0., 0., 0.
    forbidden_contacts = set()
    recovery = stable_since = None
    for sample in after:
        need(set(sample["joint_position_rad"]) == set(joints)
             and set(sample["actuator_effort_Nm"]) == set(joints), "Incomplete surviving-joint measurements")
        for name, joint in joints.items():
            position = number(sample["joint_position_rad"][name], "joint position")
            effort = number(sample["actuator_effort_Nm"][name], "actuator effort")
            joint_excess = max(joint_excess, joint["lower_rad"]-position, position-joint["upper_rad"])
            effort_excess = max(effort_excess, abs(effort)-joint["max_effort_Nm"])
            if joint["state"] == "locked":
                lock_error = max(lock_error, abs(position-joint["locked_position_rad"]))
        need(set(sample["reference_errors"]) == set(tracked), "Remaining reference channels are incomplete")
        for name in tracked:
            errors[name] = max(errors[name], number(sample["reference_errors"][name], "reference error", 0))
        support, slip = 0., 0.
        for name, contact in sample["contacts"].items():
            if name not in contacts:
                forbidden_contacts.add(name)
            force = vector(contact["force_N"], "contact force")
            slip = max(slip, number(contact["slip_speed_m_s"], "contact slip", 0))
            if name in contacts:
                support += max(0., force[2])
        maximum_slip = max(maximum_slip, slip)
        stable = (sample["up_z"] >= criteria["minimum_up_z"]
                  and support >= criteria["minimum_support_fraction"]*weight
                  and slip <= criteria["maximum_contact_slip_m_s"]
                  and not set(sample["contacts"]) - set(contacts))
        stable_since = (sample["time_s"] if stable_since is None else stable_since) if stable else None
        if (recovery is None and stable_since is not None
                and sample["time_s"]-stable_since >= criteria["stable_window_s"]):
            recovery = sample["time_s"]-event_time
    def travel(rows):
        return sum((rows[-1]["root_position_m"][i]-rows[0]["root_position_m"][i])*forward[i]
                   for i in range(3))
    duration, distance = after[-1]["time_s"]-event_time, travel(after)
    checks = {
        "reported_native_host": isinstance(receipt["host"], str) and receipt["host"].casefold() == "l-gpc"
                                and isinstance(receipt["gpu_device"], str) and bool(receipt["gpu_device"].strip()),
        "completed_run": receipt["status"] == "completed",
        "free_root_and_self_collision": receipt["root_kinematic"] is False and receipt["self_collision_enabled"] is True,
        "event_during_forward_motion": travel(before) > 0,
        "survived": not any(s["fall_detected"] for s in samples),
        "valid_physics": not any(s["invalid_physics_state"] for s in samples),
        "no_forbidden_collisions": not any(s["forbidden_collision_count"] for s in samples),
        "no_resets_or_external_support": not any(s["root_resets"] or s["external_support_applied"] for s in samples),
        "joint_and_actuator_limits": joint_excess == 0 and effort_excess == 0,
        "locked_joints_fixed": lock_error <= criteria["locked_angle_tolerance_rad"],
        "allowed_contacts_only": not forbidden_contacts,
        "contact_slip_within_limit": maximum_slip <= criteria["maximum_contact_slip_m_s"],
        "post_damage_duration": duration >= criteria["minimum_post_damage_duration_s"],
        "forward_progress": distance >= criteria["minimum_forward_m"],
        "support_recovered": recovery is not None and recovery <= criteria["maximum_recovery_s"],
        "remaining_reference_channels": all(errors[name] <= channel["maximum_error"] for name, channel in tracked.items()),
    }
    return {
        "schema_version": 1, "kind": "damage_locomotion_criteria_report",
        "damage_locomotion_criteria_pass": all(checks.values()), "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "damage_locomotion_accepted": False, "walking_accepted": False,
        "physics_accepted": False, "flux_match": False, "native_provenance_verified": False,
        "damaged_candidate_sha256": spec["damaged_candidate_sha256"],
        "baseline_candidate_sha256": spec["baseline_candidate_sha256"],
        "reference_manifest_sha256": spec["reference_manifest_sha256"],
        "damage_event": event, "criteria": criteria, "morphology": spec["morphology"],
        "tracking_channels": tracked, "masked_channels": masked,
        "metrics": {"post_damage_duration_s": duration, "forward_displacement_m": distance,
                    "support_recovery_confirmation_s": recovery, "maximum_contact_slip_m_s": maximum_slip,
                    "maximum_joint_limit_excess_rad": joint_excess, "maximum_effort_excess_Nm": effort_excess,
                    "maximum_locked_joint_error_rad": lock_error, "maximum_reference_errors": errors,
                    "forbidden_contact_links": sorted(forbidden_contacts), "sample_count": len(samples)},
        "limitations": ["Offline evaluation of supplied measurements; hashes do not authenticate the native producer.",
                        "No damage producer is implemented here; intact traces are not accepted as damage evidence.",
                        "Seed and event phase record provenance, not a verified randomization distribution.",
                        "Tracking covers declared surviving channels only; no full-body or FLUX match is inferred.",
                        "This report never changes the intact walking gate or promotes a Pacific Gym run."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("spec", "receipt", "trace", "candidate", "reference-manifest", "out"):
        parser.add_argument("--"+name, required=True, type=Path)
    args = parser.parse_args()
    need(not args.out.exists(), "Refusing to replace an existing damage report")
    paths = {name: path.resolve(strict=True) for name, path in vars(args).items() if name != "out"}
    hashes = {name: sha(path) for name, path in paths.items()}
    spec, receipt, trace = (read(paths[name]) for name in ("spec", "receipt", "trace"))
    need(receipt["spec_sha256"] == trace["spec_sha256"] == hashes["spec"], "Unbound damage specification")
    need(receipt["trace_sha256"] == hashes["trace"], "Trace bytes do not match native receipt")
    need(spec["damaged_candidate_sha256"] == hashes["candidate"], "Damaged candidate bytes changed")
    need(spec["reference_manifest_sha256"] == hashes["reference_manifest"], "Reference manifest changed")
    result = evaluate(spec, receipt, trace)
    need(all(sha(path) == hashes[name] for name, path in paths.items()), "Input changed while reporting")
    result["evidence_sha256"] = hashes
    result["reporter_sha256"] = sha(Path(__file__))
    with args.out.open("x") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"report": str(args.out), "damage_locomotion_criteria_pass": result["damage_locomotion_criteria_pass"],
                      "damage_locomotion_accepted": False, "failed_checks": result["failed_checks"]}))


if __name__ == "__main__":
    main()
