"""Create a separate Strokah hypothesis covering the declared joint families.

Adds one empty hip-yaw link per leg at the existing hip-pitch frame; uses the
stage's vertical axis and configured limits. Original visual/collider geometry
and all original joint world rest frames are preserved. This is an inferred
physics model requiring new clearance and runtime validation.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from pacific_gym.solid_volume import allocate_masses


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mesh_signature(stage):
    cache = UsdGeom.XformCache()
    rows = []
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            rows.append([str(prim.GetPath()), list(prim.GetAppliedSchemas()),
                         {attr.GetName(): str(attr.Get()) for attr in prim.GetAttributes()},
                         {rel.GetName(): list(map(str, rel.GetTargets())) for rel in prim.GetRelationships()},
                         np.array(cache.GetLocalToWorldTransform(prim)).tolist()])
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def joint_world_frames(stage):
    cache = UsdGeom.XformCache()
    rows = {}
    for prim in stage.Traverse():
        if not prim.IsA(UsdPhysics.Joint):
            continue
        joint = UsdPhysics.Joint(prim)
        frames = []
        for index in (0, 1):
            body = stage.GetPrimAtPath(getattr(joint, "GetBody"+str(index)+"Rel")().GetTargets()[0])
            position = getattr(joint, "GetLocalPos"+str(index)+"Attr")().Get()
            quaternion = getattr(joint, "GetLocalRot"+str(index)+"Attr")().Get()
            local = Gf.Matrix4d().SetRotate(Gf.Quatd(quaternion))
            local.SetTranslateOnly(Gf.Vec3d(position))
            frames.append(np.array(local * cache.GetLocalToWorldTransform(body)))
        rows[prim.GetName()] = frames
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("usd", "config", "volume-audit", "out", "receipt"):
        parser.add_argument("--"+name, required=True, type=Path)
    parser.add_argument("--usd-sha256", required=True)
    args = parser.parse_args()
    if args.out.exists() or args.receipt.exists():
        raise FileExistsError("Use new candidate and receipt paths")
    if digest(args.usd) != args.usd_sha256:
        raise ValueError("Baseline USD hash mismatch")
    config = json.loads(args.config.read_text())
    volume_audit = json.loads(args.volume_audit.read_text())
    if (volume_audit["source_sha256"] != config["source_sha256"]
            or volume_audit["unresolved_nodes"]):
        raise ValueError("Source volume evidence is unresolved or mismatched")
    original = Usd.Stage.Open(str(args.usd))
    if len([layer for layer in original.GetUsedLayers() if layer != original.GetSessionLayer()]) != 1:
        raise ValueError("Expected a self-contained USD")
    stage = Usd.Stage.Open(original.Flatten())
    if UsdGeom.GetStageUpAxis(stage) != "Z" or UsdGeom.GetStageMetersPerUnit(stage) != 1:
        raise ValueError("Expected meter-scale Z-up model")
    signature, old_frames = mesh_signature(stage), joint_world_frames(stage)
    ranges = config["clearance"]["joint_sweep_degrees"]
    existing = [prim for prim in stage.Traverse() if prim.IsA(UsdPhysics.RevoluteJoint)]
    expected = {family+"_"+side for side in ("L", "R")
                for family in ("hip_roll", "hip_pitch", "knee", "ankle_pitch", "foot_roll")}
    if {p.GetName() for p in existing} != expected:
        raise ValueError("Expected the original ten-DOF Strokah model")
    changes, excluded_pairs = [], []
    for prim in existing:
        joint = UsdPhysics.RevoluteJoint(prim)
        family = prim.GetName().rsplit("_", 1)[0]
        family = {"foot_roll": "ankle_roll"}.get(family, family)
        changes.append({"joint": prim.GetName(),
                        "before_deg": [joint.GetLowerLimitAttr().Get(), joint.GetUpperLimitAttr().Get()],
                        "after_deg": ranges[family]})
        joint.GetLowerLimitAttr().Set(ranges[family][0])
        joint.GetUpperLimitAttr().Set(ranges[family][1])
    cache = UsdGeom.XformCache()
    for side in ("L", "R"):
        pitch = UsdPhysics.RevoluteJoint(stage.GetPrimAtPath("/World/Robot/Joints/hip_pitch_"+side))
        hip_path, thigh_path = pitch.GetBody0Rel().GetTargets()[0], pitch.GetBody1Rel().GetTargets()[0]
        local = Gf.Matrix4d().SetRotate(Gf.Quatd(pitch.GetLocalRot0Attr().Get()))
        local.SetTranslateOnly(Gf.Vec3d(pitch.GetLocalPos0Attr().Get()))
        world = local * cache.GetLocalToWorldTransform(stage.GetPrimAtPath(hip_path))
        # The existing joint frame must have its Z axis aligned with stage up.
        if abs(float(world.TransformDir(Gf.Vec3d(0, 0, 1))[2])) < .999999:
            raise ValueError("Hip frame is not aligned with the declared yaw-axis inference")
        yaw_path = "/World/Robot/hipYaw"+side
        body = UsdGeom.Xform.Define(stage, yaw_path)
        body.AddTransformOp().Set(world)
        UsdPhysics.RigidBodyAPI.Apply(body.GetPrim()).CreateKinematicEnabledAttr(False)
        body.GetPrim().AddAppliedSchema("PhysxRigidBodyAPI")
        yaw = UsdPhysics.RevoluteJoint.Define(stage, "/World/Robot/Joints/hip_yaw_"+side)
        yaw.CreateBody0Rel().SetTargets([hip_path])
        yaw.CreateBody1Rel().SetTargets([body.GetPath()])
        yaw.CreateLocalPos0Attr(pitch.GetLocalPos0Attr().Get())
        yaw.CreateLocalRot0Attr(pitch.GetLocalRot0Attr().Get())
        yaw.CreateLocalPos1Attr(Gf.Vec3f(0))
        yaw.CreateLocalRot1Attr(Gf.Quatf(1))
        yaw.CreateAxisAttr("Z")
        yaw.CreateLowerLimitAttr(ranges["hip_yaw"][0])
        yaw.CreateUpperLimitAttr(ranges["hip_yaw"][1])
        yaw.CreateCollisionEnabledAttr(False)
        drive = UsdPhysics.DriveAPI.Apply(yaw.GetPrim(), "angular")
        drive.CreateTypeAttr("force")
        drive.CreateStiffnessAttr(1800)
        drive.CreateDampingAttr(180)
        drive.CreateMaxForceAttr(300)
        drive.CreateTargetPositionAttr(0)
        # Preserve the baseline hip/thigh mechanical-neighbor exclusion through
        # the empty yaw body. This is recorded explicitly, not a global filter.
        if not pitch.GetCollisionEnabledAttr().Get():
            UsdPhysics.FilteredPairsAPI.Apply(stage.GetPrimAtPath(hip_path)).CreateFilteredPairsRel().AddTarget(thigh_path)
            excluded_pairs.append([str(hip_path), str(thigh_path)])
        pitch.GetBody0Rel().SetTargets([body.GetPath()])
        pitch.GetLocalPos0Attr().Set(Gf.Vec3f(0))
        pitch.GetLocalRot0Attr().Set(Gf.Quatf(1))
    volumes = dict(volume_audit["per_link_closed_visual_volume_m3"])
    volumes.update(hipYawL=0., hipYawR=0.)
    masses = allocate_masses(volumes, config["mass_model"]["total_mass_kg"],
                             config["mass_model"]["minimum_link_mass_kg"])
    for name, mass in masses.items():
        prim = stage.GetPrimAtPath("/World/Robot/"+name)
        api = UsdPhysics.MassAPI.Apply(prim)
        previous = api.GetMassAttr().Get()
        if name.startswith("hipYaw"):
            # Conservative explicit virtual-link model, not measured source inertia.
            inertia = np.repeat(.4*mass*.015**2, 3)
            api.CreateCenterOfMassAttr(Gf.Vec3f(0))
            api.CreatePrincipalAxesAttr(Gf.Quatf(1))
        else:
            if not previous or previous <= 0:
                raise ValueError("Baseline mass is not positive")
            inertia = np.array(api.GetDiagonalInertiaAttr().Get())*mass/previous
        api.CreateMassAttr(mass)
        api.CreateDiagonalInertiaAttr(Gf.Vec3f(*(float(value) for value in inertia)))
    if mesh_signature(stage) != signature:
        raise ValueError("Range experiment changed source visual or collider geometry")
    after_frames = joint_world_frames(stage)
    frame_error = max(float(np.max(abs(a-b))) for name, values in old_frames.items()
                      for a, b in zip(values, after_frames[name]))
    if frame_error > config["max_world_rest_translation_error_m"]:
        raise ValueError("An original joint world rest frame moved")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    stage.GetRootLayer().Export(str(args.out))
    restored = Usd.Stage.Open(str(args.out))
    if mesh_signature(restored) != signature:
        raise ValueError("Mesh serialization changed preserved geometry")
    actual_mass = sum(UsdPhysics.MassAPI(p).GetMassAttr().Get()
                      for p in restored.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI))
    if abs(actual_mass-config["mass_model"]["total_mass_kg"]) > 1e-4:
        raise ValueError("Mass readback mismatch")
    if digest(args.usd) != args.usd_sha256:
        raise ValueError("Baseline changed during authoring")
    result = {
        "schema_version": 1, "status": "inferred_range_candidate_for_clearance_validation",
        "candidate_sha256": digest(args.out), "baseline_sha256": args.usd_sha256,
        "source_sha256": config["source_sha256"], "config_sha256": digest(args.config),
        "volume_audit_sha256": digest(args.volume_audit), "script_sha256": digest(Path(__file__)),
        "limits": changes, "added_dofs": ["hip_yaw_L", "hip_yaw_R"],
        "yaw_frame_inference": "Existing hip-pitch pivot/frame; local Z aligned to stage vertical.",
        "virtual_link_inertia_model": "0.015 m uniform sphere at minimum link mass.",
        "preserved_original_hip_thigh_exclusions": excluded_pairs,
        "preserved_mesh_and_collision_property_signature": signature,
        "maximum_original_joint_world_frame_matrix_error": frame_error,
        "masses_kg": masses, "readback_total_mass_kg": actual_mass,
        "all_required_clearance_sweeps_passed": False, "physics_accepted": False,
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
