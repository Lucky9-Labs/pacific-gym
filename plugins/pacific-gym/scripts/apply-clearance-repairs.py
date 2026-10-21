"""Apply local Boolean proposals with cumulative source-shape rejection gates.

Use blender-derive with source geometry, authored collider export, repair
recipe, and configuration. Outputs are a new collider export and a report.
The report never claims cooked clearance or physics acceptance.
"""

import argparse
import copy
import gzip
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == ".gz" else data)


def triangulate(faces):
    return [(f[0], f[i], f[i+1]) for f in faces for i in range(1, len(f)-1)]


def object_from(name, vertices, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def delete_object(obj):
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def triangulate_object(obj):
    # Exact Booleans can emit concave n-gons. A triangle fan is not a valid
    # tessellation for those faces, so normalize with Blender before any BVH.
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
        bm.to_mesh(obj.data)
        obj.data.update()
    finally:
        bm.free()


def geometry(obj):
    return ([list(v.co) for v in obj.data.vertices],
            [list(p.vertices) for p in obj.data.polygons])


def point_triangle_squared(samples, triangles):
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    squared = np.full(len(samples), np.inf)
    for start, end in ((a, b), (b, c), (c, a)):
        edge = end-start
        denom = np.einsum("ij,ij->i", edge, edge)
        fraction = np.divide(np.einsum("ij,ij->i", samples-start, edge), denom,
                             out=np.zeros(len(samples)), where=denom > 0)
        closest = start+np.clip(fraction, 0, 1)[:, None]*edge
        delta = samples-closest
        squared = np.minimum(squared, np.einsum("ij,ij->i", delta, delta))
    normal = np.cross(b-a, c-a)
    norm_squared = np.einsum("ij,ij->i", normal, normal)
    signed = np.einsum("ij,ij->i", samples-a, normal)
    fraction = np.divide(signed, norm_squared, out=np.zeros(len(samples)),
                         where=norm_squared > 0)
    projection = samples-fraction[:, None]*normal
    u = np.divide(np.einsum("ij,ij->i", np.cross(projection-a, c-a), normal),
                  norm_squared, out=np.full(len(samples), -1.), where=norm_squared > 0)
    v = np.divide(np.einsum("ij,ij->i", np.cross(b-a, projection-a), normal),
                  norm_squared, out=np.full(len(samples), -1.), where=norm_squared > 0)
    inside = (u >= 0) & (v >= 0) & (u+v <= 1) & (norm_squared > 0)
    plane_squared = np.divide(signed*signed, norm_squared,
                              out=np.full(len(samples), np.inf), where=inside)
    return np.minimum(squared, plane_squared)


def precise_surface_error(points, faces, other_points, other_faces):
    """BVH selects candidates; float64 arithmetic measures surface distance."""
    points = np.asarray(points, dtype=np.float64)
    samples = np.concatenate((points, points[np.asarray(faces)].mean(axis=1)))
    other_points = np.asarray(other_points, dtype=np.float64)
    other_faces = np.asarray(other_faces, dtype=np.int64)
    tree = BVHTree.FromPolygons(other_points.tolist(), other_faces.tolist(), all_triangles=True)
    indices = []
    for point in samples:
        nearest = tree.find_nearest(Vector(point))
        if nearest[2] is None:
            raise ValueError("Cleanup nearest-triangle query failed")
        indices.append(nearest[2])
    squared = point_triangle_squared(samples, other_points[other_faces[indices]])
    # Float32 BVH nearest selection can choose the wrong skinny triangle even
    # for a self-comparison. Re-evaluate nearby candidates for unresolved samples.
    expanded = np.flatnonzero(squared > 1e-24)
    for index in expanded:
        nearby = tree.find_nearest_range(Vector(samples[index]),
                                        float(np.sqrt(squared[index]))+1e-4)
        candidates = [entry[2] for entry in nearby if entry[2] is not None]
        if candidates:
            triangles = other_points[other_faces[candidates]]
            repeated = np.broadcast_to(samples[index], (len(candidates), 3))
            squared[index] = min(squared[index], float(
                point_triangle_squared(repeated, triangles).min()))
    # Degenerate float32 faces may be omitted by the BVH altogether. Use
    # float64 triangle bounds for every unresolved sample; a box lower bound
    # cannot exclude a triangle closer than the measured candidate distance.
    all_triangles = other_points[other_faces]
    low, high = all_triangles.min(axis=1), all_triangles.max(axis=1)
    bounded = np.flatnonzero(squared > 1e-24)
    for index in bounded:
        delta = np.maximum(np.maximum(low-samples[index], samples[index]-high), 0.)
        lower = np.einsum("ij,ij->i", delta, delta)
        candidates = np.flatnonzero(lower <= squared[index]+1e-24)
        repeated = np.broadcast_to(samples[index], (len(candidates), 3))
        squared[index] = min(squared[index], float(
            point_triangle_squared(repeated, all_triangles[candidates]).min()))
    return {"maximum_distance_m": float(np.sqrt(squared.max())),
            "vertex_samples": len(points), "face_center_samples": len(faces),
            "expanded_candidate_samples": len(expanded),
            "float64_bound_samples": len(bounded),
            "method": "Float64 triangle distances with conservative triangle-box fallback"}


def remove_boolean_degeneracies(obj):
    """Remove float-precision Boolean degeneracies; never fill a surface hole."""
    old_points, old_faces = geometry(obj)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        before = {"vertices": len(bm.verts), "edges": len(bm.edges), "faces": len(bm.faces)}
        tolerance = 1e-7
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=tolerance)
        bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=tolerance)
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
        after = {"vertices": len(bm.verts), "edges": len(bm.edges), "faces": len(bm.faces)}
        bm.to_mesh(obj.data)
        obj.data.update()
        points, faces = geometry(obj)
        forward = precise_surface_error(old_points, old_faces, points, faces)
        reverse = precise_surface_error(points, faces, old_points, old_faces)
        error = max(forward["maximum_distance_m"], reverse["maximum_distance_m"])
        if error > 1e-6:
            baseline = precise_surface_error(old_points, old_faces, old_points, old_faces)
            raise ValueError("Numerical cleanup surface-distance guard failed: "
                             + json.dumps({"forward": forward, "reverse": reverse,
                                           "input_self_distance": baseline,
                                           "before": before, "after": after}))
        return {"tolerance_m": tolerance, "before": before, "after": after,
                "maximum_sampled_surface_change_m": error,
                "all_vertices_and_face_centers_sampled": True}
    finally:
        bm.free()


def volume(obj):
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        if not bm.faces or any(not edge.is_manifold for edge in bm.edges):
            defects = {
                "faces": len(bm.faces),
                "wire_edges": sum(len(edge.link_faces) == 0 for edge in bm.edges),
                "boundary_edges": sum(len(edge.link_faces) == 1 for edge in bm.edges),
                "multiple_face_edges": sum(len(edge.link_faces) > 2 for edge in bm.edges),
                "zero_area_faces": sum(face.calc_area() <= 1e-18 for face in bm.faces),
            }
            raise ValueError("Boolean result is not a closed two-manifold: "
                             + json.dumps(defects, sort_keys=True))
        value = bm.calc_volume(signed=True)
        if value <= 1e-15:
            raise ValueError("Boolean result has no positive oriented volume")
        return value
    finally:
        bm.free()


def surface_error(vertices, faces, other_tree, cap=20000):
    points = [Vector(v) for v in vertices]
    centers = [sum((points[i] for i in f), Vector())/3 for f in triangulate(faces)]
    maximum = 0.
    counts = []
    for pool in (points, centers):
        selected = pool[::max(1, (len(pool)+cap-1)//cap)]
        counts.append(len(selected))
        for point in selected:
            result = other_tree.find_nearest(point)
            if result[0] is None:
                raise ValueError("Nearest-surface audit failed")
            maximum = max(maximum, float(result[3]))
    return {"maximum_distance_m": maximum, "vertex_samples": counts[0],
            "face_center_samples": counts[1]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs=4, type=Path, required=True)
    parser.add_argument("--outputs", nargs=2, type=Path, required=True)
    parser.add_argument("--boolean-solver", choices=("EXACT", "MANIFOLD"), default="MANIFOLD")
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    if any(path.exists() for path in args.outputs):
        raise FileExistsError("Use fresh repair output paths")
    source, authored, recipe, config = map(load, args.inputs)
    for name, path in zip(("source_geometry", "geometry", None, "config"), args.inputs):
        if name and recipe["inputs_sha256"][name] != sha(path):
            raise ValueError("Repair recipe input changed: "+name)
    if (authored["candidate_sha256"] != recipe["candidate_sha256"]
            or source["source_sha256"] != recipe["source_sha256"]
            or source["source_sha256"] != config["source_sha256"]):
        raise ValueError("Repair candidate/source mismatch")
    limits = config["clearance"]
    if (recipe["maximum_collision_volume_removed_per_part"] !=
            limits["maximum_collision_volume_removed_per_part"]
            or recipe["maximum_iterations_per_pair"] != limits["maximum_iterations_per_pair"]
            or recipe["target_clearance_m"] != limits["target_surface_clearance_m"]):
        raise ValueError("Recipe changed declared clearance/shape/iteration limits")
    inherited = authored.get("repair_iteration_ledger")
    if inherited is not None and inherited != recipe["repair_iteration_ledger_before"]:
        raise ValueError("Recipe reset the inherited repair iteration ledger")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    sources = {m["node"]: m for m in source["meshes"]}
    selection_policy = recipe.get("edit_selection_policy", {
        "preferred_source_nodes": [],
        "tie_breaker": "Minimum incremental source-volume removal",
        "shape_gates_may_not_be_bypassed": True,
    })
    preferred_nodes = selection_policy["preferred_source_nodes"]
    if (not isinstance(preferred_nodes, list)
            or any(type(node) is not int or node not in sources for node in preferred_nodes)
            or selection_policy["shape_gates_may_not_be_bypassed"] is not True
            or selection_policy["tie_breaker"] != "Minimum incremental source-volume removal"):
        raise ValueError("Invalid edit selection policy")
    result = copy.deepcopy(authored)
    parts = {m["path"]: m for m in result["meshes"]}
    objects, source_local, source_trees = {}, {}, {}
    for path, baseline in recipe["baseline"].items():
        if not baseline["volume_qualified"] or baseline["protected"]:
            continue
        part = parts[path]
        obj = object_from(path.rsplit("/", 1)[-1], part["vertices"], part["faces"])
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.to_mesh(obj.data)
        bm.free()
        triangulate_object(obj)
        # Reject inconsistent recipe volumes before applying any operation.
        measured = volume(obj)
        if abs(measured-baseline["current_volume_m3"]) > max(1e-12, measured*1e-5):
            raise ValueError("Authored volume differs from qualified recipe baseline")
        objects[path] = obj
        inverse = Matrix(part["mesh_world_matrix"]).inverted()
        points = [inverse @ Vector(v) for v in sources[part["node"]]["vertices"]]
        faces = sources[part["node"]]["faces"]
        source_local[path] = (points, faces)
        source_trees[path] = BVHTree.FromPolygons(points, triangulate(faces), all_triangles=True)
    report = {
        "schema_version": 1, "status": "running",
        "base_candidate_sha256": authored["candidate_sha256"],
        "source_sha256": source["source_sha256"],
        "input_sha256": [sha(p) for p in args.inputs], "script_sha256": sha(Path(__file__)),
        "boolean_solver": args.boolean_solver,
        "edit_selection_policy": selection_policy,
        "operations": [], "changed_colliders": [],
        "all_operations_resolved_by_source_boolean": False,
        "continuous_shape_distance_qualified": False,
        "recooked": False, "clearance_qualified": False, "physics_accepted": False,
        "repair_iteration_ledger_before": recipe["repair_iteration_ledger_before"],
    }
    iterations = Counter({tuple(row["pair"]): row["attempts"]
                          for row in recipe["repair_iteration_ledger_before"]["pair_attempts"]})
    changed = set()
    for index, operation in enumerate(recipe["operations"]):
        pair = tuple(operation["pair"])
        row = {"index": index, "pose": operation["pose"], "pair": list(pair), "alternatives": []}
        if iterations[pair] >= recipe["maximum_iterations_per_pair"]:
            row["status"] = "pair_iteration_budget_exhausted"
            report["operations"].append(row)
            continue
        iterations[pair] += 1
        accepted = []
        for alternative in operation["alternatives"]:
            path = alternative["collider"]
            obj = objects[path]
            candidate = obj.copy()
            candidate.data = obj.data.copy()
            bpy.context.collection.objects.link(candidate)
            trial = {"collider": path, "node": alternative["node"]}
            try:
                before = volume(obj)
                original = recipe["baseline"][path]["source_volume_m3"]
                cutter_specs = alternative.get("cutters", [alternative])
                trial.update(cutter_count=len(cutter_specs), applied_cutter_count=0)
                bpy.context.view_layer.objects.active = candidate
                previous_volume = before
                for start in range(0, len(cutter_specs), 128):
                    cutters, cutter_collection = [], None
                    try:
                        for spec in cutter_specs[start:start+128]:
                            cutter = object_from("local_clearance_cutter",
                                                 spec["cutter_vertices_body"], spec["cutter_faces"])
                            cutters.append(cutter)
                            volume(cutter)
                        modifier = candidate.modifiers.new("bounded_local_clearance", "BOOLEAN")
                        modifier.operation, modifier.solver = "DIFFERENCE", args.boolean_solver
                        if len(cutters) == 1:
                            modifier.object = cutters[0]
                        else:
                            cutter_collection = bpy.data.collections.new("clearance_cutter_collection")
                            for cutter in cutters:
                                cutter_collection.objects.link(cutter)
                            modifier.operand_type = "COLLECTION"
                            modifier.collection = cutter_collection
                        bpy.ops.object.modifier_apply(modifier=modifier.name)
                        triangulate_object(candidate)
                        trial.setdefault("numerical_topology_cleanup", []).append(
                            remove_boolean_degeneracies(candidate))
                        after_batch = volume(candidate)
                        trial.update(applied_cutter_count=start+len(cutters),
                                     volume_after_m3=after_batch,
                                     cumulative_removed_fraction=1-after_batch/original)
                        if after_batch > previous_volume+max(1e-12, previous_volume*1e-5):
                            raise ValueError("Difference batch unexpectedly added solid volume")
                        if 1-after_batch/original > recipe["maximum_collision_volume_removed_per_part"]:
                            raise ValueError("Cumulative immutable-source volume cap exceeded")
                        previous_volume = after_batch
                        print(json.dumps({"operation": index, "node": alternative["node"],
                                          "applied_cutters": trial["applied_cutter_count"],
                                          "total_cutters": len(cutter_specs)}), flush=True)
                    finally:
                        for cutter in cutters:
                            delete_object(cutter)
                        if cutter_collection is not None:
                            bpy.data.collections.remove(cutter_collection)
                after = volume(candidate)
                removed = 1-after/original
                trial.update(volume_before_m3=before, volume_after_m3=after,
                             cumulative_removed_fraction=removed)
                if after > before+max(1e-12, before*1e-5) or removed < -1e-5:
                    raise ValueError("Difference unexpectedly added solid volume")
                if removed > recipe["maximum_collision_volume_removed_per_part"]:
                    raise ValueError("Cumulative immutable-source volume cap exceeded")
                if before-after <= max(1e-13, before*1e-8):
                    raise ValueError("No material removed; cooked approximation may be responsible")
                points, faces = geometry(candidate)
                tree = BVHTree.FromPolygons([Vector(p) for p in points], triangulate(faces),
                                           all_triangles=True)
                original_points, original_faces = source_local[path]
                forward = surface_error(original_points, original_faces, tree)
                reverse = surface_error(points, faces, source_trees[path])
                trial.update(source_to_collider=forward, collider_to_source=reverse)
                distance_limit = recipe["maximum_sampled_source_distance_m"]
                if distance_limit is not None and max(
                        forward["maximum_distance_m"], reverse["maximum_distance_m"]) > distance_limit:
                    raise ValueError("Cumulative sampled source-distance cap exceeded")
                trial["status"] = "shape_gates_passed"
                accepted.append(((before-after)/original, path, candidate, trial))
            except Exception as error:
                trial.update(status="rejected", reason=str(error))
                delete_object(candidate)
            row["alternatives"].append(trial)
        if accepted:
            accepted.sort(key=lambda x: (x[3]["node"] not in preferred_nodes, x[0], x[1]))
            _, path, candidate, trial = accepted[0]
            delete_object(objects[path])
            objects[path] = candidate
            changed.add(path)
            row.update(status="local_boolean_applied_requires_recook", selected_collider=path)
            for _, _, unused, _ in accepted[1:]:
                delete_object(unused)
        else:
            row["status"] = "no_shape_qualified_alternative"
        report["operations"].append(row)
        args.outputs[1].write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
        print(json.dumps({"operation": index, "status": row["status"]}), flush=True)
    for path in sorted(changed):
        points, faces = geometry(objects[path])
        parts[path]["vertices"], parts[path]["faces"] = points, faces
    result["base_candidate_sha256"] = result.pop("candidate_sha256")
    result["kind"] = "unmaterialized_collider_repair_geometry"
    result["repair_recipe_sha256"] = sha(args.inputs[2])
    result["changed_colliders"] = sorted(changed)
    result["repair_iteration_ledger"] = {
        "baseline_candidate_sha256": recipe["repair_iteration_ledger_before"]["baseline_candidate_sha256"],
        "pair_attempts": [{"pair": list(pair), "attempts": count}
                          for pair, count in sorted(iterations.items())],
    }
    args.outputs[0].write_bytes(gzip.compress(json.dumps(result, allow_nan=False).encode(), mtime=0))
    report.update(status="completed", changed_colliders=sorted(changed),
                  repair_iteration_ledger_after=result["repair_iteration_ledger"],
                  output_geometry_sha256=sha(args.outputs[0]),
                  all_operations_resolved_by_source_boolean=all(
                      row["status"] == "local_boolean_applied_requires_recook"
                      for row in report["operations"]))
    args.outputs[1].write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")


if __name__ == "__main__":
    main()
