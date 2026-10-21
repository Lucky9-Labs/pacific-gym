"""Make the 64-vertex PhysX hull setting explicit in a new ASCII USD candidate.

Only matching authored scalar values change; every other byte is preserved.
Fresh cooking and shape/clearance validation are still required.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("usd", "out", "receipt"):
        parser.add_argument("--"+name, required=True, type=Path)
    parser.add_argument("--usd-sha256", required=True)
    args = parser.parse_args()
    if args.out.exists() or args.receipt.exists():
        raise FileExistsError("Use fresh candidate and receipt paths")
    original = args.usd.read_bytes()
    if hashlib.sha256(original).hexdigest() != args.usd_sha256:
        raise ValueError("Candidate hash mismatch")
    if not original.startswith(b"#usda 1.0"):
        raise ValueError("Expected ASCII USD")
    pattern = rb"(int physxConvexDecompositionCollision:hullVertexLimit = )(\d+)"
    changes = []

    def replace(match):
        old = int(match[2])
        if not 8 <= old:
            raise ValueError("Unexpected hull vertex count below eight")
        if old > 64:
            changes.append({"byte_offset": match.start(2), "before": old, "after": 64})
            return match[1] + b"64"
        return match[0]

    output = re.sub(pattern, replace, original)
    if not changes:
        raise ValueError("No settings need normalization")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(output)
    if args.usd.read_bytes() != original:
        raise ValueError("Input changed during normalization")
    result = {
        "schema_version": 1, "baseline_sha256": args.usd_sha256,
        "candidate_sha256": hashlib.sha256(output).hexdigest(),
        "changes": changes, "all_other_bytes_preserved": True,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "recooked": False, "physics_accepted": False,
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
