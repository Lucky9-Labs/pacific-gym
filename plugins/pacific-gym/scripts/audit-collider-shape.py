"""Measure authored collider displacement against immutable source surfaces.

Run through blender-derive with source geometry, exported collider geometry,
and configuration as inputs, and one new JSON audit as output. Distances are
sampled surface distances, not continuous Hausdorff bounds or volume proofs.
"""

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import tessellate_polygon


def load(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def measure(points, faces, other, maximum_samples):
    # Include vertices and face centers; both directions are measured.
    centers = []
    for face in faces:
        polygon = [points[i] for i in face]
        triangles = [polygon] if len(polygon) == 3 else tessellate_polygon([polygon])
        centers.extend(sum(triangle, Vector())/3 for triangle in triangles)
    pools = (points, centers)
    maximum, counts, witness = -1., [], None
    for pool in pools:
        stride = max(1, (len(pool)+maximum_samples-1)//maximum_samples)
        selected = pool[::stride]
        counts.append(len(selected))
        for point in selected:
            nearest = other.find_nearest(point)
            if nearest[0] is None:
                raise ValueError("Nearest-surface query failed")
            distance = float(nearest[3])
            if distance > maximum:
                maximum = distance
                witness = {"sample": list(point), "nearest": list(nearest[0])}
    return {"maximum_distance_m": maximum,
            "vertex_samples": counts[0], "face_center_samples": counts[1],
            "maximum_distance_witness": witness}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs=3, required=True, type=Path)
    parser.add_argument("--outputs", nargs=1, required=True, type=Path)
    parser.add_argument("--maximum-samples-per-pool", type=int, default=20000)
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    output = args.outputs[0]
    if output.exists():
        raise FileExistsError(output)
    source, geometry, config = map(load, args.inputs)
    if source["source_sha256"] != config["source_sha256"]:
        raise ValueError("Immutable source hash differs from configuration")
    if args.maximum_samples_per_pool < 1:
        raise ValueError("Sample count must be positive")
    originals = {m["node"]: m for m in source["meshes"]}
    limit = config["max_sampled_source_to_derivative_distance_m"]
    report = {
        "schema_version": 1, "candidate_sha256": geometry["candidate_sha256"],
        "source_sha256": source["source_sha256"], "status": "running",
        "sampled_source_to_derivative_limit_m": limit,
        "limit_application": "Conservative application of configured source shape distance to colliders",
        "continuous_distance_qualified": False, "volume_qualified": False,
        "physics_accepted": False, "rows": [],
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "input_sha256": [hashlib.sha256(p.read_bytes()).hexdigest() for p in args.inputs],
    }
    for collider in geometry["meshes"]:
        original = originals[collider["node"]]
        before = [Vector(p) for p in original["vertices"]]
        transform = Matrix(collider["mesh_world_matrix"])
        after = [transform @ Vector(p) for p in collider["vertices"]]
        source_tree = BVHTree.FromPolygons(before, original["faces"], all_triangles=False)
        collider_tree = BVHTree.FromPolygons(after, collider["faces"], all_triangles=False)
        forward = measure(before, original["faces"], collider_tree, args.maximum_samples_per_pool)
        reverse = measure(after, collider["faces"], source_tree, args.maximum_samples_per_pool)
        row = {"node": collider["node"], "path": collider["path"],
               "source_to_collider": forward, "collider_to_source": reverse,
               "sampled_forward_distance_pass": forward["maximum_distance_m"] <= limit,
               "protected_contact_geometry": original.get("protected_contact_geometry", False)}
        report["rows"].append(row)
        print(json.dumps({"node": row["node"],
                          "source_to_collider_m": forward["maximum_distance_m"],
                          "collider_to_source_m": reverse["maximum_distance_m"]}), flush=True)
        output.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    report["status"] = "completed"
    report["all_sampled_forward_distances_pass"] = all(
        row["sampled_forward_distance_pass"] for row in report["rows"])
    output.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")


if __name__ == "__main__":
    main()
