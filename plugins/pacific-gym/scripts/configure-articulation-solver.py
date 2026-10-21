"""Author articulation solver counts in a pinned USD with an exact change audit."""

import argparse
import hashlib
import json
from pathlib import Path

from pxr import Sdf, Usd, UsdPhysics


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("usd", "out", "receipt"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--usd-sha256", required=True)
    parser.add_argument("--position-iterations", type=int, required=True)
    parser.add_argument("--velocity-iterations", type=int, required=True)
    args = parser.parse_args()
    if args.out.exists() or args.receipt.exists() or sha(args.usd) != args.usd_sha256:
        raise ValueError("Require pinned input and fresh outputs")
    if not 1 <= args.position_iterations <= 255 or not 0 <= args.velocity_iterations <= 255:
        raise ValueError("Solver counts exceed the PhysX schema ranges")
    stage = Usd.Stage.Open(Usd.Stage.Open(str(args.usd)).Flatten())
    baseline = stage.GetRootLayer().ExportToString()
    roots = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
    if len(roots) != 1 or "PhysxArticulationAPI" not in roots[0].GetAppliedSchemas():
        raise ValueError("Expected one articulation with existing PhysX API")
    prim = roots[0]
    edits = []
    for name, value in (
            ("physxArticulation:solverPositionIterationCount", args.position_iterations),
            ("physxArticulation:solverVelocityIterationCount", args.velocity_iterations)):
        attr = prim.GetAttribute(name)
        spec = stage.GetRootLayer().GetAttributeAtPath(prim.GetPath().AppendProperty(name))
        # Existing metadata or time samples require a richer restoration audit.
        if spec and (attr.GetNumTimeSamples() or set(spec.ListInfoKeys()) -
                     {"typeName", "variability", "custom", "default"}):
            raise ValueError("Unsupported existing solver attribute metadata")
        edits.append({"path": str(prim.GetPath()), "attribute": name,
                      "had_property_spec": bool(spec),
                      "had_authored_default": bool(attr and attr.HasAuthoredValueOpinion()),
                      "before": attr.Get() if attr else None, "after": value})
        prim.CreateAttribute(name, Sdf.ValueTypeNames.Int, custom=False).Set(value)
    stage.GetRootLayer().Export(str(args.out))
    readback = Usd.Stage.Open(str(args.out))
    for edit in edits:
        target = readback.GetPrimAtPath(edit["path"])
        attr = target.GetAttribute(edit["attribute"])
        if attr.Get() != edit["after"]:
            raise ValueError("Serialized solver count mismatch")
        if not edit["had_property_spec"]:
            target.RemoveProperty(edit["attribute"])
        elif edit["had_authored_default"]:
            attr.Set(edit["before"])
        else:
            attr.Clear()
    if readback.GetRootLayer().ExportToString() != baseline or sha(args.usd) != args.usd_sha256:
        raise ValueError("Unexpected USD change")
    receipt = {
        "status": "solver_counts_changed_requires_native_validation",
        "base_candidate_sha256": args.usd_sha256,
        "candidate_sha256": sha(args.out), "script_sha256": sha(Path(__file__)),
        "edits": edits, "all_other_usd_content_preserved": True,
        "geometry_mass_drives_limits_and_collision_filters_preserved": True,
        "physics_accepted": False,
    }
    args.receipt.write_text(json.dumps(receipt, indent=2)+"\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
