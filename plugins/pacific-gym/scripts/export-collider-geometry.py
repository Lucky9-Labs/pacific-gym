"""Export exact authored collider points/topology for bounded offline repair."""

import argparse
import gzip
import hashlib
import json
import re
from pathlib import Path

import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usd", required=True, type=Path)
    parser.add_argument("--usd-sha256", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    actual = hashlib.sha256(args.usd.read_bytes()).hexdigest()
    if actual != args.usd_sha256:
        raise ValueError("USD hash mismatch")
    stage = Usd.Stage.Open(str(args.usd))
    cache = UsdGeom.XformCache()
    parts = []
    for prim in stage.Traverse():
        if not prim.HasAPI(UsdPhysics.CollisionAPI) or not str(prim.GetPath()).startswith("/World/Robot/"):
            continue
        if not prim.IsA(UsdGeom.Mesh):
            raise ValueError("Non-mesh collider needs an explicit repair adapter")
        body = prim.GetParent()
        while body and not body.HasAPI(UsdPhysics.RigidBodyAPI):
            body = body.GetParent()
        if not body:
            raise ValueError("Collider owner is missing")
        match = re.match(r"node_(\d+)_", prim.GetName())
        if not match:
            raise ValueError("Collider source-node identity is missing")
        mesh = UsdGeom.Mesh(prim)
        indices = list(map(int, mesh.GetFaceVertexIndicesAttr().Get()))
        faces, cursor = [], 0
        for count in mesh.GetFaceVertexCountsAttr().Get():
            faces.append(indices[cursor:cursor+count])
            cursor += count
        if cursor != len(indices):
            raise ValueError("Invalid mesh topology")
        parts.append({
            "node": int(match[1]), "path": str(prim.GetPath()), "body": str(body.GetPath()),
            "vertices": [list(map(float, point)) for point in mesh.GetPointsAttr().Get()],
            "faces": faces,
            "mesh_world_matrix": np.asarray(cache.GetLocalToWorldTransform(prim)).T.tolist(),
            "body_world_matrix": np.asarray(cache.GetLocalToWorldTransform(body)).T.tolist(),
        })
    result = {"schema_version": 1, "candidate_sha256": actual, "meshes": parts,
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    ledger = stage.GetRootLayer().customLayerData.get("pacificGymRepairIterationLedger")
    if ledger:
        result["repair_iteration_ledger"] = json.loads(ledger)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(gzip.compress(json.dumps(result, allow_nan=False).encode(), mtime=0))
    print(json.dumps({"candidate_sha256": actual, "meshes": len(parts),
                      "output_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
