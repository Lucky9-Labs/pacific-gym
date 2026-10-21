"""Measure posed closed-solid overlap through blender-derive; no asset mutation."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector


def make_object(name, mesh, transform):
    matrix = Matrix(transform)
    data = bpy.data.meshes.new(name)
    data.from_pydata([matrix @ Vector(v) for v in mesh["vertices"]], [], mesh["faces"])
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new()
    try:
        bm.from_mesh(data)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
        bm.to_mesh(data)
    finally:
        bm.free()
    return obj


def volume(obj, empty_allowed=False):
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        if not bm.faces and empty_allowed:
            return 0.
        if not bm.faces or any(not e.is_manifold for e in bm.edges):
            raise ValueError("Intersection/input is not a closed two-manifold")
        result = bm.calc_volume(signed=True)
        if result <= 0:
            raise ValueError("Intersection/input has no positive volume")
        return result
    finally:
        bm.free()


def remove(obj):
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs=1, type=Path, required=True)
    parser.add_argument("--outputs", nargs=1, type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    if args.outputs[0].exists():
        raise FileExistsError(args.outputs[0])
    spec = json.loads(args.inputs[0].read_text())
    solver = spec.get("boolean_solver", "MANIFOLD")
    if solver not in ("MANIFOLD", "EXACT"):
        raise ValueError("Unsupported Boolean solver")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    report = {"status": "running", "kind": "closed_solid_overlap_diagnostic",
              "input_sha256": hashlib.sha256(args.inputs[0].read_bytes()).hexdigest(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "source_sha256": spec["source_sha256"], "candidate_sha256": spec["candidate_sha256"],
              "boolean_solver": solver,
              "cases": [], "physics_accepted": False}
    for case in spec["cases"]:
        for kind, meshes in spec["mesh_sets"].items():
            a, b = [make_object(kind+"_"+str(m["node"]), m, case["transforms"][m["body"]])
                    for m in meshes]
            row = {"pose": case["label"], "geometry": kind}
            try:
                va, vb = volume(a), volume(b)
                bpy.context.view_layer.objects.active = a
                mod = a.modifiers.new("measure_overlap", "BOOLEAN")
                mod.operation, mod.solver, mod.object = "INTERSECT", solver, b
                bpy.ops.object.modifier_apply(modifier=mod.name)
                overlap = volume(a, empty_allowed=True)
                source_total = sum(m["source_volume_m3"] for m in meshes)
                removed = source_total-va-vb
                allowance = spec["maximum_removed_fraction"]*source_total-max(0., removed)
                row.update(status="measured", volume_a_m3=va, volume_b_m3=vb,
                           intersection_volume_m3=overlap,
                           remaining_combined_removal_allowance_m3=allowance,
                           overlap_exceeds_combined_allowance=overlap > allowance+1e-9)
            except Exception as error:
                row.update(status="unqualified", reason=str(error))
            finally:
                remove(a)
                remove(b)
            report["cases"].append(row)
            args.outputs[0].write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
            print(json.dumps(row), flush=True)
    report["status"] = "completed"
    args.outputs[0].write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")


if __name__ == "__main__":
    main()
