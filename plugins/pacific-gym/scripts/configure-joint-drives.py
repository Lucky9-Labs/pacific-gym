"""Change named joint-drive gains in a pinned fresh USD, preserving other content."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from pxr import Usd, UsdPhysics


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("usd", "out", "receipt"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--usd-sha256", required=True)
    parser.add_argument("--joint", action="append", required=True)
    parser.add_argument("--stiffness", type=float, required=True)
    parser.add_argument("--damping", type=float, required=True)
    args = parser.parse_args()
    if args.out.exists() or args.receipt.exists() or sha(args.usd) != args.usd_sha256:
        raise ValueError("Require pinned input and fresh outputs")
    if (len(set(args.joint)) != len(args.joint)
            or not all(math.isfinite(x) and 0 < x <= 100000
                       for x in (args.stiffness, args.damping))):
        raise ValueError("Require unique joints and finite bounded positive gains")
    stage = Usd.Stage.Open(Usd.Stage.Open(str(args.usd)).Flatten())
    baseline = stage.GetRootLayer().ExportToString()
    edits = []
    for name in args.joint:
        matches = [p for p in stage.Traverse()
                   if p.IsA(UsdPhysics.RevoluteJoint) and p.GetName() == name]
        if len(matches) != 1:
            raise ValueError("Expected exactly one revolute joint: "+name)
        prim = matches[0]
        drive = UsdPhysics.DriveAPI(prim, "angular")
        if not drive or drive.GetTypeAttr().Get() != "force":
            raise ValueError("Expected existing angular force drive")
        for attr, value in ((drive.GetStiffnessAttr(), args.stiffness),
                            (drive.GetDampingAttr(), args.damping)):
            if not attr.HasAuthoredValueOpinion():
                raise ValueError("Expected authored gain")
            before = attr.Get()
            attr.Set(value)
            # USD float attributes round to their storage precision. Audit the
            # exact stored value while retaining the caller's requested value.
            edits.append({"path": str(prim.GetPath()), "attribute": attr.GetName(),
                          "before": before, "requested": value, "after": attr.Get()})
    stage.GetRootLayer().Export(str(args.out))
    readback = Usd.Stage.Open(str(args.out))
    for edit in edits:
        attr = readback.GetPrimAtPath(edit["path"]).GetAttribute(edit["attribute"])
        if attr.Get() != edit["after"]:
            raise ValueError("Serialized gain mismatch")
        attr.Set(edit["before"])
    if readback.GetRootLayer().ExportToString() != baseline:
        raise ValueError("Unexpected USD change")
    if sha(args.usd) != args.usd_sha256:
        raise ValueError("Input changed")
    receipt = {
        "status": "drive_gains_changed_requires_native_validation",
        "base_candidate_sha256": args.usd_sha256,
        "candidate_sha256": sha(args.out),
        "script_sha256": sha(Path(__file__)),
        "edits": edits,
        "all_other_usd_content_preserved": True,
        "torque_caps_and_joint_limits_preserved": True,
        "gain_units": "Angular USD force-drive gains per degree; native tensor gains are per radian",
        "physics_accepted": False,
    }
    args.receipt.write_text(json.dumps(receipt, indent=2)+"\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
