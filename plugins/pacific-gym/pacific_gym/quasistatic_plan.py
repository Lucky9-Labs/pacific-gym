"""Find bounded weight-transfer and swing poses before spending a GPU trial.

These are kinematic feasibility proposals, not commanded root motion or proof
of balance. They still require combined-pose clearance and runtime validation.
"""

import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy.optimize import least_squares, minimize
from scipy.spatial import ConvexHull

from .articulation_audit import frame, source_basis
from .terrain import digest


def axis_rotation(axis, angle):
    vector = np.eye(3)["XYZ".index(axis)]
    x, y, z = vector
    skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    result = np.eye(4)
    result[:3, :3] += np.sin(angle)*skew + (1-np.cos(angle))*(skew @ skew)
    return result


class Model:
    def __init__(self, snapshot, source, minimum_root_up=.95,
                 foot_position_tolerance_m=.003, foot_orientation_tolerance=.02):
        if not .65 <= minimum_root_up <= 1:
            raise ValueError("Root-up threshold must be between the diagnostic fall threshold 0.65 and 1")
        self.minimum_root_up = minimum_root_up
        self.candidate_sha256 = snapshot["usd_sha256"]
        self.cooked_feet_sha256 = None
        if not 0 < foot_position_tolerance_m <= .003 or not 0 < foot_orientation_tolerance <= .02:
            raise ValueError("Foot tolerances may only tighten the original planning limits")
        self.foot_position_tolerance_m = foot_position_tolerance_m
        self.foot_orientation_tolerance = foot_orientation_tolerance
        self.bodies = {b["path"]: b for b in snapshot["bodies"]}
        self.joints = snapshot["joints"]
        self.revolute = [j for j in self.joints if "axis" in j]
        self.names = [j["name"] for j in self.revolute]
        self.limits = np.radians([j["limits_deg"] for j in self.revolute])
        children = {j["body1"][0] for j in self.joints}
        roots = set(self.bodies)-children
        if len(roots) != 1 or len(children) != len(self.joints):
            raise ValueError("Expected a directed articulation tree")
        self.root = roots.pop()
        self.frames = {
            j["name"]: (frame(j["local_pos0"], j["local_rot0_wxyz"]),
                        np.linalg.inv(frame(j["local_pos1"], j["local_rot1_wxyz"])))
            for j in self.joints
        }
        self.foot_paths = {side: next(p for p in self.bodies if p.endswith("/foot"+side))
                           for side in ("L", "R")}
        self.foot_vertices = {}
        self.foot_rest = {}
        self.polygons = {}
        terrain = [m for m in source["meshes"] if m["category"] == "terrain"]
        self.ground = max(np.max(np.asarray(m["vertices"])[:, 2]) for m in terrain)
        for side, path in self.foot_paths.items():
            world = np.concatenate([m["vertices"] for m in source["meshes"]
                                    if m["owner"] == "foot"+side and m["category"] == "robot"])
            rest = np.array(self.bodies[path]["world_matrix"], float)
            inverse = np.linalg.inv(rest)
            self.foot_vertices[side] = world @ inverse[:3, :3].T + inverse[:3, 3]
            lowest = float(world[:, 2].min())
            contact = world[world[:, 2] <= lowest+.002, :2]
            self.polygons[side] = ConvexHull(contact)
            rest[2, 3] += self.ground-lowest
            self.foot_rest[side] = rest
        self.basis = source_basis(source)
        self.reference_q = np.zeros(len(self.names))
        self.terrain = None
        self.terrain_placements = {}

    def use_local_terrain(self, terrain):
        """Bind both stance footprints to hash-verified candidate triangles."""
        self.terrain = terrain
        for side in ("L", "R"):
            placement = self.place_foot_on_terrain(side, self.foot_rest[side][:2, 3])
            self.set_terrain_placement(side, placement)

    def use_cooked_feet(self, cooked, cooked_sha256):
        if (self.terrain_placements or cooked["candidate_sha256"] != self.candidate_sha256
                or cooked["vertex_frame"] != "rigid-body-local"
                or len(cooked_sha256) != 64):
            raise ValueError("Bind exact cooked feet before placing feet on terrain")
        for side, path in self.foot_paths.items():
            self.foot_vertices[side] = np.unique(np.concatenate([
                hull["vertices"] for mesh in cooked["meshes"] if mesh["body"] == path
                for hull in mesh["hulls"]]), axis=0)
        self.cooked_feet_sha256 = cooked_sha256

    def place_foot_on_terrain(self, side, xy, previous=None):
        if self.cooked_feet_sha256:
            return self.terrain.place_cooked_foot(
                self.foot_vertices[side], xy, self.cooked_feet_sha256,
                preferred_normal=None if previous is None else previous["plane_normal"],
                preferred_support_normal_local=None if previous is None else
                previous["sole_support_normal_local"],
                heading_deg=0. if previous is None else previous.get("heading_deg",0.))
        return self.terrain.place_foot(
            self.foot_vertices[side], xy,
            preferred_normal=None if previous is None else previous["plane_normal"],
            heading_deg=0. if previous is None else previous.get("heading_deg",0.))

    def validate_terrain_placement(self, side, placement):
        if (self.terrain is None
                or placement["terrain_world_geometry_sha256"] != self.terrain.geometry_sha256
                or placement["candidate_sha256"] != self.terrain.candidate_sha256
                or self.cooked_feet_sha256 and placement.get("cooked_sha256") != self.cooked_feet_sha256
                or placement["foot_local_vertices_sha256"] != digest(self.foot_vertices[side].tolist())
                or placement["query_sha256"] != digest({k: v for k, v in placement.items() if k != "query_sha256"})):
            raise ValueError("Foot placement does not bind this terrain")

    def set_terrain_placement(self, side, placement):
        self.validate_terrain_placement(side, placement)
        self.terrain_placements[side] = placement
        self.foot_rest[side] = np.asarray(placement["world_matrix"], dtype=float)
        self.polygons[side] = ConvexHull(np.asarray(placement["support_polygon_xy_m"], dtype=float))

    def set_reference_pose(self, q_deg):
        """Use an explicit bounded stance as a planning reference, never mutate USD."""
        if set(q_deg)-set(self.names):
            raise ValueError("Reference pose names an unknown joint")
        q = np.radians([q_deg.get(name, 0.) for name in self.names])
        if (not np.isfinite(q).all() or np.any(q < self.limits[:, 0])
                or np.any(q > self.limits[:, 1])):
            raise ValueError("Reference pose exceeds the authored joint limits")
        transforms = self.transforms(q, "L")
        feet = {side: transforms[path].copy() for side, path in self.foot_paths.items()}
        vertices = {}
        for side, foot in feet.items():
            if not np.allclose(foot[:3, :3], self.foot_rest[side][:3, :3], atol=1e-6):
                raise ValueError("Reference stance requires the original flat foot orientations")
            vertices[side] = self.foot_vertices[side] @ foot[:3, :3].T + foot[:3, 3]
        lowest = min(points[:, 2].min() for points in vertices.values())
        for side, foot in feet.items():
            foot[2, 3] += self.ground-lowest
            points = vertices[side]
            contact = points[points[:, 2] <= points[:, 2].min()+.002, :2]
            self.polygons[side] = ConvexHull(contact)
        self.foot_rest = feet
        self.reference_q = q

    def transforms(self, q, stance):
        angles = dict(zip(self.names, q))
        relative = {self.root: np.eye(4)}
        pending = list(self.joints)
        while pending:
            moved = False
            for joint in pending[:]:
                a, b = joint["body0"][0], joint["body1"][0]
                if a not in relative:
                    continue
                left, right = self.frames[joint["name"]]
                turn = axis_rotation(joint["axis"], angles[joint["name"]]) if "axis" in joint else np.eye(4)
                relative[b] = relative[a] @ left @ turn @ right
                pending.remove(joint)
                moved = True
            if not moved:
                raise ValueError("Joint graph cannot be resolved")
        root_world = self.foot_rest[stance] @ np.linalg.inv(relative[self.foot_paths[stance]])
        return {path: root_world @ transform for path, transform in relative.items()}

    def center_of_mass(self, transforms):
        total = sum(b["mass_kg"] for b in self.bodies.values())
        return sum(b["mass_kg"]*(transforms[path] @ [*b["center_of_mass"], 1])[:3]
                   for path, b in self.bodies.items()) / total

    def proposal(self, stance, lift, travel, swing_inset=0., support_mode="stance",
                 target_terrain_placement=None, additional_constraints=None,
                 posture_objective="minimum_angle_norm"):
        swing = "R" if stance == "L" else "L"
        if support_mode not in ("stance", "both") or support_mode == "both" and (lift or travel or swing_inset):
            raise ValueError("Double support is only available for an unmoved neutral pose")
        if posture_objective not in ("minimum_angle_norm", "nearest_reference"):
            raise ValueError("Unknown posture objective")
        posture_origin = (self.reference_q if posture_objective == "nearest_reference"
                          else np.zeros(len(self.names)))
        polygon = (self.polygons[stance] if support_mode == "stance" else
                   ConvexHull(np.concatenate([p.points[p.vertices] for p in self.polygons.values()])))
        target = self.foot_rest[swing].copy()
        target[:3, 3] += np.array(self.basis["forward"])*travel + [0, 0, lift]
        lateral = np.asarray(self.basis["lateral"])
        separation = float(np.dot(
            self.foot_rest[stance][:3, 3]-self.foot_rest[swing][:3, 3], lateral))
        if not 0 <= swing_inset < abs(separation):
            raise ValueError("Swing inset must be nonnegative and cannot cross the stance origin")
        target[:3, 3] += lateral*np.sign(separation)*swing_inset
        placement = None
        if target_terrain_placement is not None and self.terrain is None:
            raise ValueError("An explicit terrain target requires bound terrain")
        if self.terrain is not None:
            placement = target_terrain_placement or self.place_foot_on_terrain(
                swing, target[:2, 3], previous=self.terrain_placements[swing])
            self.validate_terrain_placement(swing, placement)
            if not np.allclose(np.asarray(placement["world_matrix"])[:2, 3], target[:2, 3], atol=1e-8, rtol=0):
                raise ValueError("Explicit terrain target changes the requested XY position")
            target = np.asarray(placement["world_matrix"], dtype=float).copy()
            target[2, 3] += lift
            normal = np.asarray(placement["plane_normal"])
            offset = placement["plane_offset_m"]
            target_points = self.foot_vertices[swing]@target[:3, :3].T+target[:3, 3]
            region_low, region_high = target_points[:, :2].min(0)-.02, target_points[:, :2].max(0)+.02
            region = np.array([[region_low[0], region_low[1]], [region_high[0], region_low[1]],
                               [region_high[0], region_high[1]], [region_low[0], region_high[1]]])
            region_certificate = self.terrain.support_certificate(normal, offset, region)
            if not region_certificate["plane_above_terrain"]:
                raise ValueError("Target support plane is not conservative over the motion tolerance region")

        def clearance(points):
            if placement is None:
                return float(points[:, 2].min()-self.ground), np.empty(0)
            # The plane is proven above all terrain triangles over this region.
            # Its minimum over mesh vertices also bounds every triangle interior.
            lower_bound = float(np.min((points@normal-offset)/normal[2]))
            region_constraints = np.r_[points[:, :2].min(0)-region_low,
                                       region_high-points[:, :2].max(0)]
            return lower_bound, region_constraints

        def residual(q):
            transforms = self.transforms(q, stance)
            com = self.center_of_mass(transforms)
            foot = transforms[self.foot_paths[swing]]
            # Rotation matrix residual avoids Euler singularities and tracks a flat foot.
            # Any interior support point is useful. Forcing the polygon centroid
            # can reject valid balance over a heel or toe.
            edge_violation = polygon.equations[:, :2] @ com[:2] + polygon.equations[:, 2] + .005
            return np.r_[100*np.maximum(edge_violation, 0),
                         10*(foot[:3, 3]-target[:3, 3]),
                         (foot[:3, :3]-target[:3, :3]).ravel(),
                         .01*(q-posture_origin),
                         10*max(0., self.minimum_root_up-transforms[self.root][2, 2])]

        projection = posture_objective == "nearest_reference"
        best = (SimpleNamespace(x=self.reference_q.copy(), success=False,
                                cost=float(np.dot(residual(self.reference_q), residual(self.reference_q))/2),
                                message="Least-squares skipped for constrained trajectory projection")
                if projection else None)
        for magnitude in (() if projection else (0., .15, -.15)):
            seed = self.reference_q + np.array(
                [magnitude if "roll" in name else 0. for name in self.names])
            seed = np.clip(seed, self.limits[:, 0]+1e-8, self.limits[:, 1]-1e-8)
            result = least_squares(residual, seed, bounds=(self.limits[:, 0], self.limits[:, 1]),
                                   max_nfev=400, ftol=1e-10, xtol=1e-10, gtol=1e-10)
            if best is None or result.cost < best.cost:
                best = result
        def constraints(q):
            transforms = self.transforms(q, stance)
            com = self.center_of_mass(transforms)
            foot = transforms[self.foot_paths[swing]]
            points = self.foot_vertices[swing] @ foot[:3, :3].T + foot[:3, 3]
            margins = -(polygon.equations[:, :2] @ com[:2] + polygon.equations[:, 2])
            delta = foot[:3, 3]-target[:3, 3]
            orientation = (foot[:3, :3]-target[:3, :3]).ravel()
            height, region_constraints = clearance(points)
            extra = (np.empty(0) if additional_constraints is None else
                     np.asarray(additional_constraints(q), dtype=float).reshape(-1))
            if not np.isfinite(extra).all():
                raise ValueError("Additional planning constraints must be finite")
            return np.r_[margins-.005, self.foot_position_tolerance_m-np.linalg.norm(delta),
                         self.foot_orientation_tolerance-orientation,
                         self.foot_orientation_tolerance+orientation,
                         height-lift+self.foot_position_tolerance_m, region_constraints,
                         transforms[self.root][2, 2]-self.minimum_root_up, extra]
        # Hard constraints prevent a low aggregate residual being called feasible
        # while it trades a support violation against a better swing target.
        reference_is_feasible = bool(constraints(self.reference_q).min() >= -1e-7)
        use_reference = reference_is_feasible or posture_objective == "nearest_reference"
        refinement_seed = self.reference_q if use_reference else best.x
        optimizer_ftol = 1e-8 if projection else 1e-10
        refined = minimize(lambda q: float(np.dot(q-posture_origin, q-posture_origin)),
                           refinement_seed, method="SLSQP",
                           bounds=list(zip(self.limits[:, 0], self.limits[:, 1])),
                           constraints={"type": "ineq", "fun": constraints},
                           options={"maxiter": 300, "ftol": optimizer_ftol})
        # A successful feasible constrained solve is the final solution even if
        # the seed fit had a marginally larger floating-point constraint margin.
        if refined.success and constraints(refined.x).min() >= -1e-7:
            selected, selected_stage = refined, "constrained_refinement"
        elif best.success and constraints(best.x).min() >= -1e-7:
            selected, selected_stage = best, "initial_least_squares"
        elif constraints(refined.x).min() > constraints(best.x).min():
            selected, selected_stage = refined, "constrained_refinement"
        else:
            selected, selected_stage = best, "initial_least_squares"
        q = selected.x
        transforms = self.transforms(q, stance)
        com = self.center_of_mass(transforms)
        foot = transforms[self.foot_paths[swing]]
        vertices = self.foot_vertices[swing] @ foot[:3, :3].T + foot[:3, 3]
        margins = -(polygon.equations[:, :2] @ com[:2] + polygon.equations[:, 2])
        orientation_error = float(np.max(abs(foot[:3, :3]-target[:3, :3])))
        position_error = float(np.linalg.norm(foot[:3, 3]-target[:3, 3]))
        min_height, _ = clearance(vertices)
        feasible = bool(constraints(q).min() >= -1e-7)
        result = {
            "stance": stance, "swing": swing, "lift_target_m": lift,
            "support_mode": support_mode,
            "posture_objective": posture_objective,
            "forward_target_m": travel, "swing_inset_target_m": swing_inset,
            "foot_position_tolerance_m": self.foot_position_tolerance_m,
            "foot_orientation_matrix_tolerance": self.foot_orientation_tolerance,
            "swing_target_world_matrix": target.tolist(),
            "optimizer_success": bool(selected.success),
            "optimizer_message": selected.message,
            "selected_optimizer_stage": selected_stage,
            "initial_optimizer_success": bool(best.success),
            "initial_optimizer_message": best.message,
            "initial_optimizer_cost": best.cost,
            "constraint_refinement_success": bool(refined.success),
            "constraint_refinement_message": refined.message,
            "constraint_refinement_ftol": optimizer_ftol,
            "constraint_refinement_seed": ("feasible_reference" if reference_is_feasible else
                                          "projection_reference" if use_reference else "least_squares"),
            "minimum_constraint_residual": float(constraints(q).min()),
            "q_deg": dict(zip(self.names, map(float, np.degrees(q)))),
            "center_of_mass_m": com.tolist(), "support_polygon_margin_m": float(margins.min()),
            "swing_position_error_m": position_error,
            "swing_orientation_matrix_error": orientation_error,
            "swing_lowest_point_above_ground_m": min_height,
            "root_up_z": float(transforms[self.root][2, 2]),
            "root_world_matrix": transforms[self.root].tolist(),
            "bounded_kinematic_pose_found": feasible and bool(selected.success),
            "hard_constraints_satisfied": feasible,
            "root_transform_is_fk_result_not_a_runtime_command": True,
            "self_collision_screened": False, "physics_accepted": False,
            "foot_geometry_kind": ("native_cooked_convex_vertices" if self.cooked_feet_sha256
                                   else "source_mesh_vertices"),
            "cooked_feet_sha256": self.cooked_feet_sha256,
        }
        if additional_constraints is not None:
            result["additional_constraint_residuals"] = np.asarray(
                additional_constraints(q), dtype=float).reshape(-1).tolist()
        if placement is not None:
            heights, triangle_ids = self.terrain.query(vertices[:, :2])
            result.update(
                clearance_reference="Conservative supporting plane of hash-bound local terrain triangles",
                swing_clearance_lower_bound_m=min_height,
                swing_source_vertex_local_terrain_gap_m=float((vertices[:, 2]-heights).min()),
                swing_queried_triangle_ids=sorted({i for ids in triangle_ids for i in ids}),
                swing_target_terrain_placement=placement,
                swing_plane_tolerance_region_certificate=region_certificate,
                stance_terrain_placement=self.terrain_placements[stance])
        else:
            result["clearance_reference"]="Highest terrain point; flat-foot planning assumption"
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "source-geometry", "out"):
        parser.add_argument("--"+name, required=True, type=Path)
    parser.add_argument("--minimum-root-up", type=float, default=.95,
                        help="Explicit planning posture assumption; default allows about 18 degrees tilt")
    parser.add_argument("--swing-inset-m", type=float, default=0.,
                        help="Move the swing target toward the stance foot without changing joint limits")
    parser.add_argument("--reference-pose", type=Path,
                        help="Explicit q_deg JSON for a bounded flat-foot planning stance")
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    snapshot = json.loads(args.snapshot.read_text())
    model = Model(snapshot, json.loads(args.source_geometry.read_text()), args.minimum_root_up)
    if args.reference_pose:
        model.set_reference_pose(json.loads(args.reference_pose.read_text())["q_deg"])
    result = {
        "schema_version": 1, "candidate_sha256": snapshot["usd_sha256"],
        "basis": model.basis, "method": "Source-geometry contact patch, authored joint frames/limits and estimated link CoM.",
        "minimum_root_up_planning_assumption": args.minimum_root_up,
        "reference_q_deg": dict(zip(model.names, map(float, np.degrees(model.reference_q)))),
        "reference_foot_world_matrices": {side: matrix.tolist()
                                        for side, matrix in model.foot_rest.items()},
        "planner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "poses": [model.proposal(side, lift, travel, args.swing_inset_m) for side in ("L", "R")
                  for lift, travel in ((0., 0.), (.03, .05))],
        "limitations": ["Quasi-static approximation; no momentum or torque constraints.",
                        "No combined-pose self-collision check.",
                        "No trajectory or runtime acceptance."],
        "physics_accepted": False,
        "evidence_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest()
                            for name, path in vars(args).items()
                            if isinstance(path, Path) and name != "out"},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"report": str(args.out), "poses": result["poses"]}))


if __name__ == "__main__":
    main()
