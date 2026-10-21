"""Change explicitly listed decomposition settings without changing geometry."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from pxr import Sdf, Usd, UsdGeom, UsdPhysics


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("usd", "settings", "out", "receipt"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--usd-sha256", required=True)
    args = parser.parse_args()
    if args.out.exists() or args.receipt.exists() or sha(args.usd) != args.usd_sha256:
        raise ValueError("Require pinned input and fresh output paths")
    settings_hash = sha(args.settings)
    settings = json.loads(args.settings.read_text())
    types = {"shrinkWrap": Sdf.ValueTypeNames.Bool,
             "maxConvexHulls": Sdf.ValueTypeNames.Int,
             "voxelResolution": Sdf.ValueTypeNames.Int,
             "errorPercentage": Sdf.ValueTypeNames.Float,
             "minThickness": Sdf.ValueTypeNames.Float}
    stage = Usd.Stage.Open(Usd.Stage.Open(str(args.usd)).Flatten())
    baseline = stage.GetRootLayer().ExportToString()
    changes = []
    for path, attributes in settings.items():
        prim = stage.GetPrimAtPath(path)
        if not prim.IsA(UsdGeom.Mesh) or not prim.HasAPI(UsdPhysics.CollisionAPI):
            raise ValueError("Settings target is not a collision mesh: "+path)
        for suffix, value in attributes.items():
            if suffix not in types:
                raise ValueError("Unsupported cooking setting: "+suffix)
            if suffix == "shrinkWrap":
                if not isinstance(value, bool):
                    raise ValueError("shrinkWrap must be a Boolean")
            elif (not isinstance(value, (int, float)) or isinstance(value, bool)
                  or not math.isfinite(value) or value < 0):
                raise ValueError("Cooking settings must be finite nonnegative numbers")
            elif suffix in ("maxConvexHulls", "voxelResolution") and (
                    not isinstance(value, int) or value == 0):
                raise ValueError("Hull and voxel counts must be positive integers")
            name = "physxConvexDecompositionCollision:"+suffix
            attr = prim.GetAttribute(name)
            before = {"existed": bool(attr), "authored": bool(attr and attr.HasAuthoredValueOpinion()),
                      "value": attr.Get() if attr else None}
            if attr and attr.GetTypeName() != types[suffix]:
                raise ValueError("Existing cooking attribute has an unexpected type")
            attr = prim.CreateAttribute(name, types[suffix], custom=False)
            attr.Set(value)
            changes.append({"path": path, "attribute": name, "before": before, "after": attr.Get()})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    stage.GetRootLayer().Export(str(args.out))
    readback = Usd.Stage.Open(str(args.out))
    for change in changes:
        prim = readback.GetPrimAtPath(change["path"])
        attr = prim.GetAttribute(change["attribute"])
        if attr.Get() != change["after"]:
            raise ValueError("Serialized cooking setting changed")
        before = change["before"]
        if not before["existed"]:
            prim.RemoveProperty(change["attribute"])
        elif before["authored"]:
            attr.Set(before["value"])
        else:
            attr.Clear()
    if readback.GetRootLayer().ExportToString() != baseline:
        raise ValueError("Unexpected USD change outside explicitly listed cooking settings")
    if sha(args.usd) != args.usd_sha256 or sha(args.settings) != settings_hash:
        raise ValueError("Input changed during materialization")
    receipt = {"status": "settings_changed_requires_fresh_cook",
               "candidate_sha256": sha(args.out), "base_candidate_sha256": args.usd_sha256,
               "settings_sha256": settings_hash, "script_sha256": sha(Path(__file__)),
               "changes": changes, "all_other_usd_content_preserved": True,
               "clearance_qualified": False, "physics_accepted": False}
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, allow_nan=False)+"\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
