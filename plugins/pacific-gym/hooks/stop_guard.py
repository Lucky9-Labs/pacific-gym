#!/usr/bin/env python3
"""Prevent a Pacific Gym run from being called complete without GPU walking evidence."""

import json
import os
import sys
from pathlib import Path


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        workspace = Path(event.get("cwd") or os.getcwd()).resolve()
        root = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
        sys.path.insert(0, str(root))
        from pacific_gym.run import for_workspace, verified_isaac_check

        active = for_workspace(workspace)
        if not active:
            return 0
        manifest, data = active
        if data.get("state") == "blocked":
            blocker = data.get("blocker") or {}
            if (isinstance(blocker, dict)
                    and isinstance(blocker.get("reason"), str) and len(blocker["reason"]) >= 20
                    and isinstance(blocker.get("evidence"), str) and len(blocker["evidence"]) >= 20):
                return 0
        verified = verified_isaac_check(data)
        if data.get("state") == "complete" and verified:
            return 0
        if data.get("state") not in {"active", "blocked", "complete"}:
            data["state"] = "active"
        acceptance = data.get("acceptance", {})
        missing = []
        if acceptance.get("usd_articulation_valid") is not True:
            missing.append("validated USD articulation")
        if acceptance.get("stable_forward_walking_observed_on_gpu") is not True:
            missing.append("repeated stable forward walking observed on a supported GPU host")
        if not verified:
            missing.append("hash-verified Isaac Sim receipt, staged USD, and proof video")
        if not missing:
            return 0
        reason = (
            f"Pacific Gym run {data['run_id']} is still active. Do not claim completion: "
            + " and ".join(missing)
            + f" are missing. Continue the run or record a specific evidence-backed blocker using {manifest}. "
              "A static render or local USD inspection is not walking proof."
        )
        # Stop's block decision asks Codex to continue with this remediation prompt.
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    except Exception as exc:
        print(json.dumps({"systemMessage": f"Pacific Gym Stop guard could not verify run state: {type(exc).__name__}: {exc}. Do not claim acceptance without checking the run manifest."}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
