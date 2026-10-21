"""Author an explicit source-part ownership hypothesis at unchanged rest placement.

Moves both visual and collider meshes. Transfers a uniform-density estimate of
the part's mass and recomputes the two affected inertias from closed source solids.
Existing joint frames and collision filters are preserved; no physics acceptance.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt

from pacific_gym.solid_volume import closed_components, mesh_volume


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def moments(mesh, world_to_body):
    audit = mesh_volume(mesh["vertices"], mesh["faces"])
    if not audit["volume_qualified"]:
        raise ValueError("Source part volume is not qualified")
    volume, first, second = 0., np.zeros(3), np.zeros((3, 3))
    components = closed_components(mesh["vertices"], mesh["faces"])
    for row, component in zip(audit["components"], components):
        if row["duplicate_of"] is not None:
            continue
        points = component["points"] @ world_to_body[:3, :3].T+world_to_body[:3, 3]
        a, b, c = (points[component["outward_faces"][:, i]] for i in range(3))
        weights = np.einsum("ij,ij->i", a, np.cross(b, c))/6
        sums = a+b+c
        volume += weights.sum()
        first += (weights[:, None]*sums).sum(0)/4
        for vectors in (sums, a, b, c):
            second += np.einsum("i,ij,ik->jk", weights/20, vectors, vectors)
    if volume <= 0 or abs(volume-audit["volume_m3"]) > max(1e-12, volume*1e-6):
        raise ValueError("Source moment integral disagrees with qualified volume")
    return volume, first, second


def signature(stage, moved, containers, affected):
    mass_attrs = {"physics:mass", "physics:centerOfMass",
                  "physics:diagonalInertia", "physics:principalAxes"}
    rows = []
    for prim in stage.Traverse():
        path = str(prim.GetPath())
        if path in moved or path in containers:
            continue
        attrs = {a.GetName(): str(a.Get()) for a in prim.GetAttributes()
                 if not (path in affected and a.GetName() in mass_attrs)}
        rows.append([path, str(prim.GetTypeName()), list(prim.GetAppliedSchemas()), attrs,
                     {r.GetName(): list(map(str, r.GetTargets())) for r in prim.GetRelationships()}])
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("usd", "source-geometry", "config", "out", "receipt"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--usd-sha256", required=True)
    parser.add_argument("--source-node", type=int, required=True)
    parser.add_argument("--from-body", required=True)
    parser.add_argument("--to-body", required=True)
    args = parser.parse_args()
    if args.out.exists() or args.receipt.exists() or sha(args.usd) != args.usd_sha256:
        raise ValueError("Require pinned input and fresh outputs")
    input_hashes = {name: sha(getattr(args, name)) for name in ("usd", "source_geometry", "config")}
    source, config = [json.loads(p.read_text()) for p in (args.source_geometry, args.config)]
    if source["source_sha256"] != config["source_sha256"]:
        raise ValueError("Source/config identity mismatch")
    source_parts = {m["node"]: m for m in source["meshes"]}
    part = source_parts[args.source_node]
    if part["owner"] != args.from_body.rsplit("/", 1)[-1] or part.get("protected_contact_geometry"):
        raise ValueError("Source owner mismatch or protected contact part")
    stage = Usd.Stage.Open(Usd.Stage.Open(str(args.usd)).Flatten())
    affected = {args.from_body, args.to_body}
    cache = UsdGeom.XformCache()
    matrices, before_mass = {}, {}
    for path in sorted(affected):
        prim = stage.GetPrimAtPath(path)
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            raise ValueError("Owner is not an existing rigid body")
        matrices[path] = np.asarray(cache.GetLocalToWorldTransform(prim)).T
        before_mass[path] = float(UsdPhysics.MassAPI(prim).GetMassAttr().Get())
    moving = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)
              and p.GetName().startswith(f"node_{args.source_node}_")
              and str(p.GetPath()).startswith(args.from_body+"/")]
    if len(moving) != 2 or sum(p.HasAPI(UsdPhysics.CollisionAPI) for p in moving) != 1:
        raise ValueError("Expected exactly one visual and one collider for this source part")
    paths = {str(p.GetPath()): args.to_body+str(p.GetPath())[len(args.from_body):] for p in moving}
    containers = {path.rsplit("/", 1)[0] for path in paths.values()}
    for path in sorted(containers):
        prim = stage.GetPrimAtPath(path)
        if prim and (not prim.IsA(UsdGeom.Xform) or UsdGeom.Xformable(prim).GetOrderedXformOps()):
            raise ValueError("Destination container must be an identity Xform")
    before_signature = signature(stage, set(paths)|set(paths.values()), containers, affected)
    expected_world, mesh_properties = {}, {}
    for prim in moving:
        old = str(prim.GetPath())
        mesh = UsdGeom.Mesh(prim)
        world = np.asarray(cache.GetLocalToWorldTransform(prim)).T
        points = np.asarray(mesh.GetPointsAttr().Get(), float)
        expected_world[paths[old]] = points @ world[:3, :3].T+world[:3, 3]
        # Reassignment is restricted to equal orientation/scale, so normals stay valid.
        if not np.allclose(world[:3, :3], matrices[args.to_body][:3, :3], atol=1e-10, rtol=0):
            raise ValueError("Rotated/scaled mesh ownership requires a normal-transform adapter")
        mesh_properties[paths[old]] = [
            {a.GetName(): str(a.Get()) for a in prim.GetAttributes()
             if a.GetName() not in ("points", "extent", "xformOpOrder")},
            list(prim.GetAppliedSchemas()),
            {r.GetName(): list(map(str, r.GetTargets())) for r in prim.GetRelationships()},
        ]
    for old, new in paths.items():
        UsdGeom.Xform.Define(stage, new.rsplit("/", 1)[0])
        if stage.GetPrimAtPath(new):
            raise ValueError("Destination mesh already exists")
        if not Sdf.CopySpec(stage.GetRootLayer(), old, stage.GetRootLayer(), new):
            raise ValueError("Mesh copy failed")
        stage.RemovePrim(old)
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath(new))
        inverse = np.linalg.inv(matrices[args.to_body])
        points = expected_world[new] @ inverse[:3, :3].T+inverse[:3, 3]
        mesh.GetPointsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*map(float, p)) for p in points]))
        UsdGeom.Xformable(mesh).ClearXformOpOrder()
        mesh.GetExtentAttr().Set(UsdGeom.PointBased(mesh).ComputeExtent(mesh.GetPointsAttr().Get()))
    old_owner = args.from_body.rsplit("/", 1)[-1]
    source_group = [m for m in source["meshes"]
                    if m["category"] == "robot" and m["owner"] == old_owner and m["source_manifold"]]
    old_volume = sum(moments(m, np.linalg.inv(matrices[args.from_body]))[0] for m in source_group)
    part_volume = moments(part, np.linalg.inv(matrices[args.from_body]))[0]
    transferred = before_mass[args.from_body]*part_volume/old_volume
    after_mass = {args.from_body: before_mass[args.from_body]-transferred,
                  args.to_body: before_mass[args.to_body]+transferred}
    if min(after_mass.values()) < config["mass_model"]["minimum_link_mass_kg"]:
        raise ValueError("Mass transfer would violate the minimum body mass")
    mass_details = {}
    for path in sorted(affected):
        name = path.rsplit("/", 1)[-1]
        group = [m for m in source["meshes"] if m["category"] == "robot" and m["source_manifold"]
                 and (args.to_body.rsplit("/", 1)[-1] if m["node"] == args.source_node else m["owner"]) == name]
        integrals = [moments(m, np.linalg.inv(matrices[path])) for m in group]
        volume = sum(x[0] for x in integrals)
        first, second = sum((x[1] for x in integrals), np.zeros(3)), sum((x[2] for x in integrals), np.zeros((3, 3)))
        center = first/volume
        covariance = second/volume-np.outer(center, center)
        inertia = after_mass[path]*(np.trace(covariance)*np.eye(3)-covariance)
        eigenvalues, axes = np.linalg.eigh(inertia)
        if not np.isfinite(eigenvalues).all() or min(eigenvalues) <= 0:
            raise ValueError("Source mass model produced invalid principal inertia")
        if np.linalg.det(axes) < 0:
            axes[:, 0] *= -1
        quat = Rotation.from_matrix(axes).as_quat()
        if quat[3] < 0:
            quat *= -1
        api = UsdPhysics.MassAPI(stage.GetPrimAtPath(path))
        api.GetMassAttr().Set(after_mass[path])
        api.GetCenterOfMassAttr().Set(Gf.Vec3f(*map(float, center)))
        api.GetDiagonalInertiaAttr().Set(Gf.Vec3f(*map(float, eigenvalues)))
        api.GetPrincipalAxesAttr().Set(Gf.Quatf(float(quat[3]), Gf.Vec3f(*map(float, quat[:3]))))
        mass_details[path] = {"before_kg": before_mass[path], "after_kg": after_mass[path],
                              "source_volume_m3": volume, "center": center.tolist(),
                              "principal_inertia": eigenvalues.tolist()}
    metadata = dict(stage.GetRootLayer().customLayerData)
    if metadata.get("pacificGymRepairIterationLedger"):
        ledger = json.loads(metadata["pacificGymRepairIterationLedger"])
        for row in ledger["pair_attempts"]:
            row["pair"] = sorted(paths.get(p, p) for p in row["pair"])
        metadata["pacificGymRepairIterationLedger"] = json.dumps(ledger, sort_keys=True)
    metadata["pacificGymOwnershipHypothesis"] = json.dumps({
        "source_node": args.source_node, "from_body": args.from_body, "to_body": args.to_body,
        "source_sha256": source["source_sha256"], "base_candidate_sha256": args.usd_sha256,
    }, sort_keys=True)
    stage.GetRootLayer().customLayerData = metadata
    args.out.parent.mkdir(parents=True, exist_ok=True)
    stage.GetRootLayer().Export(str(args.out))
    readback = Usd.Stage.Open(str(args.out))
    if signature(readback, set(paths)|set(paths.values()), containers, affected) != before_signature:
        raise ValueError("Ownership authoring changed an undeclared property, joint or filter")
    cache, errors = UsdGeom.XformCache(), []
    for path, expected in expected_world.items():
        prim = readback.GetPrimAtPath(path)
        actual_properties = [
            {a.GetName(): str(a.Get()) for a in prim.GetAttributes()
             if a.GetName() not in ("points", "extent", "xformOpOrder")},
            list(prim.GetAppliedSchemas()),
            {r.GetName(): list(map(str, r.GetTargets())) for r in prim.GetRelationships()},
        ]
        if actual_properties != mesh_properties[path]:
            raise ValueError("Moved mesh changed topology, normals or another preserved property")
        world = np.asarray(cache.GetLocalToWorldTransform(prim)).T
        points = np.asarray(UsdGeom.Mesh(prim).GetPointsAttr().Get(), float)
        errors.append(float(np.linalg.norm(points @ world[:3, :3].T+world[:3, 3]-expected, axis=1).max()))
    if max(errors) > config["max_world_rest_translation_error_m"]:
        raise ValueError("Moved mesh changed world rest placement")
    total = sum(float(UsdPhysics.MassAPI(p).GetMassAttr().Get()) for p in readback.Traverse()
                if p.HasAPI(UsdPhysics.RigidBodyAPI))
    if abs(total-config["mass_model"]["total_mass_kg"]) > 1e-4:
        raise ValueError("Total mass changed")
    if (readback.GetRootLayer().customLayerData != stage.GetRootLayer().customLayerData
            or any(sha(getattr(args, key)) != value for key, value in input_hashes.items())):
        raise ValueError("Ownership metadata or an input changed during authoring")
    receipt = {"status": "ownership_hypothesis_requires_fresh_cook",
               "candidate_sha256": sha(args.out), "base_candidate_sha256": args.usd_sha256,
               "source_geometry_sha256": sha(args.source_geometry), "config_sha256": sha(args.config),
               "input_sha256": input_hashes,
               "script_sha256": sha(Path(__file__)), "moved_paths": paths,
               "max_world_rest_vertex_error_m": max(errors), "transferred_mass_kg": transferred,
               "mass_details": mass_details, "total_mass_kg": total,
               "mass_model_is_estimate": True, "cross_part_volume_union_measured": False,
               "joint_frames_and_existing_filters_preserved": True,
               "clearance_qualified": False, "physics_accepted": False}
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, allow_nan=False)+"\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
