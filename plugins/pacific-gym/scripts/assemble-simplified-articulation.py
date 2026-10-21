"""Combine a pinned rigid GLB derivative with an existing physics candidate.

Changes only visual mesh geometry. Static collision visuals (e.g. terrain)
retain their original geometry. No joint, collider, mass or material edits.
Requires NumPy, OpenUSD and the pacific_gym package on PYTHONPATH.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdGeom, UsdPhysics, Vt

from pacific_gym.rig_geometry import read_rigid_glb


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preserved_signature(stage, allowed):
    rows = []
    for prim in stage.Traverse():
        attrs = {}
        for attr in prim.GetAttributes():
            if str(prim.GetPath()) in allowed and attr.GetName() in {
                    "points", "faceVertexCounts", "faceVertexIndices", "extent", "normals"}:
                continue
            attrs[attr.GetName()] = str(attr.Get())
            if attr.GetNumTimeSamples():
                raise ValueError("Time-varying USD requires an explicit animation policy")
        rows.append([str(prim.GetPath()), str(prim.GetTypeName()),
                     list(prim.GetAppliedSchemas()), attrs,
                     {rel.GetName(): list(map(str, rel.GetTargets())) for rel in prim.GetRelationships()}])
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("usd", "source", "visual-glb", "config", "mapping", "out", "receipt"):
        parser.add_argument("--"+name, required=True, type=Path)
    parser.add_argument("--visual-sha256", required=True)
    parser.add_argument("--usd-sha256", required=True)
    args = parser.parse_args()
    if args.out.exists() or args.receipt.exists():
        raise FileExistsError("Candidate and receipt must use fresh paths")
    before_hashes = {name: sha(getattr(args, name)) for name in (
        "usd", "source", "visual_glb", "config", "mapping")}
    config = json.loads(args.config.read_text())
    mapping = json.loads(args.mapping.read_text())
    if before_hashes["source"] != config["source_sha256"] or mapping["source_sha256"] != config["source_sha256"]:
        raise ValueError("Source/config/mapping mismatch")
    if before_hashes["usd"] != args.usd_sha256 or before_hashes["visual_glb"] != args.visual_sha256:
        raise ValueError("Pinned candidate or visual derivative hash mismatch")
    source, source_frames, source_meshes = read_rigid_glb(args.source)
    derivative, derivative_frames, meshes = read_rigid_glb(args.visual_glb)
    if len(source["nodes"]) != len(derivative["nodes"]):
        raise ValueError("Derivative node count changed")
    for a, b in zip(source["nodes"], derivative["nodes"]):
        if a.get("name") != b.get("name") or a.get("children", []) != b.get("children", []):
            raise ValueError("Derivative node identity or parent hierarchy changed")
    if source.get("scenes") != derivative.get("scenes") or source.get("scene", 0) != derivative.get("scene", 0):
        raise ValueError("Derivative scene membership changed")
    if [s["joints"] for s in source.get("skins", [])] != [s["joints"] for s in derivative.get("skins", [])]:
        raise ValueError("Derivative joint identity changed")
    max_rest_error = max(float(np.max(abs(source_frames[i]-derivative_frames[i]))) for i in source_frames)
    translation_errors, rotation_errors, scale_errors = [], [], []
    for index, a in source_frames.items():
        b = derivative_frames[index]
        translation_errors.append(float(np.linalg.norm(a[:3, 3]-b[:3, 3])))
        rotations = []
        for matrix in (a, b):
            u, singular, vh = np.linalg.svd(matrix[:3, :3])
            if min(singular) < 1e-10:
                raise ValueError("Singular source or derivative rest transform")
            rotations.append(u @ vh)
        relative = rotations[0].T @ rotations[1]
        if np.linalg.det(relative) < 0:
            raise ValueError("Derivative changed rest-transform handedness")
        rotation_errors.append(float(np.arccos(np.clip((np.trace(relative)-1)/2, -1, 1))))
        scale_errors.append(float(np.max(abs(np.linalg.norm(a[:3, :3], axis=0) -
                                               np.linalg.norm(b[:3, :3], axis=0)))))
    if (max(translation_errors) > config["max_world_rest_translation_error_m"]
            or max(rotation_errors) > config["max_world_rest_rotation_error_rad"]
            or max(scale_errors) > config["max_world_rest_scale_error"]):
        raise ValueError("Derivative rest translation, rotation or scale exceeds declared tolerance")
    if len(meshes) != config["source_mesh_nodes"]:
        raise ValueError("Derivative mesh count changed")
    original = Usd.Stage.Open(str(args.usd))
    if not original or len([layer for layer in original.GetUsedLayers()
                            if layer != original.GetSessionLayer()]) != 1:
        raise ValueError("Expected one self-contained USD layer")
    stage = Usd.Stage.Open(original.Flatten())
    if UsdGeom.GetStageUpAxis(stage) != "Z" or UsdGeom.GetStageMetersPerUnit(stage) != 1:
        raise ValueError("Expected a meter-scale Z-up articulation")
    source_by_node = {m["node"]: m for m in mapping["meshes"]}
    allowed, rows, planned = set(), [], []
    cache = UsdGeom.XformCache()
    for part in meshes:
        src = source_by_node[part["node"]]
        if src["name"] != part["name"]:
            raise ValueError("Source mesh mapping changed")
        basename = "node_" + str(part["node"]) + "_" + re.sub("[^A-Za-z0-9_]", "_", part["name"])
        prefix = "/World/Robot/"+src["owner"] if src["category"] == "robot" else "/World/Environment/"+src["category"]
        path = prefix+"/visual/"+basename
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsA(UsdGeom.Mesh):
            raise ValueError("Missing source visual: "+path)
        old = UsdGeom.Mesh(prim)
        before_triangles = sum(int(n)-2 for n in old.GetFaceVertexCountsAttr().Get())
        physical = prim.HasAPI(UsdPhysics.CollisionAPI)
        row = {"node": part["node"], "path": path, "before_triangles": before_triangles,
               "after_triangles": before_triangles if physical else len(part["faces"]),
               "preserved_static_collision_visual": physical}
        rows.append(row)
        if physical:
            continue
        matrix = np.array(cache.GetLocalToWorldTransform(prim)).T
        inverse = np.linalg.inv(matrix)
        points = part["vertices"] @ inverse[:3, :3].T + inverse[:3, 3]
        planned.append((old, points, part["faces"]))
        allowed.add(path)
    total = sum(row["after_triangles"] for row in rows)
    cap = config["max_total_triangles"]*(1+config["max_total_triangles_rounding_tolerance_fraction"])
    if total > cap:
        raise ValueError("Assembled visual triangle budget exceeded")
    signature = preserved_signature(stage, allowed)
    for mesh, points, faces in planned:
        mesh.GetPointsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*p) for p in points]))
        mesh.GetFaceVertexCountsAttr().Set([3]*len(faces))
        mesh.GetFaceVertexIndicesAttr().Set(faces.ravel().tolist())
        mesh.GetNormalsAttr().Clear()
        mesh.GetExtentAttr().Set(UsdGeom.PointBased(mesh).ComputeExtent(mesh.GetPointsAttr().Get()))
    if preserved_signature(stage, allowed) != signature:
        raise ValueError("Assembly changed a preserved property")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    stage.GetRootLayer().Export(str(args.out))
    readback = Usd.Stage.Open(str(args.out))
    if preserved_signature(readback, allowed) != signature:
        raise ValueError("USD serialization changed a preserved property")
    readback_cache = UsdGeom.XformCache()
    geometry_errors = []
    for mesh, points, faces in planned:
        restored = UsdGeom.Mesh(readback.GetPrimAtPath(mesh.GetPath()))
        got = np.asarray(restored.GetPointsAttr().Get(), float)
        counts = list(restored.GetFaceVertexCountsAttr().Get())
        indices = np.asarray(restored.GetFaceVertexIndicesAttr().Get(), int)
        if counts != [3]*len(faces) or not np.array_equal(indices, faces.ravel()):
            raise ValueError("USD serialization changed visual topology")
        matrix = np.asarray(readback_cache.GetLocalToWorldTransform(restored.GetPrim())).T
        error = float(np.linalg.norm((got-points) @ matrix[:3, :3].T, axis=1).max())
        if error > config["max_world_rest_translation_error_m"]:
            raise ValueError("Visual point serialization exceeds spatial tolerance")
        geometry_errors.append(error)
    for name, expected in before_hashes.items():
        if sha(getattr(args, name)) != expected:
            raise ValueError("Input changed during assembly: "+name)
    receipt = {
        "schema_version": 1, "status": "assembled_for_validation",
        "candidate_sha256": sha(args.out), "inputs_sha256": before_hashes,
        "script_sha256": sha(Path(__file__)),
        "geometry_decoder_sha256": sha(Path(__import__("pacific_gym.rig_geometry", fromlist=["x"]).__file__)),
        "visual_meshes": len(rows), "visual_triangles": total,
        "max_source_derivative_rest_matrix_error": max_rest_error,
        "max_source_derivative_rest_translation_error_m": max(translation_errors),
        "max_source_derivative_rest_rotation_error_rad": max(rotation_errors),
        "max_source_derivative_rest_scale_error": max(scale_errors),
        "preserved_physics_and_nonvisual_properties_sha256": signature,
        "all_preserved_properties_match_after_serialization": True,
        "max_visual_world_vertex_serialization_error_m": max(geometry_errors, default=0),
        "visual_shape_error_remeasured": False,
        "physics_accepted": False, "meshes": rows,
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k != "meshes"}))


if __name__ == "__main__":
    main()
