"""Screen pinned cooked convex geometry over the declared articulation sweep.

Every pass requires a separating-plane lower-bound certificate. A small
feasible point-pair distance proves failure; solver success alone never proves
clearance. Discrete samples do not certify continuous or arbitrary joint motion.
"""

import argparse
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import linprog, minimize

from .articulation_audit import frame
from .quasistatic_plan import axis_rotation


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_case_witnesses(case, report_path, candidate_sha256):
    if "witness_file" not in case:
        return case
    reference = case["witness_file"]
    path = (report_path.parent/reference["path"]).resolve(strict=True)
    if not path.is_relative_to(report_path.parent.resolve()) or sha(path) != reference["sha256"]:
        raise ValueError("External witness path/hash mismatch")
    payload = json.loads(gzip.decompress(path.read_bytes()))
    if (payload["candidate_sha256"] != candidate_sha256
            or payload["label"] != case["label"] or payload["q_deg"] != case["q_deg"]
            or len(payload["violations"]) != case["violation_count"]
            or len(payload["indeterminate_pairs"]) != case["indeterminate_count"]):
        raise ValueError("External witness candidate/pose/count mismatch")
    return {**case, "violations": payload["violations"],
            "indeterminate_pairs": payload["indeterminate_pairs"]}


class Kinematics:
    def __init__(self, snapshot):
        self.bodies = {b["path"]: np.asarray(b["world_matrix"], float) for b in snapshot["bodies"]}
        self.joints = snapshot["joints"]
        self.joint_names = {j["name"] for j in self.joints if "axis" in j}
        children = [j["body1"][0] for j in self.joints]
        roots = set(self.bodies)-set(children)
        if len(roots) != 1 or len(set(children)) != len(children):
            raise ValueError("Expected a directed articulation tree")
        self.root = roots.pop()
        self.frames = {j["name"]: (
            frame(j["local_pos0"], j["local_rot0_wxyz"]),
            np.linalg.inv(frame(j["local_pos1"], j["local_rot1_wxyz"]))) for j in self.joints}
        self.exclusions = {frozenset((j["body0"][0], j["body1"][0]))
                           for j in self.joints if j["collision_enabled"] is False}
        for mesh in snapshot["meshes"]:
            for ancestor in mesh.get("collision_ancestors", []):
                for target in ancestor["relationships"].get("physics:filteredPairs", []):
                    if ancestor["path"] in self.bodies and target in self.bodies:
                        self.exclusions.add(frozenset((ancestor["path"], target)))
                    else:
                        raise ValueError("Non-body filtered pair needs explicit handling")
        zero = self.transforms({})
        if any(np.max(abs(zero[p]-m)) > 1e-6 for p, m in self.bodies.items()):
            raise ValueError("Joint forward kinematics do not reproduce the USD rest frames")

    def transforms(self, angles):
        if set(angles)-self.joint_names:
            raise ValueError("Unknown joint in pose")
        transforms = {self.root: self.bodies[self.root]}
        pending = list(self.joints)
        while pending:
            progressed = False
            for joint in pending[:]:
                a, b = joint["body0"][0], joint["body1"][0]
                if a not in transforms:
                    continue
                left, right = self.frames[joint["name"]]
                turn = np.eye(4)
                if "axis" in joint:
                    angle = angles.get(joint["name"], 0.)
                    lower, upper = joint["limits_deg"]
                    if not math.isfinite(angle) or not lower-1e-7 <= angle <= upper+1e-7:
                        raise ValueError("Pose exceeds authored joint limits")
                    turn = axis_rotation(joint["axis"], math.radians(angle))
                transforms[b] = transforms[a] @ left @ turn @ right
                pending.remove(joint)
                progressed = True
            if not progressed:
                raise ValueError("Joint graph cannot be resolved")
        return transforms


def isolated_cases(snapshot, config, step):
    families = {}
    for joint in snapshot["joints"]:
        if "axis" not in joint:
            continue
        family, side = joint["name"].rsplit("_", 1)
        families[({"foot_roll": "ankle_roll"}.get(family, family), side)] = joint
    cases = [{"label": "rest", "q_deg": {}}]
    for family, limits in config["clearance"]["joint_sweep_degrees"].items():
        for side in ("L", "R"):
            joint = families.get((family, side))
            if not joint or joint["limits_deg"][0] > limits[0] or joint["limits_deg"][1] < limits[1]:
                raise ValueError("Authored articulation does not cover the configured sweep")
            lower, upper = limits
            angles = {float(lower), float(upper), 0.}
            angles.update(float(n*step) for n in range(math.ceil(lower/step), math.floor(upper/step)+1))
            for angle in sorted(angles):
                if angle and lower <= angle <= upper:
                    cases.append({"label": f"{joint['name']}={angle:g}",
                                  "q_deg": {joint["name"]: angle}})
    return cases


def transformed_parts(data, transforms, cache=None):
    parts = []
    for part in data["meshes"]:
        transform = transforms[part["body"]]
        key = (id(data), part["path"])
        if cache is not None and key in cache and np.array_equal(cache[key][0], transform):
            parts.append(cache[key][1])
            continue
        rotation, position = transform[:3, :3], transform[:3, 3]
        if np.max(abs(rotation.T @ rotation-np.eye(3))) > 1e-6:
            raise ValueError("Nonrigid body transform")
        hulls = []
        for hull in part["hulls"]:
            vertices = np.asarray(hull["vertices"]) @ rotation.T+position
            planes = np.asarray(hull["planes"])
            normals = planes[:, :3] @ rotation.T
            hulls.append({"index": hull["index"], "vertices": vertices,
                          "planes": np.c_[normals, planes[:, 3]-normals @ position],
                          "lo": vertices.min(0), "hi": vertices.max(0)})
        result = {"path": part["path"], "body": part["body"], "hulls": hulls,
                  "lo": np.min([h["lo"] for h in hulls], axis=0),
                  "hi": np.max([h["hi"] for h in hulls], axis=0)}
        parts.append(result)
        if cache is not None:
            cache[key] = (transform.copy(), result)
    return parts


def aabb_gap(a, b):
    return float(np.linalg.norm(np.maximum(0, np.maximum(a["lo"]-b["hi"], b["lo"]-a["hi"]))))


def pair_distance(a, b, target):
    av, bv, ae, be = a["vertices"], b["vertices"], a["planes"], b["planes"]
    # Any verified separating direction provides a lower bound on distance.
    directions = np.r_[ae[:, :3], -be[:, :3]]
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    gaps = (bv @ directions.T).min(0)-(av @ directions.T).max(0)
    if gaps.max() >= target:
        return {"status": "clear", "separating_lower_bound_m": float(gaps.max())}
    equations = np.r_[ae, be]
    intersection = linprog(np.zeros(3), A_ub=equations[:, :3], b_ub=-equations[:, 3],
                          bounds=[(None, None)]*3, method="highs")
    if intersection.success and np.max(equations[:, :3] @ intersection.x+equations[:, 3]) <= 1e-8:
        return {"status": "violation", "distance_upper_bound_m": 0.,
                "intersection_point_m": intersection.x.tolist()}
    if intersection.status not in (0, 2):
        return {"status": "indeterminate", "reason": intersection.message}
    matrix = np.zeros((len(ae)+len(be), 6))
    matrix[:len(ae), :3], matrix[len(ae):, 3:] = ae[:, :3], be[:, :3]
    bounds = -np.r_[ae[:, 3], be[:, 3]]

    def objective(x):
        delta = x[:3]-x[3:]
        return float(delta @ delta)

    def gradient(x):
        delta = 2*(x[:3]-x[3:])
        return np.r_[delta, -delta]

    result = minimize(objective, np.r_[av.mean(0), bv.mean(0)], jac=gradient,
                      constraints={"type": "ineq", "fun": lambda x: bounds-matrix @ x,
                                   "jac": lambda x: -matrix},
                      method="SLSQP", options={"ftol": 1e-15, "maxiter": 150})
    feasible = np.isfinite(result.x).all() and np.max(matrix @ result.x-bounds) <= 1e-8
    distance = float(np.linalg.norm(result.x[:3]-result.x[3:]))
    if feasible and distance < target-1e-7:
        return {"status": "violation", "distance_upper_bound_m": distance,
                "point_a_m": result.x[:3].tolist(), "point_b_m": result.x[3:].tolist()}
    if distance > 1e-12 and np.isfinite(distance):
        normal = (result.x[3:]-result.x[:3])/distance
        lower = float((bv @ normal).min()-(av @ normal).max())
        if lower >= target:
            return {"status": "clear", "separating_lower_bound_m": lower}
    return {"status": "indeterminate", "reason": result.message,
            "feasible_point_pair": bool(feasible), "distance_upper_bound_m": distance}


def clipping_proposals(a, b, part_a, part_b, transforms, target):
    directions = np.r_[a["planes"][:, :3], b["planes"][:, :3]]
    directions = np.r_[directions, -directions]
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    directions = np.unique(np.round(directions, 8), axis=0)
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    av, bv = a["vertices"], b["vertices"]
    escape = (av @ directions.T).max(0)-(bv @ directions.T).min(0)+target
    proposals = []
    for index in np.argsort(escape)[:3]:
        normal = directions[index]
        for part, n, offset in (
                (part_a, normal, float((bv @ normal).min()-target)),
                (part_b, -normal, float(-(av @ normal).max()-target))):
            matrix = transforms[part["body"]]
            proposals.append({
                "collider": part["path"], "body": part["body"],
                "keep_halfspace_normal_body": (matrix[:3, :3].T @ n).tolist(),
                "keep_halfspace_offset_body_m": float(offset-n @ matrix[:3, 3]),
                "estimated_axis_escape_m": float(escape[index]),
                "shape_budget_checked": False, "mutation_authorized_by_this_proposal": False,
            })
    return proposals


def screen_case(data, model, case, target, geometry_cache=None, all_witnesses=False,
                pair_cache=None):
    transforms = model.transforms(case["q_deg"])
    parts = transformed_parts(data, transforms, geometry_cache)
    violations, unknown, evaluated = [], [], 0
    for a, b in itertools.combinations(parts, 2):
        if a["body"] == b["body"] or frozenset((a["body"], b["body"])) in model.exclusions:
            continue
        if aabb_gap(a, b) >= target:
            continue
        cache_key = (a["path"], b["path"], target, all_witnesses)
        if pair_cache is not None and cache_key in pair_cache:
            cached_a, cached_b, cached_v, cached_u, cached_count = pair_cache[cache_key]
            # Retain object references: transformed_parts only reuses an object
            # for exactly equal transforms. No rounded-pose or approximate reuse.
            if cached_a is a and cached_b is b:
                violations.extend(cached_v)
                unknown.extend(cached_u)
                evaluated += cached_count
                continue
        start_v, start_u, start_count = len(violations), len(unknown), evaluated
        blo, bhi = np.array([h["lo"] for h in b["hulls"]]), np.array([h["hi"] for h in b["hulls"]])
        found = False
        for ah in a["hulls"]:
            gaps = np.linalg.norm(np.maximum(0, np.maximum(ah["lo"]-bhi, blo-ah["hi"])), axis=1)
            for index in np.flatnonzero(gaps < target):
                bh = b["hulls"][index]
                evaluated += 1
                result = pair_distance(ah, bh, target)
                if result["status"] == "clear":
                    continue
                result.update(collider_a=a["path"], collider_b=b["path"],
                              body_a=a["body"], body_b=b["body"],
                              hull_a=ah["index"], hull_b=bh["index"])
                if result["status"] == "violation":
                    result["repair_proposals"] = clipping_proposals(ah, bh, a, b, transforms, target)
                    violations.append(result)
                    if not all_witnesses:
                        found = True
                        break
                else:
                    unknown.append(result)
            if found:
                break
        if pair_cache is not None:
            pair_cache[cache_key] = (a, b, violations[start_v:], unknown[start_u:],
                                    evaluated-start_count)
    return {**case, "tested_hull_pairs": evaluated, "violations": violations,
            "indeterminate_pairs": unknown, "clearance_pass": not violations and not unknown,
            "certified_distance_lower_bound_m": target if not violations and not unknown else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "cooked", "cook-receipt", "config", "out"):
        parser.add_argument("--"+name, required=True, type=Path)
    parser.add_argument("--step", type=float)
    parser.add_argument("--poses", type=Path)
    parser.add_argument("--all-witnesses", action="store_true",
                        help="Record every failing hull pair instead of the first per mesh pair")
    parser.add_argument("--external-witnesses", action="store_true",
                        help="Write immutable compressed pose witnesses beside a compact report")
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    snapshot = json.loads(args.snapshot.read_text())
    receipt = json.loads(args.cook_receipt.read_text())
    config = json.loads(args.config.read_text())
    if receipt["status"] != "completed" or receipt["output_sha256"] != sha(args.cooked):
        raise ValueError("Cook receipt is incomplete or its data hash changed")
    data = json.loads(gzip.decompress(args.cooked.read_bytes()))
    if (data["candidate_sha256"] != snapshot["usd_sha256"]
            or data["snapshot_sha256"] != sha(args.snapshot)
            or receipt["candidate_sha256"] != snapshot["usd_sha256"]):
        raise ValueError("Cook/snapshot candidate identity mismatch")
    expected = {m["path"]: m["collision_state_sha256"] for m in snapshot["meshes"]
                if m["collision"] and m["path"].startswith("/World/Robot/")}
    if len(data["meshes"]) != len(expected) or {
            m["path"]: m["snapshot_collision_fingerprint"] for m in data["meshes"]} != expected:
        raise ValueError("Cooked collider set or fingerprint is incomplete")
    step = args.step or config["clearance"]["coarse_step_degrees"]
    if step not in (config["clearance"]["coarse_step_degrees"], config["clearance"]["refinement_step_degrees"]):
        raise ValueError("Use a declared coarse or refinement step")
    cases = isolated_cases(snapshot, config, step)
    if args.poses:
        pose_document = json.loads(args.poses.read_text())
        if (pose_document.get("candidate_sha256") is not None
                and pose_document["candidate_sha256"] != snapshot["usd_sha256"]):
            raise ValueError("Combined poses were prepared for a different candidate")
        cases = [{"label": "combined_"+str(i), "q_deg": pose["q_deg"]}
                 for i, pose in enumerate(pose_document["poses"])]
        if not cases:
            raise ValueError("Combined pose set is empty")
    model = Kinematics(snapshot)
    target = config["clearance"]["target_surface_clearance_m"]
    report = {"schema_version": 1, "candidate_sha256": snapshot["usd_sha256"],
              "status": "running", "target_clearance_m": target,
              "case_count_planned": len(cases), "step_degrees": step if not args.poses else None,
              "scope": "Discrete configured isolated poses" if not args.poses else "Explicit combined poses",
              "witness_scope": "Every failing hull pair" if args.all_witnesses else "First failing hull pair per mesh pair",
              "continuous_clearance_qualified": False, "physics_accepted": False,
              "excluded_body_pairs": [sorted(pair) for pair in sorted(model.exclusions, key=lambda pair: sorted(pair))],
              "script_sha256": sha(Path(__file__)), "cases": [],
              "evidence_sha256": {name: sha(path) for name, path in vars(args).items()
                                  if isinstance(path, Path) and name != "out"}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    witness_dir = args.out.parent/(args.out.stem+"-witnesses")
    if args.external_witnesses:
        witness_dir.mkdir(exist_ok=False)
    geometry_cache = {}
    pair_cache = {} if args.all_witnesses else None
    for index, case in enumerate(cases):
        result = screen_case(data, model, case, target, geometry_cache, args.all_witnesses,
                             pair_cache)
        violation_count, unknown_count = len(result["violations"]), len(result["indeterminate_pairs"])
        if args.external_witnesses:
            witness_path = witness_dir/f"{index:04d}.json.gz"
            payload = {"candidate_sha256": snapshot["usd_sha256"], **case,
                       "violations": result.pop("violations"),
                       "indeterminate_pairs": result.pop("indeterminate_pairs")}
            witness_path.write_bytes(gzip.compress(json.dumps(payload, allow_nan=False).encode(), mtime=0))
            result.update(violation_count=violation_count, indeterminate_count=unknown_count,
                          witness_file={"path": str(witness_path.relative_to(args.out.parent)),
                                        "sha256": sha(witness_path)})
        report["cases"].append(result)
        report["cases_passed"] = sum(c["clearance_pass"] for c in report["cases"])
        report["status"] = "completed" if len(report["cases"]) == len(cases) else "running"
        report["all_sampled_cases_pass"] = report["status"] == "completed" and report["cases_passed"] == len(cases)
        temp = args.out.with_suffix(".tmp")
        temp.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
        temp.replace(args.out)
        print(case["label"], result["clearance_pass"], violation_count,
              unknown_count, flush=True)


if __name__ == "__main__":
    main()
