"""Read a USD without starting Kit; export the facts needed for offline audits.

Run with an OpenUSD Python interpreter. This does not qualify physics.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdGeom, UsdPhysics


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def plain(value):
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite": str(value)}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if hasattr(value, "GetReal"):
        return [float(value.GetReal()), *map(float, value.GetImaginary())]
    try:
        return [plain(v) for v in value]
    except TypeError:
        return str(value)


def export(source):
    stage = Usd.Stage.Open(str(source))
    if not stage:
        raise ValueError("Cannot open USD")
    cache = UsdGeom.XformCache()
    result = {
        "schema_version": 1, "usd_path": str(source),
        "usd_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
        "meters_per_unit": UsdGeom.GetStageMetersPerUnit(stage),
        "layers": [], "bodies": [], "joints": [], "meshes": [],
        "physics_accepted": False,
    }
    for layer in stage.GetUsedLayers():
        path = Path(layer.realPath) if layer.realPath else None
        result["layers"].append({
            "identifier": layer.identifier,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
            if path and path.is_file() else None,
        })
    for prim in stage.Traverse():
        path = str(prim.GetPath())
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            mass = UsdPhysics.MassAPI(prim)
            result["bodies"].append({
                "path": path,
                "world_matrix": np.asarray(cache.GetLocalToWorldTransform(prim)).T.tolist(),
                "mass_kg": plain(mass.GetMassAttr().Get()),
                "center_of_mass": plain(mass.GetCenterOfMassAttr().Get()),
                "diagonal_inertia": plain(mass.GetDiagonalInertiaAttr().Get()),
                "principal_axes": plain(mass.GetPrincipalAxesAttr().Get()),
                "kinematic": plain(UsdPhysics.RigidBodyAPI(prim).GetKinematicEnabledAttr().Get()),
                "self_collision": plain(prim.GetAttribute(
                    "physxArticulation:enabledSelfCollisions").Get()),
            })
        if prim.IsA(UsdPhysics.Joint):
            joint = UsdPhysics.Joint(prim)
            row = {"path": path, "name": prim.GetName(),
                   "body0": [str(p) for p in joint.GetBody0Rel().GetTargets()],
                   "body1": [str(p) for p in joint.GetBody1Rel().GetTargets()],
                   "local_pos0": plain(joint.GetLocalPos0Attr().Get()),
                   "local_pos1": plain(joint.GetLocalPos1Attr().Get()),
                   "local_rot0_wxyz": plain(joint.GetLocalRot0Attr().Get()),
                   "local_rot1_wxyz": plain(joint.GetLocalRot1Attr().Get()),
                   "enabled": plain(joint.GetJointEnabledAttr().Get()),
                   "collision_enabled": plain(joint.GetCollisionEnabledAttr().Get())}
            if prim.IsA(UsdPhysics.RevoluteJoint):
                revolute = UsdPhysics.RevoluteJoint(prim)
                row.update(axis=str(revolute.GetAxisAttr().Get()),
                           limits_deg=[revolute.GetLowerLimitAttr().Get(),
                                       revolute.GetUpperLimitAttr().Get()])
            result["joints"].append(row)
        if prim.IsA(UsdGeom.Mesh):
            mesh = UsdGeom.Mesh(prim)
            points = np.asarray(mesh.GetPointsAttr().Get(), dtype=float)
            matrix = np.asarray(cache.GetLocalToWorldTransform(prim)).T
            world = points @ matrix[:3, :3].T + matrix[:3, 3]
            counts = list(map(int, mesh.GetFaceVertexCountsAttr().Get()))
            indices = list(map(int, mesh.GetFaceVertexIndicesAttr().Get()))
            geometry = {"points": points.tolist(), "counts": counts, "indices": indices}
            # Include composed collision/material properties on all ancestors.
            # A geometry-only hash cannot authorize reuse of cooked hulls.
            ancestors = []
            current = prim
            while current and current.GetPath() != "/":
                ancestors.append({
                    "path": str(current.GetPath()),
                    "schemas": list(current.GetAppliedSchemas()),
                    "attributes": {
                        attr.GetName(): plain(attr.Get())
                        for attr in current.GetAttributes()
                        if attr.GetName().startswith(("physics:", "physx", "xformOp"))
                    },
                    "relationships": {
                        rel.GetName(): [str(p) for p in rel.GetTargets()]
                        for rel in current.GetRelationships()
                        if rel.GetName().startswith(("physics:", "material:"))
                    },
                })
                current = current.GetParent()
            result["meshes"].append({
                "path": path, "vertices": len(points),
                "triangles": sum(n - 2 for n in counts),
                "world_matrix": matrix.tolist(),
                "world_bounds": [world.min(0).tolist(), world.max(0).tolist()],
                "geometry_sha256": digest(geometry),
                "world_geometry_sha256": digest({**geometry, "points": world.tolist()}),
                "collision": prim.HasAPI(UsdPhysics.CollisionAPI),
                "collision_state_sha256": digest({"geometry": geometry,
                                                 "ancestors": ancestors}),
                "collision_ancestors": ancestors if prim.HasAPI(UsdPhysics.CollisionAPI) else [],
            })
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usd", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    result = export(args.usd.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"snapshot": str(args.out), "usd_sha256": result["usd_sha256"],
                      "bodies": len(result["bodies"]), "joints": len(result["joints"]),
                      "meshes": len(result["meshes"])}))
