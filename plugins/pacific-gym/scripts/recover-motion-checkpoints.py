"""Recover a hash-verified diagnostic prefix after an aborted native run.

The recovered trace is explicitly incomplete and cannot qualify a native run.
Only chunks listed in the failed native receipt are used.
"""

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("receipt", "checkpoint-directory", "out", "summary"):
        parser.add_argument("--"+name, type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists() or args.summary.exists() or args.out == args.summary:
        raise ValueError("Recovery requires fresh, distinct outputs")
    receipt = json.loads(args.receipt.read_text())
    records = receipt.get("trace_checkpoints", [])
    if receipt["status"] != "failed" or not records:
        raise ValueError("A failed native receipt with saved chunks is required")
    directory = args.checkpoint_directory.resolve(strict=True)
    samples, inputs = [], []
    dt = names = None
    for record in records:
        filename = PureWindowsPath(record["path"]).name
        path = (directory/filename).resolve(strict=True)
        if path.parent != directory or sha(path) != record["sha256"]:
            raise ValueError("Checkpoint identity mismatch")
        chunk = json.loads(gzip.decompress(path.read_bytes()))
        if dt is None:
            dt, names = chunk["dt_s"], chunk["dof_names"]
            if not math.isfinite(dt) or dt <= 0:
                raise ValueError("Invalid native timestep")
        rows = chunk["samples"]
        if (not rows or chunk["candidate_sha256"] != receipt["candidate_sha256"]
                or chunk["dt_s"] != dt or chunk["dof_names"] != names
                or chunk["first_observation_index"] != len(samples)
                or record["first_observation_index"] != len(samples)
                or record["observation_count"] != len(rows)
                or record["first_time_s"] != rows[0]["time_s"]
                or record["last_time_s"] != rows[-1]["time_s"]
                or chunk["incomplete_diagnostic_checkpoint"] is not True
                or chunk["root_resets"] != 0
                or chunk["external_support_applied"] is not False):
            raise ValueError("Checkpoint metadata or order mismatch")
        for row in rows:
            expected_time = (len(samples)+2)*dt  # The first step is the bootstrap.
            if not math.isfinite(row["time_s"]) or abs(row["time_s"]-expected_time) > 1e-8:
                raise ValueError("Checkpoint samples are not a continuous native prefix")
            samples.append(row)
        inputs.append({"name": filename, "sha256": record["sha256"]})
    payload = {
        "candidate_sha256": receipt["candidate_sha256"], "dt_s": dt,
        "dof_names": names, "samples": samples, "root_resets": 0,
        "external_support_applied": False, "recovered_incomplete_trace": True,
        "failed_native_receipt_sha256": sha(args.receipt),
        "physics_accepted": False, "walking_accepted": False}
    args.out.write_bytes(gzip.compress(json.dumps(payload, allow_nan=False).encode(), mtime=0))
    summary = {
        "status": "incomplete_diagnostic_prefix_recovered",
        "candidate_sha256": receipt["candidate_sha256"],
        "failed_native_receipt_sha256": sha(args.receipt),
        "checkpoint_inputs": inputs, "sample_output_sha256": sha(args.out),
        "observed_steps": len(samples), "first_time_s": samples[0]["time_s"],
        "last_time_s": samples[-1]["time_s"], "script_sha256": sha(Path(__file__)),
        "physics_accepted": False, "walking_accepted": False,
        "scope": "Saved diagnostic prefix only; omitted tail and failed run remain unqualified"}
    args.summary.write_text(json.dumps(summary, indent=2, allow_nan=False)+"\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
