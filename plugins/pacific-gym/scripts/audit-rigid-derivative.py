"""Blender read-only BVH audit of a source GLB and simplified rigid GLB.

Called through blender-derive with --inputs SOURCE DERIVATIVE --outputs AUDIT.
It does not import assets into a scene or mutate either input.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import bmesh
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def volume(points, faces):
    mesh = bmesh.new()
    try:
        verts = [mesh.verts.new(point) for point in points]
        for face in faces:
            mesh.faces.new([verts[int(index)] for index in face])
        closed = all(edge.is_manifold for edge in mesh.edges)
        bmesh.ops.recalc_face_normals(mesh, faces=list(mesh.faces))
        return {"closed": closed, "consistently_oriented_component_sum_m3": mesh.calc_volume(signed=False)}
    finally:
        mesh.free()


def errors(points, other_points, other_faces, budget):
    tree = BVHTree.FromPolygons(other_points.tolist(), other_faces.tolist(), all_triangles=True)
    # Explicit deterministic sample indices and sample count; no continuous-surface claim.
    indices = np.unique(np.linspace(0, len(points)-1, min(budget, len(points)), dtype=int))
    distances = []
    for index in indices:
        result = tree.find_nearest(Vector(points[index]))
        if result is None:
            raise ValueError("BVH returned no surface")
        distances.append(result[3])
    return {"samples": len(indices), "maximum_m": max(distances),
            "rms_m": float(np.sqrt(np.mean(np.square(distances))))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs=2, type=Path, required=True)
    parser.add_argument("--outputs", nargs=1, type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--samples-per-part", type=int, default=4096)
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    if args.outputs[0].exists() or args.samples_per_part < 1:
        raise ValueError("Require a fresh output path and positive sample budget")
    sys.path.insert(0, str(args.package_root))
    from pacific_gym.rig_geometry import read_rigid_glb
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in [*args.inputs, args.config]}
    config = json.loads(args.config.read_text())
    if hashes[str(args.inputs[0])] != config["source_sha256"]:
        raise ValueError("Source hash differs from declared configuration")
    _, _, source = read_rigid_glb(args.inputs[0])
    _, _, derived = read_rigid_glb(args.inputs[1])
    candidate = {part["node"]: part for part in derived}
    if set(candidate) != {part["node"] for part in source}:
        raise ValueError("Source part identities changed")
    rows = []
    for part in source:
        other = candidate[part["node"]]
        if other["name"] != part["name"]:
            raise ValueError("Source part name changed")
        a, b = (volume(p["vertices"], p["faces"]) for p in (part, other))
        forward = errors(part["vertices"], other["vertices"], other["faces"], args.samples_per_part)
        reverse = errors(other["vertices"], part["vertices"], part["faces"], args.samples_per_part)
        initial = a["consistently_oriented_component_sum_m3"]
        change = abs(b["consistently_oriented_component_sum_m3"]-initial)/initial if a["closed"] and initial > 1e-12 else None
        passes = forward["maximum_m"] <= config["max_sampled_source_to_derivative_distance_m"]
        passes = passes and (change is None or (b["closed"] and change <= config["max_closed_part_relative_volume_change"]))
        rows.append({
            "node": part["node"], "name": part["name"], "source_to_derivative": forward,
            "derivative_to_source": reverse, "source_volume": a, "derivative_volume": b,
            "closed_part_relative_volume_change": change, "passes": passes,
        })
    for path in [*args.inputs, args.config]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != hashes[str(path)]:
            raise ValueError("An immutable audit input changed")
    result = {
        "schema_version": 1, "inputs_sha256": hashes,
        "source_sha256": hashes[str(args.inputs[0])],
        "derivative_sha256": hashes[str(args.inputs[1])],
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "all_declared_sampled_shape_checks_pass": all(row["passes"] for row in rows),
        "maximum_sampled_source_to_derivative_m": max(row["source_to_derivative"]["maximum_m"] for row in rows),
        "maximum_sampled_derivative_to_source_m": max(row["derivative_to_source"]["maximum_m"] for row in rows),
        "maximum_closed_part_relative_volume_change": max((row["closed_part_relative_volume_change"] or 0) for row in rows),
        "volume_method": "Reorient each closed component and sum; not an occupied-volume union estimate.",
        "sampling_note": "Vertex samples only; does not bound the distance of every surface point.",
        "physics_accepted": False, "parts": rows,
    }
    args.outputs[0].parent.mkdir(parents=True, exist_ok=True)
    args.outputs[0].write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps({k: v for k, v in result.items() if k != "parts"}))


if __name__ == "__main__":
    main()
